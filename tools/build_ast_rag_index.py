"""Build a local SQLite retrieval index from an AST map JSONL file."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {path}: {exc.msg}"
                ) from exc
            if isinstance(payload, dict):
                yield payload


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = WAL;

        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            qualname TEXT NOT NULL,
            module TEXT,
            file TEXT NOT NULL,
            start_line INTEGER,
            end_line INTEGER,
            signature TEXT,
            docstring TEXT,
            decorators_json TEXT,
            imports_json TEXT,
            calls_json TEXT,
            parent TEXT,
            file_sha256 TEXT,
            source TEXT,
            parse_error TEXT
        );

        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            dst TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_nodes_file_lines
            ON nodes(file, start_line, end_line);

        CREATE INDEX IF NOT EXISTS idx_nodes_name
            ON nodes(name);

        CREATE INDEX IF NOT EXISTS idx_nodes_qualname
            ON nodes(qualname);

        CREATE INDEX IF NOT EXISTS idx_edges_src
            ON edges(src_id);

        CREATE INDEX IF NOT EXISTS idx_edges_dst
            ON edges(dst);

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def _set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO meta(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _create_or_reset_fts(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_nodes USING fts5(
                id UNINDEXED,
                name,
                qualname,
                module,
                file,
                signature,
                docstring,
                source,
                tokenize = 'unicode61'
            )
            """
        )
        connection.execute("DELETE FROM fts_nodes")
        return True
    except sqlite3.OperationalError:
        return False


def _rebuild_index(
    connection: sqlite3.Connection,
    ast_map_path: Path,
    *,
    include_source_in_fts: bool,
) -> dict[str, int]:
    connection.execute("DELETE FROM edges")
    connection.execute("DELETE FROM nodes")

    fts_enabled = _create_or_reset_fts(connection)

    node_count = 0
    edge_count = 0
    parse_error_count = 0

    for payload in _iter_jsonl(ast_map_path):
        node_id = str(payload.get("id", "")).strip()
        if not node_id:
            continue

        imports = _coerce_str_list(payload.get("imports"))
        calls = _coerce_str_list(payload.get("calls"))
        decorators = _coerce_str_list(payload.get("decorators"))

        parse_error = payload.get("parse_error")
        if parse_error:
            parse_error_count += 1

        row = {
            "id": node_id,
            "kind": str(payload.get("kind", "unknown")),
            "name": str(payload.get("name", "")),
            "qualname": str(payload.get("qualname", "")),
            "module": str(payload.get("module", "")),
            "file": str(payload.get("file", "")),
            "start_line": int(payload.get("start_line", 0) or 0),
            "end_line": int(payload.get("end_line", 0) or 0),
            "signature": str(payload.get("signature", "") or ""),
            "docstring": str(payload.get("docstring", "") or ""),
            "decorators_json": json.dumps(decorators, ensure_ascii=True),
            "imports_json": json.dumps(imports, ensure_ascii=True),
            "calls_json": json.dumps(calls, ensure_ascii=True),
            "parent": str(payload.get("parent", "") or ""),
            "file_sha256": str(payload.get("file_sha256", "") or ""),
            "source": str(payload.get("source", "") or ""),
            "parse_error": str(parse_error or ""),
        }

        connection.execute(
            """
            INSERT INTO nodes (
                id, kind, name, qualname, module, file,
                start_line, end_line, signature, docstring,
                decorators_json, imports_json, calls_json,
                parent, file_sha256, source, parse_error
            ) VALUES (
                :id, :kind, :name, :qualname, :module, :file,
                :start_line, :end_line, :signature, :docstring,
                :decorators_json, :imports_json, :calls_json,
                :parent, :file_sha256, :source, :parse_error
            )
            """,
            row,
        )

        node_count += 1

        for import_target in imports:
            connection.execute(
                "INSERT INTO edges(src_id, edge_type, dst) VALUES (?, 'import', ?)",
                (node_id, import_target),
            )
            edge_count += 1

        for call_target in calls:
            connection.execute(
                "INSERT INTO edges(src_id, edge_type, dst) VALUES (?, 'call', ?)",
                (node_id, call_target),
            )
            edge_count += 1

        if fts_enabled:
            source_for_fts = row["source"] if include_source_in_fts else ""
            connection.execute(
                """
                INSERT INTO fts_nodes(
                    id, name, qualname, module, file,
                    signature, docstring, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["name"],
                    row["qualname"],
                    row["module"],
                    row["file"],
                    row["signature"],
                    row["docstring"],
                    source_for_fts,
                ),
            )

    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "parse_error_count": parse_error_count,
        "fts_enabled": 1 if fts_enabled else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a local SQLite retrieval index from AST JSONL records."
    )
    parser.add_argument(
        "--ast",
        default=".windsurf/rag/ast_map.jsonl",
        help="Path to AST JSONL map.",
    )
    parser.add_argument(
        "--db",
        default=".windsurf/rag/ast.db",
        help="Path to SQLite database output.",
    )
    parser.add_argument(
        "--skip-source-in-fts",
        action="store_true",
        help="Exclude source code from FTS index (smaller DB, less recall).",
    )
    args = parser.parse_args()

    ast_path = Path(args.ast).resolve()
    db_path = Path(args.db).resolve()

    if not ast_path.exists():
        print(f"AST map not found: {ast_path}")
        print("Run tools/build_ast_map.py first.")
        return 1

    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as connection:
        _ensure_schema(connection)

        stats = _rebuild_index(
            connection,
            ast_path,
            include_source_in_fts=not args.skip_source_in_fts,
        )

        _set_meta(connection, "generated_at_utc", datetime.now(timezone.utc).isoformat())
        _set_meta(connection, "ast_map_path", str(ast_path))
        _set_meta(connection, "node_count", str(stats["node_count"]))
        _set_meta(connection, "edge_count", str(stats["edge_count"]))
        _set_meta(connection, "parse_error_count", str(stats["parse_error_count"]))
        _set_meta(connection, "fts_enabled", str(stats["fts_enabled"]))
        _set_meta(
            connection,
            "source_in_fts",
            "0" if args.skip_source_in_fts else "1",
        )

        connection.commit()

    print(f"AST map: {ast_path}")
    print(f"RAG DB:  {db_path}")
    print(f"Nodes:   {stats['node_count']}")
    print(f"Edges:   {stats['edge_count']}")
    print(f"FTS5:    {'enabled' if stats['fts_enabled'] else 'disabled'}")
    print(f"Parse errors: {stats['parse_error_count']}")

    if not stats["fts_enabled"]:
        print("Warning: SQLite FTS5 is unavailable. Query script will use LIKE fallback.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
