from sqlalchemy import Column, String, Integer, BigInteger, DateTime, Text, Index
from sqlalchemy.dialects.mysql import JSON
from datetime import datetime

from database import Base


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
    published_at = Column(DateTime, nullable=False)
    hours_ago = Column(Integer)
    url = Column(String(255))
    thumbnail_url = Column(String(500))
    group_category = Column(String(50), nullable=False)  # Will store: rhymes, stories, cartoon, animation, birds, bedtime, moral
    
    # SEO fields
    matched_keywords = Column(JSON, default=list)
    matched_terms = Column(Text, default="")
    search_rank = Column(Integer, default=0)
    keyword_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_videos_views", "views"),
        Index("idx_videos_published", "published_at"),
        Index("idx_videos_category", "group_category"),
        Index("idx_videos_channel", "channel"),
    )


class ScrapeLog(Base):
    __tablename__ = "scrape_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    total_videos_found = Column(Integer, default=0)
    total_videos_saved = Column(Integer, default=0)
    status = Column(String(50), default="running")
    error_message = Column(Text)