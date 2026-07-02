from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Integer,
    BigInteger,
    DateTime,
    Text,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB

from database import Base


class Video(Base):
    __tablename__ = "videos"

    # ============================================================
    # PRIMARY KEY
    # ============================================================
    id = Column(Integer, primary_key=True, autoincrement=True)

    # ============================================================
    # VIDEO IDENTIFICATION
    # ============================================================
    video_id = Column(String(50), unique=True, nullable=False, index=True)
    title = Column(Text, nullable=False)
    url = Column(String(255))
    thumbnail_url = Column(String(500))

    # ============================================================
    # CHANNEL INFORMATION
    # ============================================================
    channel = Column(String(255), nullable=False)
    channel_id = Column(String(100), nullable=True)
    channel_url = Column(String(500), nullable=True)

    # ============================================================
    # STATISTICS
    # ============================================================
    views = Column(BigInteger, default=0)
    likes = Column(BigInteger, default=0)
    comments = Column(BigInteger, default=0)

    # ============================================================
    # CATEGORY INFORMATION
    # ============================================================
    category = Column(String(100))
    category_id = Column(String(10))
    group_category = Column(String(50), nullable=False)

    # ============================================================
    # DURATION INFORMATION
    # ============================================================
    duration = Column(String(50))
    duration_seconds = Column(Integer)

    # ============================================================
    # DATE AND TIME
    # ============================================================
    published_at = Column(DateTime, nullable=False)
    hours_ago = Column(Integer)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # ============================================================
    # SEO INFORMATION
    # ============================================================
    matched_keywords = Column(JSONB, default=list)
    matched_terms = Column(Text, default="")
    search_rank = Column(Integer, default=0)
    keyword_count = Column(Integer, default=0)

    # ============================================================
    # INDEXES
    # ============================================================
    __table_args__ = (
        Index("idx_videos_video_id", "video_id"),
        Index("idx_videos_views", "views"),
        Index("idx_videos_published", "published_at"),
        Index("idx_videos_category", "group_category"),
        Index("idx_videos_channel", "channel"),
        Index("idx_videos_channel_id", "channel_id"),
        Index("idx_videos_hours_ago", "hours_ago"),
        Index("idx_videos_created_at", "created_at"),
        Index("idx_videos_duration", "duration_seconds"),
        Index("idx_videos_category_views", "group_category", "views"),
        Index("idx_videos_published_category", "published_at", "group_category"),
    )


class ScrapeLog(Base):
    __tablename__ = "scrape_logs"

    # ============================================================
    # PRIMARY KEY
    # ============================================================
    id = Column(Integer, primary_key=True, autoincrement=True)

    # ============================================================
    # TIMESTAMPS
    # ============================================================
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)

    # ============================================================
    # STATISTICS
    # ============================================================
    total_videos_found = Column(Integer, default=0)
    total_videos_saved = Column(Integer, default=0)

    # ============================================================
    # STATUS
    # ============================================================
    status = Column(String(50), default="running")
    error_message = Column(Text)

    # ============================================================
    # INDEXES
    # ============================================================
    __table_args__ = (
        Index("idx_scrape_logs_status", "status"),
        Index("idx_scrape_logs_started", "started_at"),
        Index("idx_scrape_logs_completed", "completed_at"),
    )