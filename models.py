# =============================================================
# models.py
# =============================================================
from sqlalchemy import Column, String, Integer, BigInteger, DateTime, Text, Index, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import TIMESTAMP
from datetime import datetime
import enum

from database import Base


class VideoCategory(str, enum.Enum):
    BIRDS_ANIMALS = "birds_animals"
    ANIMATION = "animation"


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String(50), unique=True, nullable=False, index=True)
    title = Column(Text, nullable=False)
    channel = Column(String(255), nullable=False)
    views = Column(BigInteger, default=0)
    likes = Column(BigInteger, default=0)
    comments = Column(BigInteger, default=0)
    category = Column(String(100))
    category_id = Column(String(10))
    duration = Column(String(50))
    duration_seconds = Column(Integer)
    published_at = Column(TIMESTAMP, nullable=False)
    hours_ago = Column(Integer)
    url = Column(String(255))
    thumbnail_url = Column(String(500))
    group_category = Column(SQLEnum(VideoCategory), nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Indexes
    __table_args__ = (
        Index("idx_videos_views", "views"),
        Index("idx_videos_published", "published_at"),
        Index("idx_videos_category", "group_category"),
        Index("idx_videos_channel", "channel"),
    )

    def __repr__(self):
        return f"<Video(video_id={self.video_id}, title={self.title[:50]})>"


class ScrapeLog(Base):
    __tablename__ = "scrape_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(TIMESTAMP, default=datetime.utcnow)
    completed_at = Column(TIMESTAMP)
    total_videos_found = Column(Integer, default=0)
    total_videos_saved = Column(Integer, default=0)
    status = Column(String(50), default="running")
    error_message = Column(Text)
