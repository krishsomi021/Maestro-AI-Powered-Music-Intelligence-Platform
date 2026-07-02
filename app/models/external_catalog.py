import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, Float, Integer, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.job import Base


class ExternalCatalog(Base):
    __tablename__ = "external_catalog"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    artist_name: Mapped[str] = mapped_column(String, nullable=False)
    track_name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    lastfm_url: Mapped[str | None] = mapped_column(String, nullable=True)
    lastfm_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    seed_track_name: Mapped[str | None] = mapped_column(String, nullable=True)
    global_play_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("clock_timestamp()")
    )
    last_refreshed_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("clock_timestamp()")
    )
