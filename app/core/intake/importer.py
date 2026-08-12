import uuid
from datetime import datetime, timezone
from fastapi import UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from pathlib import Path
import os
import shutil
from uuid import uuid4
import hashlib
import pandas as pd

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

    @staticmethod
    def _safe_filename(filename: str | None) -> str:
        # ``Path.name`` on Linux does not treat a Windows backslash as a separator.
        raw = (filename or "upload").replace("\\", "/").replace("\x00", "")
        name = Path(raw).name.strip() or "upload"
        return name[:512]

    def _copy_upload_with_limit(self, source, destination: Path) -> None:
        written = 0
        chunk_size = 1024 * 1024
        with destination.open("wb") as buffer:
            while chunk := source.read(chunk_size):
                written += len(chunk)
                if written > self.storage.max_upload_bytes:
                    buffer.close()
                    destination.unlink(missing_ok=True)
                    raise AppError(
                        "FILE_TOO_LARGE",
                        f"Upload exceeds the configured {self.storage.max_upload_bytes} byte limit.",
                        status_code=413,
                    )
                buffer.write(chunk)

    def import_dataframe(
        self,
        db: Session,
        frame: pd.DataFrame,
        *,
        name: str,
        source_type: str,
        tenant_id: str = "default",
        dataset_id: str | None = None,
        parent_version_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Persist a connector result as an immutable dataset version.

        This is the common write path for database/API ingestion. It keeps the
        same versioning, checksum, tenant, and lineage contract as file uploads.
        The caller owns any checkpoint transaction surrounding this operation.
        """
        if not isinstance(frame, pd.DataFrame):
            raise AppError("INVALID_DATAFRAME", "Connector output must be a pandas DataFrame.", status_code=422)
        if frame.empty:
            raise AppError("EMPTY_DATASET", "The source returned no rows.", status_code=422)
        safe_name = self._safe_filename(name)
        tenant_id = str(tenant_id or "default")
        if dataset_id:
            dataset = db.query(Dataset).filter(Dataset.id == str(dataset_id), Dataset.tenant_id == tenant_id).first()
            if dataset is None:
                raise AppError("DATASET_NOT_FOUND", "The target dataset was not found for this tenant.", status_code=404)
            previous = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_number.desc()).first()
            version_number = int(previous.version_number if previous else 0) + 1
            parent_version_id = parent_version_id or (previous.id if previous else None)
        else:
            dataset = Dataset(
                tenant_id=tenant_id,
                name=safe_name,
                source_type=str(source_type),
                original_filename=safe_name,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(dataset)
            db.flush()
            version_number = 1

        storage_key = f"{dataset.id}/v{version_number}.csv"
        storage_path = Path(self.storage.root) / storage_key
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(storage_path, index=False)
        sha256 = hashlib.sha256(storage_path.read_bytes()).hexdigest()
        version = DatasetVersion(
            dataset_id=dataset.id,
            version_number=version_number,
            parent_version_id=parent_version_id,
            storage_path=storage_key,
            sha256=sha256,
            row_count=len(frame),
            column_count=len(frame.columns),
            metadata_json=metadata or {},
            created_at=utc_now(),
        )
        db.add(version)
        db.flush()
        dataset.current_version_id = version.id
        dataset.updated_at = utc_now()
        return {
            "dataset_id": dataset.id,
            "version_id": version.id,
            "version_number": version.version_number,
            "name": dataset.name,
            "source_type": dataset.source_type,
            "row_count": version.row_count,
            "column_count": version.column_count,
            "parent_version_id": version.parent_version_id,
            "status": "IMPORTED",
        }

    async def inspect(self, upload: UploadFile) -> dict:
        """
        Temporarily save the file, detect its type, and return inspection info.
        Used for multi-sheet Excel files.
        """
        temp_dir = Path(self.storage.root) / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._safe_filename(upload.filename)
        temp_path = temp_dir / f"{uuid4().hex}_{safe_name}"

        try:
            await upload.seek(0)
            self._copy_upload_with_limit(upload.file, temp_path)
            
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
        original_filename = self._safe_filename(upload.filename or "upload.csv")
        tenant_id = str(kwargs.pop("tenant_id", "default") or "default")
        
        temp_dir = Path(self.storage.root) / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._safe_filename(original_filename)
        temp_path = temp_dir / f"{uuid4().hex}_{safe_name}"

        try:
            await upload.seek(0)
            self._copy_upload_with_limit(upload.file, temp_path)
                
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
                tenant_id=tenant_id,
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
