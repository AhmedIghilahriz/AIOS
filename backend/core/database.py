from dotenv import load_dotenv
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://aios:aios_secret@localhost:5432/aios")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
