import pandas as pd
from typing import Dict, Any
from pathlib import Path
import json

from .base import BaseImporter
from app.errors import AppError

class JSONImporter(BaseImporter):
    def inspect(self, path: Path, **kwargs) -> Dict[str, Any]:
        """
        Inspect a JSON or JSONL file.
        """
        try:
            # We just do a quick read of the first chunk to see if it's JSONL or JSON Array
            with open(path, 'r', encoding='utf-8') as f:
                first_char = f.read(1)
            
            is_jsonl = False
            if first_char == '{':
                is_jsonl = True
                
            df = self.load_to_dataframe(path, is_jsonl=is_jsonl)
            
            return {
                "file_type": "json",
                "format": "jsonl" if is_jsonl else "json_array",
                "row_count": len(df),
                "column_count": len(df.columns),
                "columns": list(df.columns)
            }
        except Exception as e:
            raise AppError("INVALID_JSON", f"Malformed JSON: {str(e)}", status_code=400)
    
    def load_to_dataframe(self, path: Path, **kwargs) -> pd.DataFrame:
        try:
            is_jsonl = kwargs.get('is_jsonl')
            if is_jsonl is None:
                with open(path, 'r', encoding='utf-8') as f:
                    first_char = f.read(1)
                is_jsonl = first_char == '{'
                
            if is_jsonl:
                df = pd.read_json(path, lines=True)
            else:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Check for nesting and normalize shallow structures
                if isinstance(data, dict):
                    # Sometimes JSON is {"data": [...]}
                    keys = list(data.keys())
                    if len(keys) == 1 and isinstance(data[keys[0]], list):
                        data = data[keys[0]]
                    else:
                        data = [data] # Wrap in list

                df = pd.json_normalize(data)
            
            return df
        except Exception as e:
            raise AppError("INVALID_JSON", f"Failed to parse JSON into dataframe: {str(e)}", status_code=400)
