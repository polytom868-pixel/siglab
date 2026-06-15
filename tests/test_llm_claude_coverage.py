from __future__ import annotations

import unittest
from typing import Any, cast

from siglab.llm.claude import (
    BAI_CREDITS_PER_TOKEN,
    _compact_scalar,
    _estimate_bai_credits,
    _estimate_message_tokens,
    _int_or_zero,
    _json_clone,
)

class BaiRatesRemainingModelsTests(unittest.TestCase):
    def test_has_minimax_models(self) -> None:
        self.assertEqual(BAI_CREDITS_PER_TOKEN["minimax-m2.7"], (0.30, 1.20, 0.375, 0.06))
        self.assertEqual(BAI_CREDITS_PER_TOKEN["minimax-m2.5"], (0.30, 1.20, 0.30, 0.03))

    def test_has_kimi_k2_6(self) -> None:
        rates = BAI_CREDITS_PER_TOKEN["kimi-k2.6"]
        self.assertEqual(rates, (0.95, 4.00, 0.95, 0.16))

    def test_has_glm_models(self) -> None:
        self.assertEqual(BAI_CREDITS_PER_TOKEN["glm-5.1"], (1.40, 4.40, 1.40, 0.26))
        self.assertEqual(BAI_CREDITS_PER_TOKEN["glm-5"], (1.00, 3.20, 1.00, 0.20))

    def test_has_deepseek_v3_2_v4_pro(self) -> None:
        self.assertEqual(BAI_CREDITS_PER_TOKEN["deepseek-v3.2"], (0.29, 0.44, 0.29, 0.145))
        self.assertEqual(BAI_CREDITS_PER_TOKEN["deepseek-v4-pro"], (0.435, 0.87, 0.435, 0.004))

    def test_has_claude_sonnet_and_haiku_variants(self) -> None:
        self.assertEqual(BAI_CREDITS_PER_TOKEN["claude-sonnet-4-6"], (3.00, 15.00, 3.75, 0.30))
        self.assertEqual(BAI_CREDITS_PER_TOKEN["claude-haiku-4-5"], (1.00, 5.00, 1.25, 0.10))

    def test_has_claude_opus_dot_aliases(self) -> None:
        self.assertEqual(BAI_CREDITS_PER_TOKEN["claude-opus-4.7"], BAI_CREDITS_PER_TOKEN["claude-opus-4-7"])
        self.assertEqual(BAI_CREDITS_PER_TOKEN["claude-opus-4.6"], BAI_CREDITS_PER_TOKEN["claude-opus-4-6"])
        self.assertEqual(BAI_CREDITS_PER_TOKEN["claude-opus-4.5"], BAI_CREDITS_PER_TOKEN["claude-opus-4-5"])

    def test_has_gpt_5_4_and_5_2(self) -> None:
        self.assertEqual(BAI_CREDITS_PER_TOKEN["gpt-5.4"], (2.50, 15.00, 2.50, 0.25))
        self.assertEqual(BAI_CREDITS_PER_TOKEN["gpt-5.2"], (1.75, 14.00, 1.75, 0.175))
        self.assertEqual(BAI_CREDITS_PER_TOKEN["gpt-5.4-pro"], (30.00, 180.00, 30.00, 3.00))


class EstimateBaiCreditsCoverageTests(unittest.TestCase):
    def test_string_input_coerced_via_int(self) -> None:
        rates = (0.14, 0.28, 0.14, 0.003)
        credits = _estimate_bai_credits(input_tokens=500, output_tokens=200, rates=rates)
        self.assertAlmostEqual(credits, 500 * 0.14 + 200 * 0.28)

    def test_both_negative_clamps_to_zero(self) -> None:
        rates = (0.14, 0.28, 0.14, 0.003)
        credits = _estimate_bai_credits(input_tokens=-100, output_tokens=-200, rates=rates)
        self.assertEqual(credits, 0.0)

    def test_cache_rates_ignored_by_credit_estimate(self) -> None:
        rates = (0.14, 0.28, 9.99, 8.88)
        credits = _estimate_bai_credits(input_tokens=1000, output_tokens=500, rates=rates)
        self.assertAlmostEqual(credits, 1000 * 0.14 + 500 * 0.28)

    def test_float_input_truncated(self) -> None:
        rates = (0.14, 0.28, 0.14, 0.003)
        credits = _estimate_bai_credits(input_tokens=99.7, output_tokens=50, rates=rates)
        self.assertAlmostEqual(credits, 99 * 0.14 + 50 * 0.28)


class EstimateMessageTokensEdgeCasesTests(unittest.TestCase):
    def test_message_with_non_serializable_uses_default_str(self) -> None:
        msgs = [{"role": "user", "content": object()}]
        est = _estimate_message_tokens(msgs)
        self.assertGreater(est, 0)

    def test_three_messages_accumulate(self) -> None:
        msgs = [
            {"role": "system", "content": "a" * 100},
            {"role": "user", "content": "b" * 100},
            {"role": "assistant", "content": "c" * 100},
        ]
        est = _estimate_message_tokens(msgs)
        self.assertGreater(est, 70)


class IntOrZeroExtendedTests(unittest.TestCase):
    def test_string_float_returns_zero(self) -> None:
        self.assertEqual(_int_or_zero("3.14"), 0)

    def test_tuple_returns_zero(self) -> None:
        self.assertEqual(_int_or_zero((1, 2)), 0)

    def test_dict_returns_zero(self) -> None:
        self.assertEqual(_int_or_zero({"a": 1}), 0)

    def test_whitespace_string_returns_zero(self) -> None:
        self.assertEqual(_int_or_zero("   "), 0)

    def test_hex_string_returns_zero(self) -> None:
        self.assertEqual(_int_or_zero("0x10"), 0)

    def test_max_int(self) -> None:
        self.assertEqual(_int_or_zero(2**63 - 1), 2**63 - 1)

    def test_int_subclass(self) -> None:
        class MyInt(int):
            pass
        self.assertEqual(_int_or_zero(MyInt(7)), 7)

    def test_true_is_one(self) -> None:
        self.assertEqual(_int_or_zero(True), 1)

    def test_negative_bool_clamped(self) -> None:
        self.assertEqual(_int_or_zero(False), 0)


class CompactScalarEdgeCasesTests(unittest.TestCase):
    def test_string_at_2201_truncates_to_2200_with_ellipsis(self) -> None:
        s = "x" * 2201
        out = cast(str, _compact_scalar(s))
        self.assertEqual(len(out), 2200)
        self.assertTrue(out.endswith("…"))

    def test_string_with_trailing_whitespace_under_2200_passes(self) -> None:
        s = "  " + ("y" * 2198)
        out = cast(str, _compact_scalar(s))
        self.assertEqual(len(out), 2200)
        self.assertFalse(out.endswith("…"))

    def test_bytes_passthrough(self) -> None:
        b = b"hello"
        self.assertIs(_compact_scalar(b), b)

    def test_float_passthrough(self) -> None:
        self.assertEqual(_compact_scalar(3.14), 3.14)

    def test_tuple_passthrough(self) -> None:
        t = (1, 2, 3)
        self.assertIs(_compact_scalar(t), t)


class JsonCloneExtendedTests(unittest.TestCase):
    def test_nested_dict_independence(self) -> None:
        original: Any = {"outer": {"inner": {"deep": [1, 2, 3]}}}
        clone = _json_clone(original)
        original["outer"]["inner"]["deep"].append(99)
        self.assertEqual(clone["outer"]["inner"]["deep"], [1, 2, 3])

    def test_tuple_becomes_list(self) -> None:
        result = _json_clone((1, 2, 3))
        self.assertEqual(result, [1, 2, 3])

    def test_decimal_uses_default_str(self) -> None:
        from decimal import Decimal
        result = _json_clone({"value": Decimal("1.5")})
        self.assertIsInstance(result["value"], str)

    def test_set_uses_default_str(self) -> None:
        result = _json_clone({"items": {1, 2, 3}})
        self.assertIsInstance(result["items"], str)


if __name__ == "__main__":
    unittest.main()
