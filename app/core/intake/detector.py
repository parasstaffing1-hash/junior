import os
from pathlib import Path

def detect_file_type(path: Path, original_filename: str) -> str:
    """
    Detects the file type using file extensions and basic magic byte heuristics.
    Returns strings like 'csv', 'excel', 'json', 'parquet' or raises ValueError.
    """
    ext = original_filename.lower().split('.')[-1]
    
    # Check Magic Bytes
    try:
        with open(path, 'rb') as f:
            head = f.read(4)
    except Exception:
        head = b''

    if head.startswith(b'PAR1'):
        if ext != 'parquet':
            raise ValueError(f"File signature is Parquet but extension is .{ext}")
        return 'parquet'
        
    if head.startswith(b'PK\x03\x04') and ext == 'xlsx':
        # XLSX is a ZIP archive
        return 'excel'
        
    if head.startswith(b'\xd0\xcf\x11\xe0') and ext == 'xls':
        # OLE2 (Old Excel)
        return 'excel'

    if ext in ['csv', 'tsv', 'txt']:
        return 'delimited'
        
    if ext in ['json', 'jsonl', 'ndjson']:
        return 'json'

    # Fallback to extension if magic bytes are inconclusive (e.g. some CSVs might look like anything)
    if ext == 'parquet':
        return 'parquet'
    if ext in ['xlsx', 'xls']:
        return 'excel'

    raise ValueError(f"Unsupported file type for extension .{ext}")
