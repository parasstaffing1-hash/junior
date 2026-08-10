import pandas as pd
from typing import Dict, Any
from pathlib import Path

from .base import BaseImporter
from app.errors import AppError

class ParquetImporter(BaseImporter):
    def inspect(self, path: Path, **kwargs) -> Dict[str, Any]:
        """
        Inspect a Parquet file.
        """
        try:
            # We can use pyarrow directly to get metadata efficiently, but for simplicity we load it via pandas
            import pyarrow.parquet as pq
            
            parquet_file = pq.ParquetFile(path)
            schema = parquet_file.schema
            
            return {
                "file_type": "parquet",
                "row_count": parquet_file.metadata.num_rows,
                "column_count": len(schema.names),
                "columns": schema.names
            }
        except Exception as e:
            raise AppError("INVALID_PARQUET", f"Malformed Parquet file: {str(e)}", status_code=400)
    
    def load_to_dataframe(self, path: Path, **kwargs) -> pd.DataFrame:
        try:
            # Pandas preserves types natively from Parquet
            df = pd.read_parquet(path)
            return df
        except Exception as e:
            raise AppError("INVALID_PARQUET", f"Failed to load Parquet: {str(e)}", status_code=400)
