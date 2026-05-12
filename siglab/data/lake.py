from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


class ParquetLake:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write_frame(self, namespace: str, key: str, frame: pd.DataFrame) -> Path:
        target_dir = self._target_dir(namespace, key)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{self._timestamp()}.parquet"
        frame.to_parquet(target)
        return target

    def latest_frame(
        self,
        namespace: str,
        key: str,
        *,
        max_age_hours: float | None = None,
    ) -> pd.DataFrame | None:
        latest = self._latest_path(namespace, key, ".parquet", max_age_hours=max_age_hours)
        if latest is None:
            return None
        return pd.read_parquet(latest)

    def write_json(self, namespace: str, key: str, payload: Any) -> Path:
        target_dir = self._target_dir(namespace, key)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{self._timestamp()}.json"
        target.write_text(json.dumps(payload, indent=2))
        return target

    def latest_json(
        self,
        namespace: str,
        key: str,
        *,
        max_age_hours: float | None = None,
    ) -> Any | None:
        latest = self._latest_path(namespace, key, ".json", max_age_hours=max_age_hours)
        if latest is None:
            return None
        return json.loads(latest.read_text())

    def _latest_path(
        self,
        namespace: str,
        key: str,
        suffix: str,
        *,
        max_age_hours: float | None = None,
    ) -> Path | None:
        target_dir = self._target_dir(namespace, key)
        if not target_dir.exists():
            return None
        matches = sorted(target_dir.glob(f"*{suffix}"))
        if not matches:
            return None
        latest = matches[-1]
        if max_age_hours is None:
            return latest
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        if datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC) < cutoff:
            return None
        return latest

    def _target_dir(self, namespace: str, key: str) -> Path:
        return self.root / self._sanitize(namespace) / self._sanitize(key)

    def _sanitize(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "default"

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
