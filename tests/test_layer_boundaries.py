"""Enforce the layer dependency rules described in CLAUDE.md via static import analysis."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LLM_AND_HTTP = {"openai", "langchain", "langchain_openai", "langchain_community", "requests", "httpx"}

# layer directory -> modules it must not import (see CLAUDE.md "의존 방향")
FORBIDDEN_IMPORTS: dict[str, set[str]] = {
    "src/common": {"src.collectors", "src.preprocessing", "src.database", "src.analysis", "src.rag"},
    "src/database": {"src.collectors", "src.preprocessing", "src.analysis", "src.rag"},
    "src/collectors": {"src.preprocessing", "src.database", "src.analysis", "src.rag", "openai", "langchain"},
    "src/preprocessing": {"src.collectors", "src.analysis", "src.rag"} | LLM_AND_HTTP,
    "src/analysis": {"src.collectors", "src.preprocessing", "src.rag"},
    "src/rag": {"src.collectors", "src.preprocessing", "src.analysis"},
    # Dashboard is read-only: no API/LLM clients, no batch layers. Q&A only via src.rag.rag_pipeline.
    "dashboard": {"src.collectors", "src.preprocessing", "src.analysis"} | LLM_AND_HTTP,
}
DASHBOARD_ALLOWED_RAG = "src.rag.rag_pipeline"


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
            modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


class LayerBoundaryTest(unittest.TestCase):
    def test_no_forbidden_imports(self) -> None:
        violations: list[str] = []
        for layer, forbidden in FORBIDDEN_IMPORTS.items():
            for path in (ROOT / layer).rglob("*.py"):
                for module in _imported_modules(path):
                    if any(_matches(module, f) for f in forbidden):
                        violations.append(f"{path.relative_to(ROOT)} imports {module}")
                    if (
                        layer == "dashboard"
                        and _matches(module, "src.rag")
                        and module != "src.rag"
                        and not _matches(module, DASHBOARD_ALLOWED_RAG)
                    ):
                        violations.append(
                            f"{path.relative_to(ROOT)} imports {module} "
                            f"(dashboard may only use {DASHBOARD_ALLOWED_RAG})"
                        )
        self.assertEqual(violations, [], "Layer boundary violations:\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
