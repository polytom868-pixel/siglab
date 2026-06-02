from __future__ import annotations

import unittest
from pathlib import Path
import ast


from conftest import REPO_ROOT
THIS_FILE = Path(__file__).resolve()
SCAN_ROOTS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "pyproject.toml",
    REPO_ROOT / "siglab",
    REPO_ROOT / "tests",
    REPO_ROOT / "benchmarks",
]
FORBIDDEN_SNIPPETS = [
    "/Users/",
    "sosovalue_auto_researcher",
]
SKIP_PARTS = {
    "runs",
    "backups",
    ".pytest_cache",
    "__pycache__",
    ".venv",
}
SKIP_SUFFIXES = {
    ".db",
    ".log",
    ".pyc",
}
TEXT_SUFFIXES = {
    "",
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".toml",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}


class RepoHygieneTests(unittest.TestCase):
    def test_public_files_do_not_contain_local_machine_paths(self) -> None:
        offenders: list[str] = []
        for root in SCAN_ROOTS:
            if root.is_file():
                specs = [root]
            else:
                specs = [
                    path
                    for path in root.rglob("*")
                    if path.is_file()
                    and not any(part in SKIP_PARTS for part in path.parts)
                    and path.suffix not in SKIP_SUFFIXES
                    and path.suffix in TEXT_SUFFIXES
                ]
            for path in specs:
                if path.resolve() == THIS_FILE:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                if any(snippet in text for snippet in FORBIDDEN_SNIPPETS):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [])

    def test_deleted_legacy_modules_are_not_imported(self) -> None:
        forbidden = {"siglab.data.lake", "siglab.data.providers", "siglab.llm.kimi", "siglab.settings", "siglab.models"}
        offenders: list[str] = []
        for root in [REPO_ROOT / "siglab", REPO_ROOT / "tests"]:
            for path in root.rglob("*.py"):
                if any(part in SKIP_PARTS for part in path.parts):
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = {alias.name for alias in node.names}
                    elif isinstance(node, ast.ImportFrom):
                        names = {node.module or ""}
                    else:
                        continue
                    if names & forbidden:
                        offenders.append(f"{path.relative_to(REPO_ROOT)} imports {sorted(names & forbidden)}")
        self.assertEqual(offenders, [])

    def test_direct_runtime_dependencies_are_declared(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for dependency in ["httpx", "certifi", "websockets", "numpy", "pandas", "pyarrow", "pyyaml"]:
            self.assertIn(dependency, pyproject.lower())


if __name__ == "__main__":
    unittest.main()


