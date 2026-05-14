from __future__ import annotations

import unittest
from pathlib import Path

from siglab.hardening_profile import build_profile, profile_as_text


class HardeningProfileTests(unittest.TestCase):
    def test_profile_imports_modules_and_has_no_high_risk_findings(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profile = build_profile(root)

        self.assertGreater(profile["summary"]["module_count"], 20)
        self.assertGreater(profile["summary"]["public_object_count"], 50)
        self.assertEqual(profile["summary"]["by_kind"].get("import_error"), None)
        self.assertEqual(profile["summary"]["by_severity"].get("critical"), None)
        self.assertEqual(profile["summary"]["by_severity"].get("high"), None)

    def test_profile_text_is_operator_readable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = profile_as_text(build_profile(root))

        self.assertIn("SigLab hardening profile", text)
        self.assertIn("Findings:", text)
        self.assertIn("modules=", text)


if __name__ == "__main__":
    unittest.main()
