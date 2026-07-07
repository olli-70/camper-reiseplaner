import os

from sqlmodel import Session, SQLModel, create_engine

# Default: file inside the mounted /data volume; overridable for tests.
DB_PATH = os.getenv("CAMPER_DB", "/data/camper.db")
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


# Neue Spalten, die bei einem Upgrade zu einer bereits bestehenden
# stop-Tabelle ergänzt werden müssen (SQLite create_all ändert keine
# vorhandenen Tabellen). Additive ALTER TABLE -> Daten bleiben erhalten.
_STOP_COLUMNS = {
    "reserviert": "BOOLEAN NOT NULL DEFAULT 0",
    "reserviert_von": "DATETIME",
    "reserviert_bis": "DATETIME",
}


def _migrate() -> None:
    with engine.begin() as conn:
        existing = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(stop)").fetchall()
        }
        for column, ddl in _STOP_COLUMNS.items():
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE stop ADD COLUMN {column} {ddl}")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _migrate()


def get_session():
    with Session(engine) as session:
        yield session
