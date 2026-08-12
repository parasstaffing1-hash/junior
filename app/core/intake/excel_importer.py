import pandas as pd
from typing import Dict, Any
from pathlib import Path
import warnings

from .base import BaseImporter
from app.errors import AppError

class ExcelImporter(BaseImporter):
    def inspect(self, path: Path, **kwargs) -> Dict[str, Any]:
        """
        Inspect an Excel workbook.
        Returns sheet names and dimensions.
        """
        try:
            sheets_info = []
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with pd.ExcelFile(path) as xl:
                    for sheet_name in xl.sheet_names:
                        df = xl.parse(sheet_name)
                        sheets_info.append({
                            "name": sheet_name,
                            "rows": len(df),
                            "columns": len(df.columns)
                        })
                
            return {
                "file_type": "excel",
                "requires_selection": len(sheets_info) > 1,
                "can_combine_sheets": len(sheets_info) > 1,
                "combine_mode": "union_columns_with_source_sheet" if len(sheets_info) > 1 else None,
                "sheets": sheets_info
            }
        except Exception as e:
            raise AppError("INVALID_EXCEL_FILE", f"Failed to parse Excel file: {str(e)}", status_code=400)
    
    def load_to_dataframe(self, path: Path, **kwargs) -> pd.DataFrame:
        sheet_name = kwargs.get('sheet_name')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with pd.ExcelFile(path) as xl:
                    if sheet_name == "__ALL_SHEETS__":
                        frames = []
                        source_column = "_source_sheet"
                        if any("_source_sheet" in (xl.parse(name, nrows=0).columns) for name in xl.sheet_names):
                            source_column = "__source_sheet"
                        for name in xl.sheet_names:
                            sheet = xl.parse(name)
                            if sheet.empty and len(sheet.columns) == 0:
                                continue
                            sheet = sheet.copy()
                            sheet[source_column] = str(name)
                            frames.append(sheet)
                        if not frames:
                            raise AppError("EXCEL_EMPTY_WORKBOOK", "The workbook contains no usable rows.", status_code=400)
                        return pd.concat(frames, ignore_index=True, sort=False)
                    if not sheet_name:
                        if len(xl.sheet_names) == 1:
                            sheet_name = xl.sheet_names[0]
                        else:
                            raise AppError("EXCEL_SHEET_REQUIRED", "Workbook contains multiple sheets. Please specify a sheet_name.", status_code=400)
                    
                    if sheet_name not in xl.sheet_names:
                        raise AppError("EXCEL_SHEET_NOT_FOUND", f"Sheet '{sheet_name}' not found in workbook.", status_code=400)
                        
                    df = xl.parse(sheet_name)
            
            return df
        except AppError:
            raise
        except Exception as e:
            raise AppError("INVALID_EXCEL_FILE", f"Failed to load Excel sheet: {str(e)}", status_code=400)
