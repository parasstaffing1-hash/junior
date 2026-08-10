from typing import Dict, Type
from .base import BaseImporter
from .csv_importer import CSVImporter
from .excel_importer import ExcelImporter
from .json_importer import JSONImporter
from .parquet_importer import ParquetImporter

class ImporterRegistry:
    def __init__(self):
        self._importers: Dict[str, BaseImporter] = {
            'csv': CSVImporter(),
            'delimited': CSVImporter(),
            'excel': ExcelImporter(),
            'json': JSONImporter(),
            'parquet': ParquetImporter()
        }

    def get_importer(self, file_type: str) -> BaseImporter:
        importer = self._importers.get(file_type)
        if not importer:
            raise ValueError(f"No importer registered for file type: {file_type}")
        return importer

registry = ImporterRegistry()
