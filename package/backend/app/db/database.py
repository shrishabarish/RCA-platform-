import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

# Default to a local SQLite database if no PostgreSQL URL is provided
# This allows local development without setting up RDS immediately.
# Example PostgreSQL URL: "postgresql://user:<password>@rds-endpoint.amazonaws.com/dbname"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./rca_data.db")

# For SQLite, we need connect_args={"check_same_thread": False}
# For PostgreSQL, this argument is not needed/supported.
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Use pg8000 (pure Python) to avoid C-extension/glibc issues in AWS Lambda
    db_url = DATABASE_URL
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+pg8000://", 1)
    elif db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+pg8000://", 1)
    engine = create_engine(db_url)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
