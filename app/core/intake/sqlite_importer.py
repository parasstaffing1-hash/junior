import pandas as pd
from typing import Dict, Any
from pathlib import Path
import sqlite3

from .base import BaseImporter
from app.errors import AppError

class SQLiteImporter(BaseImporter):
    def inspect(self, path: Path, **kwargs) -> Dict[str, Any]:
        """
        Inspect an SQLite file to find the primary data table.
        """
        try:
            conn = sqlite3.connect(path)
            cursor = conn.cursor()
            
            # Find all tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]
            
            if not tables:
                raise ValueError("No tables found in SQLite database.")
                
            # For simplicity, if there's only one table, use it.
            # If there are multiple, we'll pick the largest one.
            largest_table = None
            max_rows = -1
            
            for table in tables:
                try:
                    cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                    rows = cursor.fetchone()[0]
                    if rows > max_rows:
                        max_rows = rows
                        largest_table = table
                except Exception:
                    pass
            
            if not largest_table:
                raise ValueError("Could not read any tables in the database.")
                
            # Get columns for the largest table
            cursor.execute(f'PRAGMA table_info("{largest_table}");')
            columns = [row[1] for row in cursor.fetchall()]
            
            conn.close()
            
            return {
                "file_type": "sqlite",
                "format": "sqlite",
                "table_name": largest_table,
                "row_count": max_rows,
                "column_count": len(columns),
                "columns": columns,
                "tables_available": tables
            }
        except Exception as e:
            raise AppError("INVALID_SQLITE", f"Failed to inspect SQLite database: {str(e)}", status_code=400)
    
    def load_to_dataframe(self, path: Path, **kwargs) -> pd.DataFrame:
        try:
            table_name = kwargs.get('table_name')
            conn = sqlite3.connect(path)
            
            if not table_name:
                # Find largest table
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = [row[0] for row in cursor.fetchall()]
                if not tables:
                    raise ValueError("No tables found.")
                largest_table = tables[0]
                max_rows = -1
                for table in tables:
                    try:
                        cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                        rows = cursor.fetchone()[0]
                        if rows > max_rows:
                            max_rows = rows
                            largest_table = table
                    except Exception:
                        pass
                table_name = largest_table
                
            df = pd.read_sql_query(f'SELECT * FROM "{table_name}"', conn)
            conn.close()
            
            return df
        except Exception as e:
            raise AppError("INVALID_SQLITE", f"Failed to parse SQLite table into dataframe: {str(e)}", status_code=400)
