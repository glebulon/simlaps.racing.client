---
description: Build and query a local AST RAG index for better coding assistance
---

# AST RAG Workflow

Use this workflow to generate a symbol-level AST map and query it for focused context before asking for implementation help.

## 1) Build AST map JSONL

From repo root:

```bash
python tools/build_ast_map.py --project-root . --roots src tests --out .windsurf/rag/ast_map.jsonl --manifest .windsurf/rag/ast_manifest.json
```

What this does:
- Parses Python files under `src/` and `tests/`
- Extracts modules, classes, functions, line ranges, imports, and call targets
- Writes:
  - `.windsurf/rag/ast_map.jsonl`
  - `.windsurf/rag/ast_manifest.json`

## 2) Build local SQLite RAG index

```bash
python tools/build_ast_rag_index.py --ast .windsurf/rag/ast_map.jsonl --db .windsurf/rag/ast.db
```

What this does:
- Builds `nodes` and `edges` tables
- Builds FTS5 text index when available
- Stores index metadata in `meta`

## 3) Query for targeted context

```bash
python tools/query_ast_rag.py "where is lap validity decided" --db .windsurf/rag/ast.db --k 8
```

Useful variants:

```bash
python tools/query_ast_rag.py "SharedSessionManager" --db .windsurf/rag/ast.db --k 12
python tools/query_ast_rag.py "telemetry fallback handling" --db .windsurf/rag/ast.db --k 10 --no-source
python tools/query_ast_rag.py "APIClient submit" --db .windsurf/rag/ast.db --k 6 --json
```

## 4) Use in Windsurf chats

1. Run a query.
2. Copy the most relevant returned blocks (`@file#line-line` + snippet).
3. Paste that context into your Windsurf prompt.
4. Ask for a concrete action (bug fix, refactor, tests, etc.).

## 5) Refresh policy

Rebuild AST map + index after significant code changes:

```bash
python tools/build_ast_map.py --project-root . --roots src tests --out .windsurf/rag/ast_map.jsonl --manifest .windsurf/rag/ast_manifest.json
python tools/build_ast_rag_index.py --ast .windsurf/rag/ast_map.jsonl --db .windsurf/rag/ast.db
```

## 6) Optional: Run as MCP server (best Windsurf integration)

Start server manually:

```bash
python tools/mcp_ast_rag_server.py --repo-root . --db .windsurf/rag/ast.db
```

Register this command as a stdio MCP server in Windsurf and enable it for this workspace.

Recommended tool usage from chat:
- `ast_rag_search` for contextual retrieval
- `ast_rag_symbol_lookup` for direct symbol jumps
- `ast_rag_stats` to verify DB state
- `ast_rag_refresh_index` after major code changes
