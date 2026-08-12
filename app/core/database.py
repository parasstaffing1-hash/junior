from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()


def ensure_additive_local_schema(database_engine) -> None:
    """Bridge older local SQLite databases without taking ownership from Alembic.

    Production deployments run Alembic before the web process. Local previews
    historically used ``create_all`` and may therefore have pre-migration tables;
    these additive columns keep those previews compatible without destructive DDL.
    """
    Base.metadata.create_all(bind=database_engine)
    inspector = inspect(database_engine)
    additions = {
        "datasets": {"tenant_id": "VARCHAR NOT NULL DEFAULT 'default'"},
        "workspace_assets": {"tenant_id": "VARCHAR NOT NULL DEFAULT 'default'"},
        "registered_models": {"tenant_id": "VARCHAR NOT NULL DEFAULT 'default'"},
        "experiments": {"tenant_id": "VARCHAR NOT NULL DEFAULT 'default'"},
        "geographic_boundaries": {"tenant_id": "VARCHAR NOT NULL DEFAULT 'default'"},
        "geographic_mappings": {"tenant_id": "VARCHAR NOT NULL DEFAULT 'default'"},
        "dataset_versions": {"metadata": "JSON"},
        "automation_runs": {
            "tenant_id": "VARCHAR NOT NULL DEFAULT 'default'",
            "max_attempts": "INTEGER NOT NULL DEFAULT 3",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "next_run_at": "DATETIME",
            "locked_at": "DATETIME",
            "locked_by": "VARCHAR",
            "heartbeat_at": "DATETIME",
            "retryable": "BOOLEAN NOT NULL DEFAULT 1",
        },
        "alert_rules": {
            "tenant_id": "VARCHAR NOT NULL DEFAULT 'default'",
            "workspace_id": "VARCHAR NOT NULL DEFAULT 'default'",
            "name": "VARCHAR NOT NULL DEFAULT 'alert'",
            "definition": "JSON NOT NULL DEFAULT '{}'",
            "enabled": "BOOLEAN NOT NULL DEFAULT 1",
            "created_at": "DATETIME",
            "updated_at": "DATETIME",
        },
    }
    with database_engine.begin() as connection:
        for table, columns in additions.items():
            existing = {item["name"] for item in inspector.get_columns(table)} if table in inspector.get_table_names() else set()
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


def build_database(database_url: str):
    """Build an engine/session pair for the app or an isolated test instance."""
    connect_args = {"check_same_thread": False} if "sqlite" in database_url else {}
    database_engine = create_engine(database_url, connect_args=connect_args, future=True)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=database_engine, expire_on_commit=False)
    return database_engine, session_factory


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./analytics.db")
engine, SessionLocal = build_database(DATABASE_URL)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
