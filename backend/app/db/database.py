import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root so DATABASE_URL points to AWS RDS
root_dir = Path(__file__).resolve().parent.parent.parent.parent
load_dotenv(root_dir / ".env")

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import config

DATABASE_URL = config.database.url

db_url = DATABASE_URL
selected_driver = config.database.driver.lower()

# Format dialect string if bare postgresql:// provided
if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
    prefix = "postgresql://" if db_url.startswith("postgresql://") else "postgres://"
    if selected_driver in ("psycopg", "psycopg3"):
        db_url = db_url.replace(prefix, "postgresql+psycopg://", 1)
    elif selected_driver == "psycopg2":
        db_url = db_url.replace(prefix, "postgresql+psycopg2://", 1)
    else:
        db_url = db_url.replace(prefix, "postgresql+pg8000://", 1)

connect_args = {}
engine_kwargs = {"pool_pre_ping": True}

if "postgresql" in db_url or "postgres" in db_url:
    if "pg8000" in db_url:
        connect_args["timeout"] = config.database.connect_timeout_seconds
    elif "psycopg" in db_url:
        connect_args["connect_timeout"] = config.database.connect_timeout_seconds

    engine_kwargs.update({
        "pool_size": config.database.pool_size,
        "max_overflow": config.database.max_overflow,
        "pool_timeout": config.database.pool_timeout_seconds,
        "pool_recycle": config.database.pool_recycle_seconds
    })
elif db_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    db_url,
    connect_args=connect_args,
    **engine_kwargs
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

