"""MCP stdio server exposing local AST RAG tools for Windsurf.

This server lets Windsurf call the local AST index directly without manual
copy/paste. It speaks JSON-RPC 2.0 with MCP-style methods over stdio
messages. Primary transport is newline-delimited JSON (current MCP spec),
with legacy Content-Length framing also accepted for compatibility.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

SUPPORTED_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "simlaps-ast-rag"
SERVER_VERSION = "0.1.0"


class JsonRpcError(Exception):
    """JSON-RPC error wrapper."""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


def _log(message: str) -> None:
    """Write server logs to stderr only (never stdout)."""
    sys.stderr.write(f"[mcp-ast-rag] {message}\n")
    sys.stderr.flush()


def _safe_fts_query(raw_query: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_\.]+", raw_query)
    if not tokens:
        return raw_query.strip()
    return " OR ".join(tokens)


def _clip_lines(text: str, max_lines: int) -> str:
    if max_lines <= 0:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    clipped = lines[:max_lines]
    clipped.append("... [truncated]")
    return "\n".join(clipped)


def _anchor(row: sqlite3.Row) -> str:
    start_line = int(row["start_line"] or 1)
    end_line = int(row["end_line"] or start_line)
    return f"@{row['file']}#{start_line}-{end_line}"


def _get_meta(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return str(row[0])


def _search_nodes(connection: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    fts_enabled = _get_meta(connection, "fts_enabled", "0") == "1"

    if fts_enabled:
        fts_query = _safe_fts_query(query)
        if fts_query:
            try:
                rows = connection.execute(
                    """
                    SELECT
                        n.id,
                        n.kind,
                        n.name,
                        n.qualname,
                        n.module,
                        n.file,
                        n.start_line,
                        n.end_line,
                        n.signature,
                        n.docstring,
                        n.source,
                        bm25(fts_nodes) AS score
                    FROM fts_nodes
                    JOIN nodes n ON n.id = fts_nodes.id
                    WHERE fts_nodes MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
                if rows:
                    return rows
            except sqlite3.OperationalError:
                pass

    like_pattern = f"%{query.strip()}%"
    return connection.execute(
        """
        SELECT
            id,
            kind,
            name,
            qualname,
            module,
            file,
            start_line,
            end_line,
            signature,
            docstring,
            source,
            0.0 AS score
        FROM nodes
        WHERE
            name LIKE ? OR
            qualname LIKE ? OR
            signature LIKE ? OR
            docstring LIKE ? OR
            source LIKE ?
        ORDER BY
            CASE
                WHEN qualname LIKE ? THEN 0
                WHEN name LIKE ? THEN 1
                ELSE 2
            END,
            LENGTH(source) DESC
        LIMIT ?
        """,
        (
            like_pattern,
            like_pattern,
            like_pattern,
            like_pattern,
            like_pattern,
            like_pattern,
            like_pattern,
            limit,
        ),
    ).fetchall()


def _collect_neighbors(
    connection: sqlite3.Connection,
    node_id: str,
    *,
    limit: int,
) -> dict[str, list[str]]:
    rows = connection.execute(
        """
        SELECT edge_type, dst
        FROM edges
        WHERE src_id = ?
        ORDER BY edge_type, dst
        LIMIT ?
        """,
        (node_id, limit),
    ).fetchall()

    grouped: dict[str, list[str]] = {"call": [], "import": []}
    for row in rows:
        edge_type = str(row[0])
        dst = str(row[1])
        grouped.setdefault(edge_type, []).append(dst)
    return grouped


def _render_markdown(
    results: list[sqlite3.Row],
    neighbors: dict[str, dict[str, list[str]]],
    *,
    include_source: bool,
    max_source_lines: int,
) -> str:
    if not results:
        return "No matching symbols found."

    blocks: list[str] = []
    for index, row in enumerate(results, start=1):
        node_id = str(row["id"])
        related = neighbors.get(node_id, {"call": [], "import": []})

        blocks.append(f"### {index}. `{row['qualname']}` ({row['kind']})")
        blocks.append(f"- Location: {_anchor(row)}")

        signature = str(row["signature"] or "").strip()
        if signature:
            blocks.append(f"- Signature: `{signature}`")

        docstring = str(row["docstring"] or "").strip()
        if docstring:
            first_line = docstring.splitlines()[0].strip()
            if first_line:
                blocks.append(f"- Doc: {first_line}")

        call_targets = related.get("call", [])[:8]
        import_targets = related.get("import", [])[:8]
        if call_targets:
            blocks.append(f"- Calls: {', '.join(f'`{item}`' for item in call_targets)}")
        if import_targets:
            blocks.append(f"- Imports: {', '.join(f'`{item}`' for item in import_targets)}")

        if include_source:
            source = _clip_lines(str(row["source"] or ""), max_source_lines).strip()
            if source:
                blocks.append("```python")
                blocks.append(source)
                blocks.append("```")

        blocks.append("")

    return "\n".join(blocks).rstrip()


def _render_json(
    results: list[sqlite3.Row],
    neighbors: dict[str, dict[str, list[str]]],
    *,
    include_source: bool,
    max_source_lines: int,
) -> str:
    payload: list[dict[str, Any]] = []
    for row in results:
        node_id = str(row["id"])
        source = str(row["source"] or "")
        if include_source:
            source = _clip_lines(source, max_source_lines)
        else:
            source = ""

        payload.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "name": row["name"],
                "qualname": row["qualname"],
                "module": row["module"],
                "file": row["file"],
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "anchor": _anchor(row),
                "signature": row["signature"],
                "docstring": row["docstring"],
                "source": source,
                "neighbors": neighbors.get(node_id, {"call": [], "import": []}),
            }
        )

    return json.dumps(payload, indent=2, ensure_ascii=True)


class AstRagService:
    """Provides AST RAG operations used by MCP tools."""

    def __init__(self, repo_root: Path, db_path: Path) -> None:
        self.repo_root = repo_root
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise JsonRpcError(
                code=-32001,
                message="AST RAG database not found.",
                data={
                    "db": str(self.db_path),
                    "hint": "Run ast_rag_refresh_index tool or build scripts first.",
                },
            )
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def rag_search(
        self,
        *,
        query: str,
        k: int,
        neighbor_limit: int,
        max_source_lines: int,
        no_source: bool,
        output_json: bool,
    ) -> str:
        with self._connect() as connection:
            results = _search_nodes(connection, query=query, limit=max(k, 1))
            neighbors: dict[str, dict[str, list[str]]] = {}
            for row in results:
                node_id = str(row["id"])
                neighbors[node_id] = _collect_neighbors(
                    connection,
                    node_id,
                    limit=max(neighbor_limit, 1),
                )

        include_source = not no_source
        if output_json:
            return _render_json(
                results,
                neighbors,
                include_source=include_source,
                max_source_lines=max(max_source_lines, 1),
            )
        return _render_markdown(
            results,
            neighbors,
            include_source=include_source,
            max_source_lines=max(max_source_lines, 1),
        )

    def symbol_lookup(
        self,
        *,
        qualname: str,
        max_results: int,
        fuzzy: bool,
        max_source_lines: int,
        no_source: bool,
    ) -> str:
        with self._connect() as connection:
            if fuzzy:
                rows = connection.execute(
                    """
                    SELECT
                        id,
                        kind,
                        name,
                        qualname,
                        module,
                        file,
                        start_line,
                        end_line,
                        signature,
                        docstring,
                        source,
                        0.0 AS score
                    FROM nodes
                    WHERE qualname = ? OR qualname LIKE ? OR name = ? OR name LIKE ?
                    ORDER BY
                        CASE WHEN qualname = ? THEN 0
                             WHEN name = ? THEN 1
                             WHEN qualname LIKE ? THEN 2
                             ELSE 3 END,
                        LENGTH(source) DESC
                    LIMIT ?
                    """,
                    (
                        qualname,
                        f"%{qualname}%",
                        qualname,
                        f"%{qualname}%",
                        qualname,
                        qualname,
                        f"%{qualname}%",
                        max(max_results, 1),
                    ),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT
                        id,
                        kind,
                        name,
                        qualname,
                        module,
                        file,
                        start_line,
                        end_line,
                        signature,
                        docstring,
                        source,
                        0.0 AS score
                    FROM nodes
                    WHERE qualname = ?
                    ORDER BY start_line ASC
                    LIMIT ?
                    """,
                    (qualname, max(max_results, 1)),
                ).fetchall()

            neighbors: dict[str, dict[str, list[str]]] = {}
            for row in rows:
                node_id = str(row["id"])
                neighbors[node_id] = _collect_neighbors(connection, node_id, limit=24)

        return _render_markdown(
            rows,
            neighbors,
            include_source=not no_source,
            max_source_lines=max(max_source_lines, 1),
        )

    def rag_stats(self) -> str:
        with self._connect() as connection:
            node_count = int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
            edge_count = int(connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
            parse_error_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM nodes WHERE COALESCE(parse_error, '') != ''"
                ).fetchone()[0]
            )
            meta_rows = connection.execute("SELECT key, value FROM meta ORDER BY key").fetchall()

        meta = {str(row[0]): str(row[1]) for row in meta_rows}
        payload = {
            "db": str(self.db_path),
            "repo_root": str(self.repo_root),
            "node_count": node_count,
            "edge_count": edge_count,
            "parse_error_count": parse_error_count,
            "meta": meta,
        }
        return json.dumps(payload, indent=2, ensure_ascii=True)

    def refresh_index(
        self,
        *,
        roots: list[str],
        include_tests: bool,
    ) -> str:
        if not roots:
            roots = ["src"]

        root_list = [str(root) for root in roots]
        if include_tests and "tests" not in root_list:
            root_list.append("tests")

        out_path = ".windsurf/rag/ast_map.jsonl"
        manifest_path = ".windsurf/rag/ast_manifest.json"

        build_map_command = [
            sys.executable,
            str(self.repo_root / "tools" / "build_ast_map.py"),
            "--project-root",
            ".",
            "--roots",
            *root_list,
            "--out",
            out_path,
            "--manifest",
            manifest_path,
        ]

        build_index_command = [
            sys.executable,
            str(self.repo_root / "tools" / "build_ast_rag_index.py"),
            "--ast",
            out_path,
            "--db",
            str(self.db_path),
        ]

        first = subprocess.run(
            build_map_command,
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if first.returncode != 0:
            raise JsonRpcError(
                code=-32002,
                message="Failed to build AST map.",
                data={
                    "command": build_map_command,
                    "returncode": first.returncode,
                    "stdout": first.stdout,
                    "stderr": first.stderr,
                },
            )

        second = subprocess.run(
            build_index_command,
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if second.returncode != 0:
            raise JsonRpcError(
                code=-32003,
                message="Failed to build AST RAG index.",
                data={
                    "command": build_index_command,
                    "returncode": second.returncode,
                    "stdout": second.stdout,
                    "stderr": second.stderr,
                },
            )

        output_parts = [
            "AST RAG index refreshed.",
            "",
            "build_ast_map.py output:",
            first.stdout.strip(),
            "",
            "build_ast_rag_index.py output:",
            second.stdout.strip(),
        ]
        return "\n".join(part for part in output_parts if part is not None)


class McpAstRagServer:
    """Minimal MCP server exposing AST RAG tools."""

    def __init__(self, service: AstRagService) -> None:
        self.service = service
        self.tools: dict[str, ToolSpec] = {
            "ast_rag_search": ToolSpec(
                name="ast_rag_search",
                description=(
                    "Search the local AST RAG index and return ranked code context "
                    "with @file#line-line anchors."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "k": {
                            "type": "integer",
                            "description": "Number of top results.",
                            "default": 8,
                            "minimum": 1,
                            "maximum": 50,
                        },
                        "neighbor_limit": {
                            "type": "integer",
                            "description": "Max linked calls/imports per hit.",
                            "default": 24,
                            "minimum": 1,
                            "maximum": 100,
                        },
                        "max_source_lines": {
                            "type": "integer",
                            "description": "Max source lines in each hit.",
                            "default": 40,
                            "minimum": 1,
                            "maximum": 300,
                        },
                        "no_source": {
                            "type": "boolean",
                            "description": "Exclude source snippets.",
                            "default": False,
                        },
                        "json": {
                            "type": "boolean",
                            "description": "Return JSON payload text.",
                            "default": False,
                        },
                    },
                    "required": ["query"],
                },
            ),
            "ast_rag_symbol_lookup": ToolSpec(
                name="ast_rag_symbol_lookup",
                description=(
                    "Lookup symbol(s) by qualname or name and return matching "
                    "definitions with anchors."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "qualname": {
                            "type": "string",
                            "description": "Exact or partial symbol name.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum matches.",
                            "default": 5,
                            "minimum": 1,
                            "maximum": 50,
                        },
                        "fuzzy": {
                            "type": "boolean",
                            "description": "Allow partial matching.",
                            "default": True,
                        },
                        "max_source_lines": {
                            "type": "integer",
                            "description": "Max source lines in each hit.",
                            "default": 80,
                            "minimum": 1,
                            "maximum": 400,
                        },
                        "no_source": {
                            "type": "boolean",
                            "description": "Exclude source snippets.",
                            "default": False,
                        },
                    },
                    "required": ["qualname"],
                },
            ),
            "ast_rag_stats": ToolSpec(
                name="ast_rag_stats",
                description="Show AST RAG index metadata and counts.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
            ),
            "ast_rag_refresh_index": ToolSpec(
                name="ast_rag_refresh_index",
                description=(
                    "Rebuild AST map + SQLite index from source files. "
                    "Use this after major code changes."
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "roots": {
                            "type": "array",
                            "description": "Roots to scan.",
                            "items": {"type": "string"},
                            "default": ["src", "tests"],
                        },
                        "include_tests": {
                            "type": "boolean",
                            "description": "Ensure tests/ is included.",
                            "default": True,
                        },
                    },
                },
            ),
        }

    def _tool_list_payload(self) -> dict[str, Any]:
        return {
            "tools": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "inputSchema": spec.input_schema,
                }
                for spec in self.tools.values()
            ]
        }

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "ast_rag_search":
            query = str(arguments.get("query", "")).strip()
            if not query:
                raise JsonRpcError(code=-32602, message="'query' is required.")
            return self.service.rag_search(
                query=query,
                k=int(arguments.get("k", 8) or 8),
                neighbor_limit=int(arguments.get("neighbor_limit", 24) or 24),
                max_source_lines=int(arguments.get("max_source_lines", 40) or 40),
                no_source=bool(arguments.get("no_source", False)),
                output_json=bool(arguments.get("json", False)),
            )

        if name == "ast_rag_symbol_lookup":
            qualname = str(arguments.get("qualname", "")).strip()
            if not qualname:
                raise JsonRpcError(code=-32602, message="'qualname' is required.")
            return self.service.symbol_lookup(
                qualname=qualname,
                max_results=int(arguments.get("max_results", 5) or 5),
                fuzzy=bool(arguments.get("fuzzy", True)),
                max_source_lines=int(arguments.get("max_source_lines", 80) or 80),
                no_source=bool(arguments.get("no_source", False)),
            )

        if name == "ast_rag_stats":
            return self.service.rag_stats()

        if name == "ast_rag_refresh_index":
            raw_roots = arguments.get("roots", ["src", "tests"])
            roots = [str(item) for item in raw_roots] if isinstance(raw_roots, list) else ["src", "tests"]
            include_tests = bool(arguments.get("include_tests", True))
            return self.service.refresh_index(roots=roots, include_tests=include_tests)

        raise JsonRpcError(code=-32601, message=f"Unknown tool: {name}")

    def _dispatch(self, method: str, params: Any) -> dict[str, Any]:
        if method == "initialize":
            negotiated_protocol_version = SUPPORTED_PROTOCOL_VERSION
            if isinstance(params, dict):
                requested_version = params.get("protocolVersion")
                if isinstance(requested_version, str) and requested_version.strip():
                    negotiated_protocol_version = requested_version.strip()

            return {
                "protocolVersion": negotiated_protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Use ast_rag_search for contextual code retrieval. Use "
                    "ast_rag_refresh_index after major code updates."
                ),
            }

        if method in {"notifications/initialized", "initialized"}:
            return {}

        if method == "ping":
            return {}

        if method == "tools/list":
            return self._tool_list_payload()

        if method == "tools/call":
            if not isinstance(params, dict):
                raise JsonRpcError(code=-32602, message="Invalid params for tools/call")
            name = params.get("name")
            if not isinstance(name, str):
                raise JsonRpcError(code=-32602, message="tools/call missing tool name")
            raw_arguments = params.get("arguments", {})
            arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
            text = self._call_tool(name, arguments)
            return {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            }

        if method == "resources/list":
            return {"resources": []}

        if method == "prompts/list":
            return {"prompts": []}

        raise JsonRpcError(code=-32601, message=f"Method not found: {method}")

    def handle_message(self, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            raise JsonRpcError(code=-32600, message="Invalid Request")

        request_id = payload.get("id")
        has_id = "id" in payload

        method = payload.get("method")
        if not isinstance(method, str):
            raise JsonRpcError(code=-32600, message="Invalid Request: missing method")

        params = payload.get("params", {})

        try:
            result = self._dispatch(method, params)
            if has_id:
                return {"jsonrpc": "2.0", "id": request_id, "result": result}
            return None
        except JsonRpcError as exc:
            if not has_id:
                _log(f"Notification error ignored ({method}): {exc.message}")
                return None
            error_payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                },
            }
            if exc.data is not None:
                error_payload["error"]["data"] = exc.data
            return error_payload
        except Exception as exc:  # pragma: no cover
            _log(f"Unhandled server error in method '{method}': {exc}")
            if not has_id:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": "Internal server error",
                    "data": str(exc),
                },
            }


def _read_json_line_message(line: bytes) -> Any:
    payload = line.strip()
    if not payload:
        raise JsonRpcError(code=-32700, message="Empty JSON line")

    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise JsonRpcError(code=-32700, message=f"Invalid JSON payload: {exc.msg}") from exc


def _read_content_length_message(stdin: BinaryIO, first_header_line: bytes) -> Any:
    headers: dict[str, str] = {}

    def parse_header(raw_line: bytes) -> None:
        decoded = raw_line.decode("ascii", errors="replace").strip()
        if not decoded:
            return
        if ":" not in decoded:
            raise JsonRpcError(code=-32700, message="Malformed header")
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    parse_header(first_header_line)

    while True:
        line = stdin.readline()
        if line == b"":
            raise JsonRpcError(code=-32700, message="Unexpected EOF while reading headers")
        if line in (b"\r\n", b"\n"):
            break
        parse_header(line)

    content_length_raw = headers.get("content-length")
    if content_length_raw is None:
        raise JsonRpcError(code=-32700, message="Missing Content-Length header")

    try:
        content_length = int(content_length_raw)
    except ValueError as exc:
        raise JsonRpcError(code=-32700, message="Invalid Content-Length header") from exc

    if content_length < 0:
        raise JsonRpcError(code=-32700, message="Negative Content-Length")

    body = stdin.read(content_length)
    if len(body) != content_length:
        raise JsonRpcError(code=-32700, message="Unexpected EOF while reading body")

    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise JsonRpcError(code=-32700, message=f"Invalid JSON payload: {exc.msg}") from exc


def _read_message(stdin: BinaryIO) -> tuple[Any, str] | None:
    while True:
        line = stdin.readline()
        if line == b"":
            return None
        if line not in (b"\r\n", b"\n"):
            break

    if line.lower().startswith(b"content-length:"):
        return _read_content_length_message(stdin, line), "content-length"

    return _read_json_line_message(line), "json-lines"


def _write_message(stdout: BinaryIO, payload: dict[str, Any], mode: str) -> None:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if mode == "content-length":
        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
        stdout.write(header)
        stdout.write(encoded)
    else:
        stdout.write(encoded)
        stdout.write(b"\n")
    stdout.flush()


def _resolve_repo_root(raw_repo_root: str | None) -> Path:
    if raw_repo_root:
        return Path(raw_repo_root).resolve()
    env_root = os.environ.get("AST_RAG_REPO_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parent.parent


def _resolve_db_path(raw_db_path: str | None, repo_root: Path) -> Path:
    if raw_db_path:
        return Path(raw_db_path).resolve()
    env_db = os.environ.get("AST_RAG_DB")
    if env_db:
        return Path(env_db).resolve()
    return (repo_root / ".windsurf" / "rag" / "ast.db").resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AST RAG MCP server over stdio.")
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root. Defaults to script parent parent or AST_RAG_REPO_ROOT.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite AST RAG DB path. Defaults to AST_RAG_DB or .windsurf/rag/ast.db.",
    )
    args = parser.parse_args()

    repo_root = _resolve_repo_root(args.repo_root)
    db_path = _resolve_db_path(args.db, repo_root)

    _log(f"Starting server with repo_root={repo_root}")
    _log(f"Using db={db_path}")

    service = AstRagService(repo_root=repo_root, db_path=db_path)
    server = McpAstRagServer(service=service)

    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    transport_mode = "json-lines"

    while True:
        try:
            message_with_mode = _read_message(stdin)
            if message_with_mode is None:
                return 0
            message, detected_mode = message_with_mode
            transport_mode = detected_mode
            response = server.handle_message(message)
            if response is not None:
                _write_message(stdout, response, transport_mode)
        except JsonRpcError as exc:
            _log(f"Protocol error: {exc.message}")
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                },
            }
            if exc.data is not None:
                error_response["error"]["data"] = exc.data
            _write_message(stdout, error_response, transport_mode)
        except KeyboardInterrupt:
            _log("Interrupted")
            return 0
        except Exception as exc:  # pragma: no cover
            _log(f"Fatal error: {exc}")
            fatal_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32000,
                    "message": "Fatal server error",
                    "data": str(exc),
                },
            }
            _write_message(stdout, fatal_response, transport_mode)


if __name__ == "__main__":
    raise SystemExit(main())
