from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import importlib.util
import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import pandas as pd
from sqlalchemy import create_engine, text


class ConnectorError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class ConnectorResult:
    frame: pd.DataFrame
    source: dict[str, Any]
    checkpoint: str | None = None


def _safe_read_query(query: str) -> str:
    normalized = str(query or "").strip()
    if not normalized or not normalized.casefold().startswith(("select ", "with ")):
        raise ConnectorError("READ_ONLY_QUERY_REQUIRED", "Only SELECT or WITH queries are allowed.")
    if ";" in normalized.rstrip(";"):
        raise ConnectorError("MULTI_STATEMENT_BLOCKED", "Multiple SQL statements are not allowed.")
    blocked = (" insert ", " update ", " delete ", " drop ", " alter ", " create ", " attach ", " pragma ", " copy ")
    if any(token in f" {normalized.casefold()} " for token in blocked):
        raise ConnectorError("WRITE_QUERY_BLOCKED", "The connector only permits read-only SQL.")
    return normalized.rstrip(";")


def _validate_url(url: str, allowed_hosts: set[str] | None = None) -> str:
    parsed = urlparse(str(url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConnectorError("INVALID_SOURCE_URL", "Only absolute HTTP(S) URLs are accepted.")
    host = (parsed.hostname or "").casefold()
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        raise ConnectorError("PRIVATE_SOURCE_BLOCKED", "Private or loopback source URLs are blocked by default.")
    try:
        address = ipaddress.ip_address(host)
        if address.is_private or address.is_loopback or address.is_link_local:
            raise ConnectorError("PRIVATE_SOURCE_BLOCKED", "Private source IPs are blocked by default.")
    except ValueError:
        pass
    if allowed_hosts and host not in allowed_hosts:
        raise ConnectorError("SOURCE_HOST_NOT_ALLOWED", "The source host is not in the configured egress allowlist.", details={"host": host})
    return str(url)


def validate_schema(frame: pd.DataFrame, contract: dict[str, Any]) -> dict[str, Any]:
    expected = {str(item["name"]): item for item in (contract.get("columns") or []) if isinstance(item, dict) and item.get("name")}
    actual = {str(column): str(frame[column].dtype) for column in frame.columns}
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    type_changes = []
    for column in sorted(set(expected) & set(actual)):
        accepted = str(expected[column].get("physical_type") or expected[column].get("type") or "")
        accepted_norm = accepted.casefold()
        actual_norm = actual[column].casefold()
        compatible = accepted_norm in actual_norm or (accepted_norm in {"object", "string", "str"} and actual_norm in {"object", "string", "str"})
        if accepted and not compatible:
            type_changes.append({"column": column, "expected": accepted, "actual": actual[column]})
    required_nulls = [column for column in expected if expected[column].get("required") and column in frame.columns and frame[column].isna().any()]
    breaking = bool(missing or type_changes or required_nulls)
    return {"valid": not breaking, "breaking": breaking, "missing_columns": missing, "unexpected_columns": unexpected, "type_changes": type_changes, "required_columns_with_nulls": required_nulls, "actual_columns": actual}


class ConnectorService:
    """Stateless adapters; credentials are supplied per request or by a secret ref."""

    def __init__(self, *, timeout_seconds: float = 30, allowed_hosts: set[str] | None = None):
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.allowed_hosts = allowed_hosts

    @staticmethod
    def catalog() -> list[dict[str, Any]]:
        def database_entry(identifier: str, driver: str, label: str) -> dict[str, Any]:
            try:
                installed = importlib.util.find_spec(driver) is not None
            except (ImportError, ModuleNotFoundError):
                installed = False
            return {
                "id": identifier,
                "kind": "database",
                "status": "available" if installed else "optional_driver",
                "driver": driver,
                "label": label,
                "capabilities": ["read", "schema", "watermark_incremental"] if installed else ["read", "schema", "watermark_incremental"],
                "installation_required": not installed,
            }
        return [
            database_entry("postgresql", "psycopg2", "PostgreSQL via psycopg2"),
            database_entry("sqlserver", "pyodbc", "SQL Server via pyodbc and an installed ODBC driver"),
            database_entry("snowflake", "snowflake.sqlalchemy", "Snowflake via snowflake-sqlalchemy"),
            database_entry("bigquery", "google.cloud.bigquery", "BigQuery via google-cloud-bigquery and sqlalchemy-bigquery"),
            {"id": "rest_json", "kind": "api", "status": "available", "capabilities": ["read", "pagination", "retries", "schema_drift"]},
            {"id": "parquet", "kind": "file", "status": "available", "capabilities": ["read", "predicate_pushdown", "partitioned_storage"]},
        ]

    def read_database(self, *, connection_url: str, query: str, parameters: dict[str, Any] | None = None, max_rows: int = 100_000) -> ConnectorResult:
        safe_query = _safe_read_query(query)
        engine = create_engine(connection_url, pool_pre_ping=True, future=True)
        try:
            with engine.connect() as connection:
                frame = pd.read_sql_query(text(safe_query), connection, params=parameters or {}, dtype_backend="numpy_nullable")
        except Exception as exc:
            raise ConnectorError("DATABASE_READ_FAILED", "The database query failed.", details={"error": str(exc)}) from exc
        finally:
            engine.dispose()
        if len(frame) > max_rows:
            frame = frame.head(max_rows)
        return ConnectorResult(frame, {"kind": "database", "query": safe_query, "row_count": len(frame)})

    def read_rest(self, *, url: str, data_path: str | None = None, headers: dict[str, str] | None = None, page_param: str = "page", page_size_param: str = "page_size", page_size: int = 100, max_pages: int = 100, max_rows: int = 100_000, retries: int = 3) -> ConnectorResult:
        source_url = _validate_url(url, self.allowed_hosts)
        rows: list[dict[str, Any]] = []
        pages = 0
        next_url: str | None = source_url
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
            while next_url and pages < max_pages and len(rows) < max_rows:
                page = 1 if pages == 0 else pages + 1
                current = next_url
                if pages:
                    separator = "&" if "?" in current else "?"
                    current = f"{current}{separator}{page_param}={page}&{page_size_param}={page_size}"
                response = None
                for attempt in range(max(1, retries)):
                    try:
                        response = client.get(current, headers=headers or {})
                        if response.status_code in {429, 500, 502, 503, 504}:
                            raise httpx.HTTPStatusError("retryable status", request=response.request, response=response)
                        response.raise_for_status()
                        break
                    except (httpx.HTTPError, httpx.TimeoutException) as exc:
                        if attempt + 1 >= max(1, retries):
                            raise ConnectorError("REST_READ_FAILED", "The REST source could not be read after retries.", details={"error": str(exc), "page": page}) from exc
                        time.sleep(min(2 ** attempt, 5))
                body = response.json()
                items = body
                if data_path:
                    for token in data_path.split("."):
                        items = items.get(token, []) if isinstance(items, dict) else []
                if isinstance(items, dict):
                    items = items.get("data", items.get("items", [items]))
                if not isinstance(items, list):
                    raise ConnectorError("REST_PAYLOAD_INVALID", "The selected REST data path did not resolve to a list.")
                rows.extend(item for item in items if isinstance(item, dict))
                pages += 1
                next_url = body.get("next") if isinstance(body, dict) else None
                if not next_url and len(items) < page_size:
                    break
        frame = pd.json_normalize(rows[:max_rows])
        return ConnectorResult(frame, {"kind": "rest_json", "url": source_url, "pages": pages, "row_count": len(frame)}, checkpoint=str(pages))

    def read_parquet(self, *, path: str, columns: list[str] | None = None, filters: list[tuple[str, str, Any]] | None = None, max_rows: int = 100_000) -> ConnectorResult:
        try:
            frame = pd.read_parquet(path, columns=columns, filters=filters)
        except Exception as exc:
            raise ConnectorError("PARQUET_READ_FAILED", "The Parquet source could not be read.", details={"error": str(exc)}) from exc
        return ConnectorResult(frame.head(max_rows), {"kind": "parquet", "path": path, "row_count": min(len(frame), max_rows)})
