"""AST-based Python language analyzer for Milestone 7."""

import ast
from pathlib import Path
from typing import List, Optional, Set, Union

from fusion_agent.repository.analyzers.base import LanguageAnalyzer
from fusion_agent.repository.indexer import SymbolDefinition


class PythonAnalyzer(LanguageAnalyzer):
    """Parses Python source files using standard AST module."""

    def supports(self, path: str) -> bool:
        return path.lower().endswith(".py")

    def extract_symbols(self, rel_path: str, content: str) -> List[SymbolDefinition]:
        """Extract classes, methods, and functions with signatures and docstrings."""
        symbols: List[SymbolDefinition] = []
        try:
            tree = ast.parse(content, filename=rel_path)
        except Exception:
            return symbols

        for stmt in tree.body:
            if isinstance(stmt, ast.ClassDef):
                doc = ast.get_docstring(stmt) or ""
                bases = [self._format_node_name(b) for b in stmt.bases]
                sig = f"class {stmt.name}({', '.join(bases)})" if bases else f"class {stmt.name}"
                sym = SymbolDefinition(
                    name=stmt.name,
                    kind="class",
                    rel_path=rel_path,
                    line_start=stmt.lineno,
                    line_end=getattr(stmt, "end_lineno", stmt.lineno),
                    signature=sig,
                    docstring=doc,
                )
                symbols.append(sym)

                for item in stmt.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m_doc = ast.get_docstring(item) or ""
                        m_sig = self._format_func_signature(item)
                        m_sym = SymbolDefinition(
                            name=f"{stmt.name}.{item.name}",
                            kind="method",
                            rel_path=rel_path,
                            line_start=item.lineno,
                            line_end=getattr(item, "end_lineno", item.lineno),
                            signature=m_sig,
                            docstring=m_doc,
                        )
                        symbols.append(m_sym)

            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(stmt) or ""
                sig = self._format_func_signature(stmt)
                sym = SymbolDefinition(
                    name=stmt.name,
                    kind="function",
                    rel_path=rel_path,
                    line_start=stmt.lineno,
                    line_end=getattr(stmt, "end_lineno", stmt.lineno),
                    signature=sig,
                    docstring=doc,
                )
                symbols.append(sym)

        return symbols

    def extract_dependencies(self, rel_path: str, content: str, all_files: Set[str]) -> Set[str]:
        """Resolve internal project dependencies from import statements."""
        dependencies: Set[str] = set()
        try:
            tree = ast.parse(content, filename=rel_path)
        except Exception:
            return dependencies

        for stmt in tree.body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    resolved = self._resolve_module_to_file(alias.name, all_files)
                    if resolved and resolved != rel_path:
                        dependencies.add(resolved)
            elif isinstance(stmt, ast.ImportFrom):
                if stmt.level and stmt.level > 0:
                    # Relative import resolution
                    cur_p = Path(rel_path).parent
                    for _ in range(stmt.level - 1):
                        cur_p = cur_p.parent
                    base_rel = cur_p.as_posix().strip(".")
                    mod_part = (stmt.module or "").replace(".", "/")
                    target_base = f"{base_rel}/{mod_part}".strip("/") if base_rel else mod_part

                    for alias in stmt.names:
                        cand1 = f"{target_base}/{alias.name}.py".strip("/")
                        cand2 = f"{target_base}.py".strip("/")
                        if cand1 in all_files and cand1 != rel_path:
                            dependencies.add(cand1)
                        elif cand2 in all_files and cand2 != rel_path:
                            dependencies.add(cand2)
                    if f"{target_base}.py" in all_files and f"{target_base}.py" != rel_path:
                        dependencies.add(f"{target_base}.py")
                else:
                    mod = stmt.module or ""
                    resolved = self._resolve_module_to_file(mod, all_files)
                    if resolved and resolved != rel_path:
                        dependencies.add(resolved)

        return dependencies

    def detect_tests(self, rel_path: str, all_files: Set[str]) -> Set[str]:
        """Associate Python source files with their test counterparts."""
        tests: Set[str] = set()
        stem = Path(rel_path).stem
        is_test = stem.startswith("test_") or stem.endswith("_test") or "/tests/" in rel_path

        if not is_test:
            candidate_names = {f"test_{stem}.py", f"{stem}_test.py"}
            for f in all_files:
                if Path(f).name in candidate_names:
                    tests.add(f)
        else:
            # If this is a test, reverse match source
            source_stem = stem
            if source_stem.startswith("test_"):
                source_stem = source_stem[5:]
            elif source_stem.endswith("_test"):
                source_stem = source_stem[:-5]

            cand_source = f"{source_stem}.py"
            for f in all_files:
                if Path(f).name == cand_source and f != rel_path:
                    tests.add(f)

        return tests

    def _format_func_signature(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> str:
        prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
        args = []
        for arg in node.args.args:
            if arg.annotation:
                args.append(f"{arg.arg}: {ast.unparse(arg.annotation)}")
            else:
                args.append(arg.arg)
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")
        ret_ann = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"{prefix}{node.name}({', '.join(args)}){ret_ann}"

    def _format_node_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._format_node_name(node.value)}.{node.attr}"
        return ""

    def _resolve_module_to_file(self, module_name: str, all_files: Set[str]) -> Optional[str]:
        if not module_name:
            return None
        parts = module_name.split(".")
        cand_file = "/".join(parts) + ".py"
        if cand_file in all_files:
            return cand_file

        cand_pkg = "/".join(parts) + "/__init__.py"
        if cand_pkg in all_files:
            return cand_pkg

        if len(parts) > 1:
            cand_parent = "/".join(parts[:-1]) + ".py"
            if cand_parent in all_files:
                return cand_parent

        return None
