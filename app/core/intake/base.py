from abc import ABC, abstractmethod
from typing import Dict, Any
from pathlib import Path
import pandas as pd

class BaseImporter(ABC):
    """
    Base class for all dataset format importers.
    """
    
    @abstractmethod
    def inspect(self, path: Path, **kwargs) -> Dict[str, Any]:
        """
        Inspect the file and return metadata.
        For CSV: row count, column count, delimiter.
        For Excel: number of sheets, sheet names, rows/cols per sheet.
        """
        pass
    
    @abstractmethod
    def load_to_dataframe(self, path: Path, **kwargs) -> pd.DataFrame:
        """
        Load the file (or a specific sheet/subset of it) into a pandas DataFrame.
        """
        pass
