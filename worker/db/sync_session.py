from pgvector.psycopg2 import register_vector
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(settings.sync_database_url, pool_pre_ping=True)


@event.listens_for(engine, "connect")
def on_connect(dbapi_connection, connection_record):
    register_vector(dbapi_connection)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_sync_db() -> Session:
    return SessionLocal()
