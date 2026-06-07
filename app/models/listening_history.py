import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

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
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("NOW()"))
