import os

from sqlmodel import Session, SQLModel, create_engine

# Default: file inside the mounted /data volume; overridable for tests.
DB_PATH = os.getenv("CAMPER_DB", "/data/camper.db")
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
