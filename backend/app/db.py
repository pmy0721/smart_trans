import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
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


def ensure_sqlite_schema(e: Engine) -> None:
    if e.url.get_backend_name() != "sqlite":
        return

    db_path = e.url.database
    if not db_path:
        return

    try:
        import sqlite3
    except Exception:
        return

    desired: dict[str, str] = {
        # Location fields (added after initial table may have been created)
        "location_text": "VARCHAR(256)",
        "lat": "FLOAT",
        "lng": "FLOAT",
        "location_source": "VARCHAR(32)",
        "location_confidence": "FLOAT",
        # Debugging / provenance
        "raw_model_output": "TEXT",
    }

    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='accidents'")
        if not cur.fetchone():
            return

        cur.execute("PRAGMA table_info(accidents)")
        cols = {row[1] for row in cur.fetchall()}

        altered = False
        for col, typ in desired.items():
            if col in cols:
                continue
            cur.execute(f"ALTER TABLE accidents ADD COLUMN {col} {typ}")
            altered = True

        if altered:
            con.commit()
    finally:
        con.close()
