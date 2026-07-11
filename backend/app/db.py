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


def _backfill_datum_to_an() -> None:
    """Einmaliger, datenschonender Backfill nach Wegfall des Einzelfelds `datum`:

    Übernimmt einen bestehenden `datum`-Wert (Tag) als „An"-Zeitpunkt
    (reserviert_von, Tagesbeginn 00:00), sofern für die Zeile noch KEIN An-Wert
    gesetzt ist. Idempotent (läuft danach ins Leere, weil reserviert_von belegt
    ist) und nicht-destruktiv: Die alte `datum`-Spalte bleibt in der Tabelle
    erhalten (SQLite droppt keine Spalten), wird vom Modell nur nicht mehr
    genutzt. So gehen keine bestehenden Termine verloren.

    Datumsformat mit Leerzeichen-Trenner ("YYYY-MM-DD HH:MM:SS"), damit
    SQLAlchemy den Wert beim Lesen wieder als datetime parst.
    """
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql(
            'PRAGMA table_info("stop")').fetchall()}
        if "datum" not in cols:
            return  # frische DB ohne Alt-Spalte -> nichts zu tun
        conn.exec_driver_sql(
            """
            UPDATE stop
               SET reserviert_von = datum || ' 00:00:00'
             WHERE (reserviert_von IS NULL OR reserviert_von = '')
               AND datum IS NOT NULL AND datum != ''
            """
        )


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _migrate()
    _backfill_datum_to_an()


def get_session():
    with Session(engine) as session:
        yield session
