from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from siglab.data.store import ParquetLake


class ParquetLakeInitTests(unittest.TestCase):
    def test_creates_root_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "lake"
            self.assertFalse(root.exists())
            ParquetLake(root)
            self.assertTrue(root.is_dir())

    def test_uses_existing_root_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            self.assertEqual(lake.root, root)
            self.assertTrue(root.is_dir())

    def test_stores_root_attribute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data_lake"
            lake = ParquetLake(root)
            self.assertEqual(lake.root, root)


class ParquetLakeSanitizeTests(unittest.TestCase):
    def setUp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.lake = ParquetLake(Path(tmp))

    def test_keeps_alphanumeric_chars(self) -> None:
        result = self.lake._sanitize("hello123")
        self.assertEqual(result, "hello123")

    def test_replaces_special_chars_with_underscore(self) -> None:
        result = self.lake._sanitize("hello world")
        self.assertEqual(result, "hello_world")

    def test_replaces_multiple_special_chars(self) -> None:
        result = self.lake._sanitize("a/b/c")
        self.assertEqual(result, "a_b_c")

    def test_keeps_dots_and_hyphens(self) -> None:
        result = self.lake._sanitize("test-file.name_v2")
        self.assertEqual(result, "test-file.name_v2")

    def test_strips_leading_and_trailing_underscores(self) -> None:
        result = self.lake._sanitize("__hello__")
        self.assertEqual(result, "hello")

    def test_returns_default_when_result_is_empty(self) -> None:
        result = self.lake._sanitize("!!!")
        self.assertEqual(result, "default")

    def test_handles_unicode_characters(self) -> None:
        result = self.lake._sanitize("café")
        self.assertEqual(result, "caf")

    def test_handles_empty_string(self) -> None:
        result = self.lake._sanitize("")
        self.assertEqual(result, "default")


class ParquetLakeTimestampTests(unittest.TestCase):
    def setUp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.lake = ParquetLake(Path(tmp))

    def test_timestamp_matches_expected_format(self) -> None:
        ts = self.lake._timestamp()
        self.assertEqual(len(ts), 16)
        self.assertEqual(ts[8], "T")
        self.assertEqual(ts[-1], "Z")
        digits = ts[:8] + ts[9:15]
        self.assertTrue(digits.isdigit())

    def test_timestamp_contains_todays_date(self) -> None:
        ts = self.lake._timestamp()
        date_part = ts[:8]
        from datetime import UTC, datetime

        today = datetime.now(UTC).strftime("%Y%m%d")
        self.assertEqual(date_part, today)


class ParquetLakeTargetDirTests(unittest.TestCase):
    def test_joins_sanitized_namespace_and_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target = lake._target_dir("my namespace", "my-key")
            expected = root / "my_namespace" / "my-key"
            self.assertEqual(target, expected)

    def test_sanitizes_both_parts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target = lake._target_dir("ns/1", "key/2")
            expected = root / "ns_1" / "key_2"
            self.assertEqual(target, expected)


class ParquetLakeLatestPathTests(unittest.TestCase):
    def test_returns_none_when_directory_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            result = lake._latest_path("nonexistent", "key", ".json")
            self.assertIsNone(result)

    def test_returns_none_when_no_matching_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            (target_dir / "data.txt").write_text("hello")
            result = lake._latest_path("ns", "key", ".json")
            self.assertIsNone(result)

    def test_returns_latest_file_by_sort_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            older = target_dir / "20260501T000000Z.json"
            older.write_text("old")
            newer = target_dir / "20260530T000000Z.json"
            newer.write_text("new")
            result = lake._latest_path("ns", "key", ".json")
            self.assertEqual(result, newer)

    def test_returns_file_when_within_max_age(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            recent = target_dir / "data.json"
            recent.write_text("recent")
            result = lake._latest_path("ns", "key", ".json", max_age_hours=48)
            self.assertEqual(result, recent)

    def test_returns_none_when_file_exceeds_max_age(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            old_file = target_dir / "data.json"
            old_file.write_text("old")
            old_mtime = time.time() - 72 * 3600
            os.utime(old_file, (old_mtime, old_mtime))
            result = lake._latest_path("ns", "key", ".json", max_age_hours=24)
            self.assertIsNone(result)

    def test_prefers_newer_file_over_older_one_respecting_age(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            old_file = target_dir / "20260501T000000Z.json"
            old_file.write_text("old")
            old_mtime = time.time() - 72 * 3600
            os.utime(old_file, (old_mtime, old_mtime))
            recent_file = target_dir / "20260530T000000Z.json"
            recent_file.write_text("recent")
            result = lake._latest_path("ns", "key", ".json", max_age_hours=48)
            self.assertEqual(result, recent_file)


class ParquetLakeWriteFrameTests(unittest.TestCase):
    @patch.object(pd.DataFrame, "to_parquet")
    def test_write_frame_returns_path_with_parquet_suffix(
        self, mock_to_parquet: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            df = pd.DataFrame({"col": [1, 2, 3]})
            result = lake.write_frame("ns", "key", df)
            self.assertIsInstance(result, Path)
            self.assertEqual(result.suffix, ".parquet")

    @patch.object(pd.DataFrame, "to_parquet")
    def test_write_frame_creates_target_directory(
        self, mock_to_parquet: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            df = pd.DataFrame({"x": [42]})
            lake.write_frame("my-ns", "my-key", df)
            expected_dir = root / "my-ns" / "my-key"
            self.assertTrue(expected_dir.is_dir())

    @patch.object(pd.DataFrame, "to_parquet")
    def test_write_frame_passes_frame_to_parquet(
        self, mock_to_parquet: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            df = pd.DataFrame({"val": [10, 20]})
            lake.write_frame("ns", "key", df)
            mock_to_parquet.assert_called_once()

    @patch.object(pd.DataFrame, "to_parquet")
    def test_write_frame_path_includes_namespace_and_key(
        self, mock_to_parquet: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            df = pd.DataFrame({"a": [1]})
            result = lake.write_frame("alpha", "beta", df)
            self.assertIn("alpha", str(result))
            self.assertIn("beta", str(result))


class ParquetLakeLatestFrameTests(unittest.TestCase):
    @patch("pandas.read_parquet")
    def test_latest_frame_returns_dataframe_when_file_exists(
        self, mock_read_parquet: MagicMock
    ) -> None:
        expected_df = pd.DataFrame({"a": [1, 2]})
        mock_read_parquet.return_value = expected_df
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            (target_dir / "data.parquet").write_text("")
            result = lake.latest_frame("ns", "key")
            self.assertIsNotNone(result)
            pd.testing.assert_frame_equal(result, expected_df)

    def test_latest_frame_returns_none_when_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            result = lake.latest_frame("ns", "key")
            self.assertIsNone(result)

    def test_latest_frame_returns_none_when_directory_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            result = lake.latest_frame("nonexistent", "key")
            self.assertIsNone(result)

    @patch("pandas.read_parquet")
    def test_latest_frame_respects_max_age_and_returns_none(
        self, mock_read_parquet: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            f = target_dir / "data.parquet"
            f.write_text("")
            old_mtime = time.time() - 72 * 3600
            os.utime(f, (old_mtime, old_mtime))
            result = lake.latest_frame("ns", "key", max_age_hours=24)
            self.assertIsNone(result)
            mock_read_parquet.assert_not_called()

    @patch("pandas.read_parquet")
    def test_latest_frame_calls_read_parquet_with_correct_path(
        self, mock_read_parquet: MagicMock
    ) -> None:
        mock_read_parquet.return_value = pd.DataFrame({"x": [1]})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            f = target_dir / "data.parquet"
            f.write_text("")
            lake.latest_frame("ns", "key")
            mock_read_parquet.assert_called_once_with(f)


class ParquetLakeWriteJsonTests(unittest.TestCase):
    def test_write_json_returns_path_with_json_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            result = lake.write_json("ns", "key", {"msg": "hello"})
            self.assertIsInstance(result, Path)
            self.assertEqual(result.suffix, ".json")

    def test_write_json_creates_target_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            lake.write_json("my-ns", "my-key", {"data": 1})
            expected_dir = root / "my-ns" / "my-key"
            self.assertTrue(expected_dir.is_dir())

    def test_write_json_writes_serializable_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            payload = {"name": "test", "value": 42, "tags": ["a", "b"]}
            result = lake.write_json("ns", "key", payload)
            self.assertTrue(result.exists())
            content = result.read_text()
            self.assertIn("test", content)
            self.assertIn("42", content)

    def test_write_json_path_includes_namespace_and_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            result = lake.write_json("alpha", "beta", {"k": "v"})
            self.assertIn("alpha", str(result))
            self.assertIn("beta", str(result))

    def test_write_json_handles_list_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            payload = [1, 2, 3]
            result = lake.write_json("ns", "key", payload)
            self.assertTrue(result.exists())

    def test_write_json_handles_none_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            result = lake.write_json("ns", "key", None)
            self.assertTrue(result.exists())
            self.assertEqual(result.read_text().strip(), "null")

    def test_write_json_handles_nested_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            payload = {"outer": {"inner": [1, {"deep": True}]}}
            result = lake.write_json("ns", "key", payload)
            self.assertTrue(result.exists())
            import json
            loaded = json.loads(result.read_text())
            self.assertEqual(loaded, payload)


class ParquetLakeLatestJsonTests(unittest.TestCase):
    def test_latest_json_returns_data_when_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            (target_dir / "data.json").write_text('{"msg": "hello"}')
            result = lake.latest_json("ns", "key")
            self.assertEqual(result, {"msg": "hello"})

    def test_latest_json_returns_none_when_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            result = lake.latest_json("ns", "key")
            self.assertIsNone(result)

    def test_latest_json_returns_none_when_directory_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            result = lake.latest_json("nonexistent", "key")
            self.assertIsNone(result)

    def test_latest_json_returns_latest_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            (target_dir / "20260501T000000Z.json").write_text('"old"')
            (target_dir / "20260530T000000Z.json").write_text('"new"')
            result = lake.latest_json("ns", "key")
            self.assertEqual(result, "new")

    def test_latest_json_respects_max_age_and_returns_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            f = target_dir / "data.json"
            f.write_text('{"recent": true}')
            result = lake.latest_json("ns", "key", max_age_hours=48)
            self.assertEqual(result, {"recent": True})

    def test_latest_json_respects_max_age_and_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            f = target_dir / "data.json"
            f.write_text('{"old": true}')
            old_mtime = time.time() - 72 * 3600
            os.utime(f, (old_mtime, old_mtime))
            result = lake.latest_json("ns", "key", max_age_hours=24)
            self.assertIsNone(result)

    def test_latest_json_handles_empty_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            (target_dir / "data.json").write_text("{}")
            result = lake.latest_json("ns", "key")
            self.assertEqual(result, {})

    def test_latest_json_handles_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            (target_dir / "data.json").write_text("[1, 2, 3]")
            result = lake.latest_json("ns", "key")
            self.assertEqual(result, [1, 2, 3])

    def test_latest_json_handles_primitive_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            (target_dir / "data.json").write_text('"just a string"')
            result = lake.latest_json("ns", "key")
            self.assertEqual(result, "just a string")


class ParquetLakeRoundTripTests(unittest.TestCase):
    @patch("pandas.read_parquet")
    def test_write_then_read_frame_roundtrip(
        self,
        mock_read_parquet: MagicMock,
    ) -> None:
        original_df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        mock_read_parquet.return_value = original_df
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            lake.write_frame("ns", "key", original_df)
            target_dir = root / "ns" / "key"
            dummy = target_dir / "20260530T120000Z.parquet"
            dummy.write_text("")
            result = lake.latest_frame("ns", "key")
            pd.testing.assert_frame_equal(result, original_df)

    def test_write_then_read_json_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            original = {"key": "value", "nested": {"num": 42}}
            lake.write_json("ns", "key", original)
            result = lake.latest_json("ns", "key")
            self.assertEqual(result, original)

    def test_multiple_writes_in_same_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            lake.write_json("shared", "a", {"id": 1})
            lake.write_json("shared", "b", {"id": 2})
            self.assertTrue((root / "shared" / "a").is_dir())
            self.assertTrue((root / "shared" / "b").is_dir())
            self.assertEqual(lake.latest_json("shared", "a"), {"id": 1})
            self.assertEqual(lake.latest_json("shared", "b"), {"id": 2})


class ParquetLakeCrossNamespaceTests(unittest.TestCase):
    def test_different_namespaces_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            lake.write_json("ns1", "key", {"from": "ns1"})
            lake.write_json("ns2", "key", {"from": "ns2"})
            self.assertEqual(lake.latest_json("ns1", "key"), {"from": "ns1"})
            self.assertEqual(lake.latest_json("ns2", "key"), {"from": "ns2"})


class ParquetLakePruneTests(unittest.TestCase):
    def test_prune_removes_old_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            lake.write_json("ns", "key", {"data": 1})
            target_dir = lake._target_dir("ns", "key")
            files_before = list(target_dir.glob("*"))
            self.assertEqual(len(files_before), 1)
            old_mtime = time.time() - 72 * 3600
            os.utime(files_before[0], (old_mtime, old_mtime))
            removed = lake.prune("ns", "key", max_age_hours=24)
            self.assertEqual(removed, 1)
            files_after = list(target_dir.glob("*"))
            self.assertEqual(len(files_after), 0)

    def test_prune_keeps_recent_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            lake.write_json("ns", "key", {"data": 1})
            target_dir = lake._target_dir("ns", "key")
            removed = lake.prune("ns", "key", max_age_hours=24)
            self.assertEqual(removed, 0)
            files_after = list(target_dir.glob("*"))
            self.assertEqual(len(files_after), 1)

    def test_prune_returns_zero_when_directory_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            removed = lake.prune("nonexistent", "key", max_age_hours=24)
            self.assertEqual(removed, 0)

    def test_prune_only_removes_cache_file_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            target_dir = root / "ns" / "key"
            target_dir.mkdir(parents=True)
            keep_file = target_dir / "some_other.txt"
            keep_file.write_text("keep me")
            old_file = target_dir / "old_data.json"
            old_file.write_text("old")
            old_mtime = time.time() - 72 * 3600
            os.utime(old_file, (old_mtime, old_mtime))
            removed = lake.prune("ns", "key", max_age_hours=24)
            self.assertEqual(removed, 1)
            self.assertTrue(keep_file.exists())
            self.assertFalse(old_file.exists())

    def test_prune_removes_multiple_old_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            for i in range(5):
                f = lake._target_dir("ns", "key") / f"20260501T{i:06d}Z.json"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(str(i))
            old_mtime = time.time() - 72 * 3600
            for f in lake._target_dir("ns", "key").glob("*.json"):
                os.utime(f, (old_mtime, old_mtime))
            removed = lake.prune("ns", "key", max_age_hours=24)
            self.assertEqual(removed, 5)

    def test_prune_mixed_age_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            target_dir = lake._target_dir("ns", "key")
            target_dir.mkdir(parents=True, exist_ok=True)
            recent = target_dir / "recent.json"
            recent.write_text("recent")
            old = target_dir / "old.json"
            old.write_text("old")
            old_mtime = time.time() - 72 * 3600
            os.utime(old, (old_mtime, old_mtime))
            removed = lake.prune("ns", "key", max_age_hours=24)
            self.assertEqual(removed, 1)
            self.assertTrue(recent.exists())
            self.assertFalse(old.exists())

    def test_prune_all_removes_old_files_in_all_namespaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            lake.write_json("ns1", "k1", {"a": 1})
            lake.write_json("ns2", "k2", {"b": 2})
            target1 = lake._target_dir("ns1", "k1")
            target2 = lake._target_dir("ns2", "k2")
            old_mtime = time.time() - 72 * 3600
            for f in list(target1.glob("*")) + list(target2.glob("*")):
                os.utime(f, (old_mtime, old_mtime))
            total = lake.prune_all(default_max_age_hours=24)
            self.assertEqual(total, 2)
            self.assertEqual(len(list(target1.glob("*"))), 0)
            self.assertEqual(len(list(target2.glob("*"))), 0)

    def test_prune_all_skips_namespaces_younger_than_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            lake.write_json("ns_old", "k", {"old": True})
            lake.write_json("ns_fresh", "k", {"fresh": True})
            target_old = lake._target_dir("ns_old", "k")
            old_mtime = time.time() - 72 * 3600
            for f in target_old.glob("*"):
                os.utime(f, (old_mtime, old_mtime))
            total = lake.prune_all(default_max_age_hours=24)
            self.assertEqual(total, 1)
            self.assertEqual(
                len(list(lake._target_dir("ns_old", "k").glob("*"))), 0
            )
            self.assertEqual(
                len(list(lake._target_dir("ns_fresh", "k").glob("*"))), 1
            )

    def test_prune_all_with_empty_lake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            total = lake.prune_all(default_max_age_hours=24)
            self.assertEqual(total, 0)

    def test_prune_all_with_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake = ParquetLake(root)
            (root / "ns" / "key").mkdir(parents=True)
            total = lake.prune_all(default_max_age_hours=24)
            self.assertEqual(total, 0)

    def test_prune_respects_namespace_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            lake.write_json("alice", "portfolio", {"balance": 100})
            lake.write_json("bob", "portfolio", {"balance": 200})
            target_alice = lake._target_dir("alice", "portfolio")
            old_mtime = time.time() - 72 * 3600
            for f in target_alice.glob("*"):
                os.utime(f, (old_mtime, old_mtime))
            removed = lake.prune("alice", "portfolio", max_age_hours=24)
            self.assertEqual(removed, 1)
            self.assertIsNotNone(lake.latest_json("bob", "portfolio"))
            self.assertEqual(lake.latest_json("bob", "portfolio"), {"balance": 200})

    def test_prune_works_with_parquet_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = ParquetLake(Path(tmp))
            target_dir = lake._target_dir("ns", "key")
            target_dir.mkdir(parents=True, exist_ok=True)
            pf = target_dir / "data.parquet"
            pf.write_text("dummy parquet")
            old_mtime = time.time() - 72 * 3600
            os.utime(pf, (old_mtime, old_mtime))
            removed = lake.prune("ns", "key", max_age_hours=24)
            self.assertEqual(removed, 1)
            self.assertFalse(pf.exists())


if __name__ == "__main__":
    unittest.main()
