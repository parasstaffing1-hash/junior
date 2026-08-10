from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()


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
