import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _db_url() -> str:
    default_path = Path(__file__).resolve().parent.parent / "data" / "accidents.db"
    path = os.getenv("SMART_TRANS_DB", str(default_path))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


engine = create_engine(
    _db_url(),
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
