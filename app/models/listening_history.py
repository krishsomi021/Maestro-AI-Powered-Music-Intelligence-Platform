import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, event, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.normalization import normalize_key
from app.models.job import Base


class ListeningHistory(Base):
    __tablename__ = "listening_history"
    __table_args__ = (
        UniqueConstraint("artist_name", "track_name", "end_time"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    artist_name: Mapped[str] = mapped_column(String, nullable=False)
    track_name: Mapped[str] = mapped_column(String, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ms_played: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_key: Mapped[str] = mapped_column(String, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("NOW()"))


@event.listens_for(ListeningHistory, "before_insert")
def _populate_normalized_key(mapper, connection, target: ListeningHistory) -> None:
    """
    ORM-level insert path (session.add(...)) doesn't go through worker/tasks/etl_pipeline.py's
    explicit normalize_key() call, so derive it here if the caller didn't already set one.
    The Core-level bulk pg_insert() in etl_pipeline._load() bypasses ORM events entirely and
    always supplies it explicitly — this listener only backstops direct ORM construction.
    """
    if not target.normalized_key:
        target.normalized_key = normalize_key(target.artist_name, target.track_name)
