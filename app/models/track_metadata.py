import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.job import Base


class TrackMetadata(Base):
    __tablename__ = "track_metadata"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    spotify_track_id: Mapped[str | None] = mapped_column(String, nullable=True)
    artist_name: Mapped[str] = mapped_column(String, nullable=False)
    track_name: Mapped[str] = mapped_column(String, nullable=False)
    spotify_artist_id: Mapped[str | None] = mapped_column(String, nullable=True)
    genres: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    popularity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    enriched_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
