from __future__ import annotations
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
import pandas as pd
logger = logging.getLogger(__name__)

class ParquetLake:

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write_frame(self, namespace: str, key: str, frame: pd.DataFrame) -> Path:
        target_dir = self._target_dir(namespace, key)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f'{self._timestamp()}.parquet'
        frame.to_parquet(target)
        return target

    def latest_frame(self, namespace: str, key: str, *, max_age_hours: float | None=None) -> pd.DataFrame | None:
        latest = self._latest_path(namespace, key, '.parquet', max_age_hours=max_age_hours)
        if latest is None:
            return None
        return pd.read_parquet(latest)

    def write_json(self, namespace: str, key: str, payload: object) -> Path:
        target_dir = self._target_dir(namespace, key)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f'{self._timestamp()}.json'
        target.write_text(json.dumps(payload, indent=2))
        return target

    def latest_json(self, namespace: str, key: str, *, max_age_hours: float | None=None) -> Any | None:
        latest = self._latest_path(namespace, key, '.json', max_age_hours=max_age_hours)
        if latest is None:
            return None
        return json.loads(latest.read_text())

    def _latest_path(self, namespace: str, key: str, suffix: str, *, max_age_hours: float | None=None) -> Path | None:
        target_dir = self._target_dir(namespace, key)
        if not target_dir.exists():
            return None
        matches = sorted(target_dir.glob(f'*{suffix}'))
        if not matches:
            return None
        latest = matches[-1]
        if max_age_hours is None:
            return latest
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        if datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC) < cutoff:
            return None
        return latest

    def prune(self, namespace: str, key: str, max_age_hours: float) -> int:
        """Remove cached files older than *max_age_hours* in a namespace/key."""
        target_dir = self._target_dir(namespace, key)
        return self._prune_dir(target_dir, max_age_hours)

    def prune_all(self, default_max_age_hours: float) -> int:
        """Remove cached files older than *default_max_age_hours* across"""
        total = 0
        if not self.root.is_dir():
            return 0
        for ns_dir in self.root.iterdir():
            if not ns_dir.is_dir():
                continue
            for key_dir in ns_dir.iterdir():
                if not key_dir.is_dir():
                    continue
                total += self._prune_dir(key_dir, default_max_age_hours)
        return total

    @staticmethod
    def _prune_dir(target_dir: Path, max_age_hours: float) -> int:
        """Remove stale ``.parquet`` / ``.json`` files under *target_dir*."""
        if not target_dir.is_dir():
            return 0
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        removed = 0
        for path in list(target_dir.iterdir()):
            if path.suffix not in ('.parquet', '.json'):
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            except OSError:
                logger.warning('Could not stat %s, skipping', path)
                continue
            if mtime < cutoff:
                path.unlink()
                removed += 1
        return removed

    def _target_dir(self, namespace: str, key: str) -> Path:
        return self.root / self._sanitize(namespace) / self._sanitize(key)

    def _sanitize(self, value: str) -> str:
        return re.sub('[^A-Za-z0-9_.-]+', '_', value).strip('_') or 'default'

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')