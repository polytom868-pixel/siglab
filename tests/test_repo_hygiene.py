from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
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


if __name__ == "__main__":
    unittest.main()


