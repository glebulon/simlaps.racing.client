"""Query a local AST RAG SQLite index and print compact context blocks."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


def _get_meta(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return str(row[0])


def _safe_fts_query(raw_query: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_\.]+", raw_query)
    if not tokens:
        return raw_query.strip()
    return " OR ".join(tokens)


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


def _parse_json_array(raw_json: str | None) -> list[str]:
    if not raw_json:
        return []
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload]


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


def _anchor(row: sqlite3.Row) -> str:
    start_line = int(row["start_line"] or 1)
    end_line = int(row["end_line"] or start_line)
    return f"@{row['file']}#{start_line}-{end_line}"


def _clip_lines(text: str, max_lines: int) -> str:
    if max_lines <= 0:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    clipped = lines[:max_lines]
    clipped.append("... [truncated]")
    return "\n".join(clipped)


def _render_markdown(
    results: list[sqlite3.Row],
    neighbors: dict[str, dict[str, list[str]]],
    *,
    max_source_lines: int,
    include_source: bool,
) -> str:
    if not results:
        return "No matching symbols found."

    chunks: list[str] = []

    for index, row in enumerate(results, start=1):
        node_id = str(row["id"])
        related = neighbors.get(node_id, {"call": [], "import": []})

        header = f"### {index}. `{row['qualname']}` ({row['kind']})"
        chunks.append(header)
        chunks.append(f"- Location: {_anchor(row)}")

        signature = str(row["signature"] or "").strip()
        if signature:
            chunks.append(f"- Signature: `{signature}`")

        docstring = str(row["docstring"] or "").strip()
        if docstring:
            first_line = docstring.splitlines()[0].strip()
            if first_line:
                chunks.append(f"- Doc: {first_line}")

        call_targets = related.get("call", [])[:8]
        import_targets = related.get("import", [])[:8]
        if call_targets:
            chunks.append(f"- Calls: {', '.join(f'`{item}`' for item in call_targets)}")
        if import_targets:
            chunks.append(f"- Imports: {', '.join(f'`{item}`' for item in import_targets)}")

        if include_source:
            source = _clip_lines(str(row["source"] or ""), max_source_lines).strip()
            if source:
                chunks.append("```python")
                chunks.append(source)
                chunks.append("```")

        chunks.append("")

    return "\n".join(chunks).rstrip()


def _render_json(
    results: list[sqlite3.Row],
    neighbors: dict[str, dict[str, list[str]]],
    *,
    max_source_lines: int,
    include_source: bool,
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
                "id": node_id,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Query the local AST RAG index.")
    parser.add_argument("query", help="Natural-language or symbol query.")
    parser.add_argument(
        "--db",
        default=".windsurf/rag/ast.db",
        help="Path to SQLite AST RAG database.",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=8,
        help="Number of top symbols to return.",
    )
    parser.add_argument(
        "--neighbor-limit",
        type=int,
        default=24,
        help="Max outgoing edge rows loaded per result.",
    )
    parser.add_argument(
        "--max-source-lines",
        type=int,
        default=40,
        help="Max source lines per result block.",
    )
    parser.add_argument(
        "--no-source",
        action="store_true",
        help="Do not include source snippets in output.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of Markdown-style output.",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"RAG DB not found: {db_path}")
        print("Run tools/build_ast_rag_index.py first.")
        return 1

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row

        results = _search_nodes(connection, args.query, max(args.k, 1))
        neighbors: dict[str, dict[str, list[str]]] = {}

        for row in results:
            node_id = str(row["id"])
            neighbors[node_id] = _collect_neighbors(
                connection,
                node_id,
                limit=max(args.neighbor_limit, 1),
            )

        if args.json:
            print(
                _render_json(
                    results,
                    neighbors,
                    max_source_lines=max(args.max_source_lines, 1),
                    include_source=not args.no_source,
                )
            )
        else:
            print(
                _render_markdown(
                    results,
                    neighbors,
                    max_source_lines=max(args.max_source_lines, 1),
                    include_source=not args.no_source,
                )
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
