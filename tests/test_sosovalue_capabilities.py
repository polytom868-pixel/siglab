from __future__ import annotations

import unittest

from siglab.data.sosovalue_capabilities import (
    CAPABILITIES,
    SoSoValueCapability,
    capability_matrix,
)


class SoSoValueCapabilityDataclassTests(unittest.TestCase):
    def test_construct_minimal_capability(self) -> None:
        cap = SoSoValueCapability(
            "TestModule",
            "GET /test/endpoint",
            "Client.test_method",
            True,
            False,
            True,
            False,
            False,
            "IMPLEMENTED",
            "Test reason.",
        )
        self.assertEqual(cap.module, "TestModule")
        self.assertEqual(cap.endpoint, "GET /test/endpoint")
        self.assertEqual(cap.wrapper, "Client.test_method")
        self.assertTrue(cap.tested)
        self.assertFalse(cap.cached)
        self.assertTrue(cap.retried)
        self.assertFalse(cap.rate_limited)
        self.assertFalse(cap.used_by_strategy)
        self.assertEqual(cap.status, "IMPLEMENTED")
        self.assertEqual(cap.reason, "Test reason.")

    def test_construct_capability_with_none_wrapper(self) -> None:
        cap = SoSoValueCapability(
            "BlockedModule",
            "blocked/endpoint",
            None,
            False,
            False,
            False,
            False,
            False,
            "BLOCKED",
            "No endpoint found.",
        )
        self.assertIsNone(cap.wrapper)

    def test_frozen_dataclass_prevents_attribute_mutation(self) -> None:
        cap = SoSoValueCapability(
            "TestModule",
            "GET /test/endpoint",
            None,
            False,
            False,
            False,
            False,
            False,
            "DRAFT",
            "Draft reason.",
        )
        with self.assertRaises(Exception):
            cap.module = "MutatedModule"  # type: ignore[misc]

    def test_repr_is_meaningful(self) -> None:
        cap = SoSoValueCapability(
            "TestModule",
            "GET /test/ep",
            None,
            False,
            False,
            False,
            False,
            False,
            "DRAFT",
            "A reason.",
        )
        r = repr(cap)
        self.assertIn("SoSoValueCapability", r)
        self.assertIn("TestModule", r)
        self.assertIn("DRAFT", r)

    def test_all_fields_have_correct_types_in_capabilities_tuple(self) -> None:
        for cap in CAPABILITIES:
            self.assertIsInstance(cap.module, str)
            self.assertIsInstance(cap.endpoint, str)
            self.assertIsInstance(cap.wrapper, (str, type(None)))
            self.assertIsInstance(cap.tested, bool)
            self.assertIsInstance(cap.cached, bool)
            self.assertIsInstance(cap.retried, bool)
            self.assertIsInstance(cap.rate_limited, bool)
            self.assertIsInstance(cap.used_by_strategy, bool)
            self.assertIsInstance(cap.status, str)
            self.assertIsInstance(cap.reason, str)


class CapabilityMatrixStructureTests(unittest.TestCase):
    def test_returns_list(self) -> None:
        matrix = capability_matrix()
        self.assertIsInstance(matrix, list)

    def test_returns_same_count_as_capabilities(self) -> None:
        matrix = capability_matrix()
        self.assertEqual(len(matrix), len(CAPABILITIES))

    def test_every_entry_has_all_required_keys(self) -> None:
        required_keys = {
            "doc_module",
            "endpoint",
            "siglab_wrapper",
            "tested",
            "cached",
            "retried",
            "rate_limited",
            "used_by_strategy",
            "status",
            "reason",
        }
        for entry in capability_matrix():
            self.assertEqual(set(entry.keys()), required_keys)

    def test_every_entry_has_correct_value_types(self) -> None:
        for entry in capability_matrix():
            self.assertIsInstance(entry["doc_module"], str)
            self.assertIsInstance(entry["endpoint"], str)
            self.assertIsInstance(entry["siglab_wrapper"], (str, type(None)))
            self.assertIsInstance(entry["tested"], bool)
            self.assertIsInstance(entry["cached"], bool)
            self.assertIsInstance(entry["retried"], bool)
            self.assertIsInstance(entry["rate_limited"], bool)
            self.assertIsInstance(entry["used_by_strategy"], bool)
            self.assertIsInstance(entry["status"], str)
            self.assertIsInstance(entry["reason"], str)

    def test_matrix_values_match_capabilities(self) -> None:
        matrix = capability_matrix()
        for cap, entry in zip(CAPABILITIES, matrix):
            self.assertEqual(entry["doc_module"], cap.module)
            self.assertEqual(entry["endpoint"], cap.endpoint)
            self.assertEqual(entry["siglab_wrapper"], cap.wrapper)
            self.assertEqual(entry["tested"], cap.tested)
            self.assertEqual(entry["cached"], cap.cached)
            self.assertEqual(entry["retried"], cap.retried)
            self.assertEqual(entry["rate_limited"], cap.rate_limited)
            self.assertEqual(entry["used_by_strategy"], cap.used_by_strategy)
            self.assertEqual(entry["status"], cap.status)
            self.assertEqual(entry["reason"], cap.reason)


class ImplementedCapabilityTests(unittest.TestCase):
    implemented: list[SoSoValueCapability]
    matrix_implemented: list[dict[str, object]]

    def setUp(self) -> None:
        self.implemented = [c for c in CAPABILITIES if c.status == "IMPLEMENTED"]
        self.matrix_implemented = [
            e for e in capability_matrix() if e["status"] == "IMPLEMENTED"
        ]

    def test_implemented_count(self) -> None:
        self.assertEqual(len(self.implemented), 10)

    def test_implemented_have_wrapper(self) -> None:
        for cap in self.implemented:
            self.assertIsNotNone(
                cap.wrapper,
                f"IMPLEMENTED {cap.module}/{cap.endpoint} has no wrapper",
            )
            self.assertIsInstance(cap.wrapper, str)
            self.assertNotEqual(cap.wrapper, "")

    def test_implemented_have_nonempty_reason(self) -> None:
        for cap in self.implemented:
            self.assertTrue(
                cap.reason, f"IMPLEMENTED {cap.module}/{cap.endpoint} has empty reason"
            )

    def test_implemented_tested_flag_consistent(self) -> None:
        for cap in self.implemented:
            self.assertTrue(
                cap.tested,
                f"IMPLEMENTED {cap.module}/{cap.endpoint} tested is False",
            )

    def test_implemented_cached_flag_consistent(self) -> None:
        for cap in self.implemented:
            self.assertTrue(
                cap.cached,
                f"IMPLEMENTED {cap.module}/{cap.endpoint} cached is False",
            )

    def test_implemented_retried_flag_consistent(self) -> None:
        for cap in self.implemented:
            self.assertTrue(
                cap.retried,
                f"IMPLEMENTED {cap.module}/{cap.endpoint} retried is False",
            )

    def test_implemented_rate_limited_flag_consistent(self) -> None:
        for cap in self.implemented:
            self.assertTrue(
                cap.rate_limited,
                f"IMPLEMENTED {cap.module}/{cap.endpoint} rate_limited is False",
            )

    def test_implemented_matrix_entries_have_wrapper(self) -> None:
        for entry in self.matrix_implemented:
            self.assertIsNotNone(
                entry["siglab_wrapper"],
                f"IMPLEMENTED {entry['doc_module']}/{entry['endpoint']} has no wrapper",
            )
            self.assertIsInstance(entry["siglab_wrapper"], str)
            self.assertNotEqual(entry["siglab_wrapper"], "")

    def test_used_by_strategy_is_boolean_for_implemented(self) -> None:
        for cap in self.implemented:
            self.assertIsInstance(cap.used_by_strategy, bool)

    def test_implemented_endpoints_contain_slash(self) -> None:
        for cap in self.implemented:
            self.assertIn("/", cap.endpoint, f"Endpoint {cap.endpoint!r} lacks slash")


class BlockedCapabilityTests(unittest.TestCase):
    blocked: list[SoSoValueCapability]
    matrix_blocked: list[dict[str, object]]

    def setUp(self) -> None:
        self.blocked = [c for c in CAPABILITIES if c.status == "BLOCKED"]
        self.matrix_blocked = [
            e for e in capability_matrix() if e["status"] == "BLOCKED"
        ]

    def test_blocked_count(self) -> None:
        self.assertEqual(len(self.blocked), 10)

    def test_blocked_have_none_wrapper(self) -> None:
        for cap in self.blocked:
            self.assertIsNone(cap.wrapper)

    def test_blocked_have_nonempty_reason(self) -> None:
        for cap in self.blocked:
            self.assertTrue(
                cap.reason, f"BLOCKED {cap.module}/{cap.endpoint} has empty reason"
            )

    def test_blocked_all_flags_false(self) -> None:
        for cap in self.blocked:
            self.assertFalse(cap.tested, f"{cap.module} tested is not False")
            self.assertFalse(cap.cached, f"{cap.module} cached is not False")
            self.assertFalse(cap.retried, f"{cap.module} retried is not False")
            self.assertFalse(
                cap.rate_limited, f"{cap.module} rate_limited is not False"
            )
            self.assertFalse(
                cap.used_by_strategy,
                f"{cap.module} used_by_strategy is not False",
            )


class CapabilityMatrixNoDuplicatesTests(unittest.TestCase):
    def test_no_duplicate_module_endpoint_pairs(self) -> None:
        seen: set[tuple[str, str]] = set()
        for cap in CAPABILITIES:
            pair = (cap.module, cap.endpoint)
            self.assertNotIn(pair, seen, f"Duplicate (module, endpoint): {pair}")
            seen.add(pair)

    def test_no_duplicate_wrappers_among_implemented(self) -> None:
        wrappers = [
            c.wrapper
            for c in CAPABILITIES
            if c.status == "IMPLEMENTED" and c.wrapper is not None
        ]
        self.assertEqual(len(wrappers), len(set(wrappers)))


class CapabilityModuleCategorizationTests(unittest.TestCase):
    def test_unique_modules_among_implemented(self) -> None:
        implemented_modules = {
            c.module for c in CAPABILITIES if c.status == "IMPLEMENTED"
        }
        self.assertGreaterEqual(len(implemented_modules), 3)
        self.assertIn("Currency & Pairs", implemented_modules)
        self.assertIn("Feeds", implemented_modules)
        self.assertIn("ETF", implemented_modules)

    def test_blocked_modules_are_distinct_categories(self) -> None:
        blocked_modules = {c.module for c in CAPABILITIES if c.status == "BLOCKED"}
        expected_areas = {
            "Currency & Pairs",
            "ETF",
            "Feeds",
            "SoSoValue Index",
            "Crypto Stocks",
            "BTC Treasuries",
            "Fundraising",
            "Macro",
            "Analysis Charts",
        }
        self.assertEqual(blocked_modules, expected_areas)

    def test_all_capabilities_have_module(self) -> None:
        for cap in CAPABILITIES:
            self.assertTrue(
                cap.module, f"Capability at endpoint {cap.endpoint} has empty module"
            )

    def test_all_capabilities_have_endpoint(self) -> None:
        for cap in CAPABILITIES:
            self.assertTrue(
                cap.endpoint,
                f"Capability in module {cap.module} has empty endpoint",
            )


class CapabilityStatusInvariantsTests(unittest.TestCase):
    def test_only_valid_status_values(self) -> None:
        for cap in CAPABILITIES:
            self.assertIn(
                cap.status,
                {"IMPLEMENTED", "BLOCKED"},
                f"Unexpected status {cap.status!r} for {cap.module}",
            )

    def test_all_reasons_are_nonempty(self) -> None:
        for cap in CAPABILITIES:
            self.assertTrue(
                cap.reason,
                f"Capability {cap.module}/{cap.endpoint} has empty reason",
            )

    def test_status_counts_sum_to_total(self) -> None:
        total = len(CAPABILITIES)
        implemented = sum(1 for c in CAPABILITIES if c.status == "IMPLEMENTED")
        blocked = sum(1 for c in CAPABILITIES if c.status == "BLOCKED")
        self.assertEqual(implemented + blocked, total)

    def test_endpoint_field_is_consistent_with_status(self) -> None:
        for cap in CAPABILITIES:
            if cap.status == "IMPLEMENTED":
                self.assertIn("/", cap.endpoint, f"IMPLEMENTED endpoint {cap.endpoint!r} lacks slash")
            elif cap.status == "BLOCKED":
                self.assertFalse(
                    cap.endpoint.startswith(("POST /openapi")),
                    f"BLOCKED endpoint {cap.endpoint!r} looks like an already-wired API path",
                )

    def test_wrapper_presence_is_consistent_with_status(self) -> None:
        for cap in CAPABILITIES:
            if cap.status == "IMPLEMENTED":
                self.assertIsNotNone(cap.wrapper)
            else:
                self.assertIsNone(cap.wrapper)


if __name__ == "__main__":
    unittest.main()
