import os

from sqlmodel import Session, SQLModel, create_engine

# Default: file inside the mounted /data volume; overridable for tests.
DB_PATH = os.getenv("CAMPER_DB", "/data/camper.db")
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


def _migrate() -> None:
    """Additive Auto-Migration: ergänzt jede Spalte, die im Modell existiert,
    aber in der Tabelle noch fehlt, per ALTER TABLE ADD COLUMN. Spalten werden
    NULL-fähig hinzugefügt, damit Bestandszeilen unangetastet bleiben.

    Dadurch genügt es, ein neues Feld nur im Modell (models.py) zu ergänzen –
    die passende Spalte entsteht beim nächsten Start automatisch. SQLite
    ``create_all`` legt nur fehlende Tabellen an, nicht fehlende Spalten.
    """
    from . import models  # noqa: F401  – registriert die Tabellen in der Metadata

    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            rows = conn.exec_driver_sql(
                f'PRAGMA table_info("{table.name}")'
            ).fetchall()
            if not rows:
                continue  # Tabelle existiert noch nicht -> create_all erledigt das
            existing = {row[1] for row in rows}
            for col in table.columns:
                if col.name not in existing:
                    sqltype = col.type.compile(dialect=engine.dialect)
                    conn.exec_driver_sql(
                        f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {sqltype}'
                    )


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _migrate()


def get_session():
    with Session(engine) as session:
        yield session
