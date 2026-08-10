import uuid
from datetime import datetime, timezone
from fastapi import UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from pathlib import Path
import os
import shutil
from uuid import uuid4

from app.errors import AppError
from app.models.all import Dataset, DatasetVersion
from app.storage.dataset_storage import DatasetStorage
from app.core.intake.registry import registry
from app.core.intake.detector import detect_file_type


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

class DatasetImporter:
    def __init__(self, storage: DatasetStorage):
        self.storage = storage

    async def inspect(self, upload: UploadFile) -> dict:
        """
        Temporarily save the file, detect its type, and return inspection info.
        Used for multi-sheet Excel files.
        """
        temp_dir = Path(self.storage.root) / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(upload.filename or "upload").name
        temp_path = temp_dir / f"{uuid4().hex}_{safe_name}"

        try:
            await upload.seek(0)
            with open(temp_path, "wb") as buffer:
                shutil.copyfileobj(upload.file, buffer)
            
            try:
                file_type = detect_file_type(temp_path, upload.filename or safe_name)
                importer = registry.get_importer(file_type)
            except ValueError as exc:
                raise AppError("unsupported_file_type", str(exc), status_code=415) from exc
            
            inspection = await run_in_threadpool(importer.inspect, temp_path)
            inspection["file_type"] = file_type
            
            return inspection
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    async def import_file(self, db: Session, upload: UploadFile, **kwargs) -> dict:
        dataset_id = str(uuid.uuid4())
        original_filename = (upload.filename or "upload.csv")[:512]
        
        temp_dir = Path(self.storage.root) / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(original_filename).name
        temp_path = temp_dir / f"{uuid4().hex}_{safe_name}"

        try:
            await upload.seek(0)
            with open(temp_path, "wb") as buffer:
                shutil.copyfileobj(upload.file, buffer)
                
            try:
                file_type = detect_file_type(temp_path, original_filename)
                importer = registry.get_importer(file_type)
            except ValueError as exc:
                raise AppError("unsupported_file_type", str(exc), status_code=415) from exc
            
            # Use importer to load into a dataframe first
            df = await run_in_threadpool(importer.load_to_dataframe, temp_path, **kwargs)
            
            # Save Canonical Dataframe directly to storage as CSV/Parquet (let's use CSV for now to match old behavior)
            # We don't use `self.storage.store_upload` which copies the raw file. We convert to CSV first.
            version_number = 1
            dataset = Dataset(
                id=dataset_id,
                name=original_filename,
                source_type=file_type,
                original_filename=original_filename,
                created_at=utc_now(),
                updated_at=utc_now()
            )
            db.add(dataset)
            db.flush()

            storage_key = f"{dataset_id}/v{version_number}.csv"
            storage_path = Path(self.storage.root) / storage_key
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Canonical internal format is CSV
            df.to_csv(storage_path, index=False)
            
            import hashlib
            sha256 = hashlib.sha256(storage_path.read_bytes()).hexdigest()

            dataset_version = DatasetVersion(
                dataset_id=dataset_id,
                version_number=version_number,
                storage_path=storage_key,
                sha256=sha256,
                row_count=len(df),
                column_count=len(df.columns),
                created_at=utc_now()
            )
            db.add(dataset_version)
            db.flush()
            
            dataset.current_version_id = dataset_version.id
            db.commit()
            
            return {
                "dataset_id": dataset.id,
                "version_id": dataset_version.id,
                "name": dataset.name,
                "file_type": file_type,
                "row_count": dataset_version.row_count,
                "column_count": dataset_version.column_count,
                "status": "IMPORTED"
            }
        except AppError:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise AppError("import_failed", str(exc), status_code=500) from exc
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
