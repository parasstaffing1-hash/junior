"""Dataset-version storage with path containment and atomic dataframe writes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd


class StoragePathError(ValueError):
    """Raised when a storage key attempts to escape the configured root."""


class DatasetStorage:
    """Owns filesystem access for canonical, versioned dataset files.

    The API stores only relative keys (for example ``<dataset-id>/v2.csv``).
    Resolving a key always verifies containment below ``root`` so callers cannot
    accidentally turn a database value into arbitrary file access.
    """

    def __init__(self, *, root: str | Path, max_upload_bytes: int) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = int(max_upload_bytes)
        if self.max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be greater than zero.")

    def resolve(self, storage_key: str | Path) -> Path:
        key = Path(storage_key)
        if not str(storage_key) or key.is_absolute():
            raise StoragePathError("Storage keys must be non-empty relative paths.")
        path = (self.root / key).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise StoragePathError("Storage path escapes the configured root.") from exc
        return path

    def ensure_upload_size(self, size_bytes: int) -> None:
        if int(size_bytes) > self.max_upload_bytes:
            raise ValueError(f"Upload exceeds the configured {self.max_upload_bytes} byte limit.")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def store_dataframe(self, dataframe: pd.DataFrame, dataset_id: str, version_number: int) -> dict[str, Any]:
        """Atomically persist a canonical CSV version and return immutable metadata."""
        if not isinstance(dataframe, pd.DataFrame):
            raise TypeError("dataframe must be a pandas DataFrame.")
        if not dataset_id or "/" in str(dataset_id) or "\\" in str(dataset_id):
            raise StoragePathError("dataset_id must not contain path separators.")
        if not isinstance(version_number, int) or version_number < 1:
            raise ValueError("version_number must be a positive integer.")

        storage_key = f"{dataset_id}/v{version_number}.csv"
        destination = self.resolve(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            dataframe.to_csv(temporary, index=False)
            temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

        return {
            "storage_key": storage_key,
            "sha256": self._sha256(destination),
            "size_bytes": destination.stat().st_size,
            "row_count": int(len(dataframe)),
            "column_count": int(len(dataframe.columns)),
        }
