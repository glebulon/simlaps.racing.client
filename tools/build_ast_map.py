"""Build a project-wide AST symbol map for local retrieval.

This script scans Python files, extracts symbols and relationships, and writes
JSONL records that can be indexed for RAG-style code retrieval.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_ROOTS = ("src",)
IGNORED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".windsurf",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "venv",
    "venv-sim-laps-client",
}


@dataclass
class ParseResult:
    records: list[dict[str, object]]
    file_sha256: str
    parse_error: str | None


def _sha256_bytes(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def _safe_unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _extract_source(source_lines: list[str], start_line: int, end_line: int) -> str:
    if not source_lines:
        return ""
    start_line = max(start_line, 1)
    end_line = max(end_line, start_line)
    if start_line > len(source_lines):
        return ""
    return "\n".join(source_lines[start_line - 1 : min(end_line, len(source_lines))])


def _module_name_from_relative_path(relative_path: Path) -> str:
    parts = list(relative_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return relative_path.stem
    return ".".join(parts)


def _iter_python_files(project_root: Path, roots: Sequence[str]) -> list[Path]:
    files: set[Path] = set()

    for root in roots:
        root_path = (project_root / root).resolve()
        if not root_path.exists():
            continue

        if root_path.is_file():
            if root_path.suffix == ".py":
                files.add(root_path)
            continue

        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if dirname not in IGNORED_DIR_NAMES and not dirname.startswith(".")
            ]
            current_dir = Path(dirpath)
            for filename in filenames:
                if filename.endswith(".py"):
                    files.add(current_dir / filename)

    return sorted(files)


def _format_arg(arg: ast.arg, default: ast.AST | None = None) -> str:
    rendered = arg.arg
    annotation = _safe_unparse(arg.annotation)
    if annotation:
        rendered += f": {annotation}"
    if default is not None:
        default_text = _safe_unparse(default) or "..."
        rendered += f" = {default_text}"
    return rendered


def _build_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args

    positional_args = list(args.posonlyargs) + list(args.args)
    positional_defaults: list[ast.AST | None] = [None] * (
        len(positional_args) - len(args.defaults)
    ) + list(args.defaults)

    parts: list[str] = []

    for index, arg in enumerate(args.posonlyargs):
        parts.append(_format_arg(arg, positional_defaults[index]))

    if args.posonlyargs:
        parts.append("/")

    pos_offset = len(args.posonlyargs)
    for index, arg in enumerate(args.args, start=pos_offset):
        parts.append(_format_arg(arg, positional_defaults[index]))

    if args.vararg is not None:
        parts.append(f"*{_format_arg(args.vararg)}")
    elif args.kwonlyargs:
        parts.append("*")

    for kwarg, kw_default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(_format_arg(kwarg, kw_default))

    if args.kwarg is not None:
        parts.append(f"**{_format_arg(args.kwarg)}")

    signature = f"{node.name}({', '.join(parts)})"
    return_annotation = _safe_unparse(node.returns)
    if return_annotation:
        signature += f" -> {return_annotation}"
    return signature


def _build_class_signature(node: ast.ClassDef) -> str:
    base_parts = [_safe_unparse(base) for base in node.bases if _safe_unparse(base)]
    keyword_parts = []
    for keyword in node.keywords:
        value = _safe_unparse(keyword.value)
        if not value:
            continue
        if keyword.arg is None:
            keyword_parts.append(f"**{value}")
        else:
            keyword_parts.append(f"{keyword.arg}={value}")

    all_parts = base_parts + keyword_parts
    if not all_parts:
        return node.name
    return f"{node.name}({', '.join(all_parts)})"


def _call_target(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        parent = _call_target(expr.value)
        if parent:
            return f"{parent}.{expr.attr}"
        return expr.attr
    if isinstance(expr, ast.Subscript):
        return _call_target(expr.value)
    if isinstance(expr, ast.Call):
        return _call_target(expr.func)
    return None


class _CallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        target = _call_target(node.func)
        if target:
            self.calls.add(target)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _collect_calls_from_body(body: Iterable[ast.stmt]) -> list[str]:
    visitor = _CallCollector()
    for statement in body:
        visitor.visit(statement)
    return sorted(visitor.calls)


def _collect_module_imports(tree: ast.Module) -> list[str]:
    imports: set[str] = set()

    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                imports.add(alias.name)
        elif isinstance(statement, ast.ImportFrom):
            base_module = statement.module or ""
            prefix = "." * statement.level
            if base_module:
                prefix = f"{prefix}{base_module}"
            for alias in statement.names:
                if alias.name == "*":
                    target = f"{prefix}.*" if prefix else "*"
                elif prefix:
                    target = f"{prefix}.{alias.name}"
                else:
                    target = alias.name
                imports.add(target)

    return sorted(imports)


class _RecordBuilder(ast.NodeVisitor):
    def __init__(
        self,
        relative_file: str,
        module_name: str,
        file_sha256: str,
        source_lines: list[str],
        module_imports: list[str],
        module_docstring: str,
    ) -> None:
        self.relative_file = relative_file
        self.module_name = module_name
        self.file_sha256 = file_sha256
        self.source_lines = source_lines
        self.module_imports = module_imports
        self.module_docstring = module_docstring
        self.records: list[dict[str, object]] = []
        self._scope_stack: list[str] = []

    def build(self, tree: ast.Module) -> list[dict[str, object]]:
        module_end_line = max(len(self.source_lines), 1)
        module_record = self._make_record(
            kind="module",
            name=self.module_name.split(".")[-1] or self.module_name,
            qualname=self.module_name,
            parent=None,
            start_line=1,
            end_line=module_end_line,
            signature=None,
            docstring=self.module_docstring,
            decorators=[],
            imports=self.module_imports,
            calls=[],
        )
        self.records.append(module_record)

        self._scope_stack.append(self.module_name)
        for statement in tree.body:
            self.visit(statement)
        self._scope_stack.pop()

        return self.records

    def _current_parent(self) -> str | None:
        if not self._scope_stack:
            return None
        return self._scope_stack[-1]

    def _make_qualname(self, local_name: str) -> str:
        parent = self._current_parent()
        if parent:
            return f"{parent}.{local_name}"
        return local_name

    def _make_record(
        self,
        *,
        kind: str,
        name: str,
        qualname: str,
        parent: str | None,
        start_line: int,
        end_line: int,
        signature: str | None,
        docstring: str,
        decorators: list[str],
        imports: list[str],
        calls: list[str],
    ) -> dict[str, object]:
        return {
            "id": f"{self.relative_file}::{qualname}",
            "kind": kind,
            "name": name,
            "qualname": qualname,
            "module": self.module_name,
            "file": self.relative_file,
            "parent": parent,
            "start_line": start_line,
            "end_line": end_line,
            "signature": signature,
            "docstring": docstring,
            "decorators": decorators,
            "imports": imports,
            "calls": calls,
            "file_sha256": self.file_sha256,
            "source": _extract_source(self.source_lines, start_line, end_line),
            "parse_error": None,
        }

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = self._make_qualname(node.name)
        parent = self._current_parent()
        start_line = getattr(node, "lineno", 1)
        end_line = getattr(node, "end_lineno", start_line)
        decorators = [_safe_unparse(d) for d in node.decorator_list if _safe_unparse(d)]

        record = self._make_record(
            kind="class",
            name=node.name,
            qualname=qualname,
            parent=parent,
            start_line=start_line,
            end_line=end_line,
            signature=_build_class_signature(node),
            docstring=ast.get_docstring(node) or "",
            decorators=decorators,
            imports=[],
            calls=_collect_calls_from_body(node.body),
        )
        self.records.append(record)

        self._scope_stack.append(qualname)
        for statement in node.body:
            self.visit(statement)
        self._scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_like(node, kind="function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_like(node, kind="async_function")

    def _visit_function_like(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        kind: str,
    ) -> None:
        qualname = self._make_qualname(node.name)
        parent = self._current_parent()
        start_line = getattr(node, "lineno", 1)
        end_line = getattr(node, "end_lineno", start_line)
        decorators = [_safe_unparse(d) for d in node.decorator_list if _safe_unparse(d)]

        record = self._make_record(
            kind=kind,
            name=node.name,
            qualname=qualname,
            parent=parent,
            start_line=start_line,
            end_line=end_line,
            signature=_build_function_signature(node),
            docstring=ast.get_docstring(node) or "",
            decorators=decorators,
            imports=[],
            calls=_collect_calls_from_body(node.body),
        )
        self.records.append(record)

        self._scope_stack.append(qualname)
        for statement in node.body:
            self.visit(statement)
        self._scope_stack.pop()


def _parse_file(file_path: Path, project_root: Path) -> ParseResult:
    raw_bytes = file_path.read_bytes()
    source_text = raw_bytes.decode("utf-8", errors="replace")
    file_sha256 = _sha256_bytes(raw_bytes)
    source_lines = source_text.splitlines()

    relative_path = file_path.relative_to(project_root)
    relative_path_posix = relative_path.as_posix()
    module_name = _module_name_from_relative_path(relative_path)

    try:
        tree = ast.parse(source_text, filename=relative_path_posix)
    except SyntaxError as exc:
        end_line = max(len(source_lines), 1)
        parse_error = f"{exc.msg} (line {exc.lineno})"
        error_record = {
            "id": f"{relative_path_posix}::{module_name}",
            "kind": "module_error",
            "name": module_name.split(".")[-1] or module_name,
            "qualname": module_name,
            "module": module_name,
            "file": relative_path_posix,
            "parent": None,
            "start_line": 1,
            "end_line": end_line,
            "signature": None,
            "docstring": "",
            "decorators": [],
            "imports": [],
            "calls": [],
            "file_sha256": file_sha256,
            "source": _extract_source(source_lines, 1, end_line),
            "parse_error": parse_error,
        }
        return ParseResult(records=[error_record], file_sha256=file_sha256, parse_error=parse_error)

    module_imports = _collect_module_imports(tree)
    module_docstring = ast.get_docstring(tree) or ""

    builder = _RecordBuilder(
        relative_file=relative_path_posix,
        module_name=module_name,
        file_sha256=file_sha256,
        source_lines=source_lines,
        module_imports=module_imports,
        module_docstring=module_docstring,
    )
    records = builder.build(tree)
    return ParseResult(records=records, file_sha256=file_sha256, parse_error=None)


def _write_jsonl(path: Path, records: Sequence[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            handle.write("\n")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a JSONL AST map for local RAG.")
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root directory. Defaults to current directory.",
    )
    parser.add_argument(
        "--roots",
        nargs="+",
        default=list(DEFAULT_ROOTS),
        help="Root directories/files to scan (relative to project root).",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include tests/ in addition to selected roots.",
    )
    parser.add_argument(
        "--out",
        default=".windsurf/rag/ast_map.jsonl",
        help="Output JSONL path (relative to project root unless absolute).",
    )
    parser.add_argument(
        "--manifest",
        default=".windsurf/rag/ast_manifest.json",
        help="Manifest output path (relative to project root unless absolute).",
    )
    parser.add_argument(
        "--fail-on-parse-error",
        action="store_true",
        help="Exit with non-zero status if any files fail to parse.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output.",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    roots = list(args.roots)
    if args.include_tests and "tests" not in roots:
        roots.append("tests")

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = project_root / out_path

    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = project_root / manifest_path

    python_files = _iter_python_files(project_root, roots)

    all_records: list[dict[str, object]] = []
    manifest_files: list[dict[str, object]] = []
    parse_error_count = 0

    for file_path in python_files:
        result = _parse_file(file_path, project_root)
        all_records.extend(result.records)

        rel_file = file_path.relative_to(project_root).as_posix()
        manifest_files.append(
            {
                "file": rel_file,
                "sha256": result.file_sha256,
                "record_count": len(result.records),
                "parse_error": result.parse_error,
            }
        )

        if result.parse_error:
            parse_error_count += 1
            if not args.quiet:
                print(f"[WARN] Parse error in {rel_file}: {result.parse_error}")

    all_records.sort(
        key=lambda item: (
            str(item.get("file", "")),
            int(item.get("start_line", 0) or 0),
            str(item.get("kind", "")),
            str(item.get("qualname", "")),
        )
    )

    _write_jsonl(out_path, all_records)

    manifest_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root),
        "roots": roots,
        "file_count": len(python_files),
        "record_count": len(all_records),
        "parse_error_count": parse_error_count,
        "out": str(out_path),
        "files": manifest_files,
    }
    _write_json(manifest_path, manifest_payload)

    if not args.quiet:
        print(f"Scanned files: {len(python_files)}")
        print(f"Records written: {len(all_records)}")
        print(f"Parse errors: {parse_error_count}")
        print(f"AST map: {out_path}")
        print(f"Manifest: {manifest_path}")

    if args.fail_on_parse_error and parse_error_count > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
