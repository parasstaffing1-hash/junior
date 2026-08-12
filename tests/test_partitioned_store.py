from __future__ import annotations

import pandas as pd

from app.core.storage.partitioned_store import PartitionedParquetStore


def test_partitioned_parquet_store_writes_and_scans(tmp_path):
    store = PartitionedParquetStore(tmp_path)
    manifest = store.write(pd.DataFrame({"date": ["2026-01", "2026-02", "2026-01"], "value": [1, 2, 3]}), dataset_id="sales", partition_column="date")
    assert manifest["format"] == "parquet"
    assert manifest["file_count"] >= 2
    result = store.scan(manifest["relative_path"], filters=[("date", "==", "2026-01")])
    assert sorted(result["value"].tolist()) == [1, 3]
