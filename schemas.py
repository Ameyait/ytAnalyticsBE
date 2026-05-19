# =============================================================
# schemas.py
# =============================================================
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List
from enum import Enum


class VideoCategoryEnum(str, Enum):
    BIRDS_ANIMALS = "birds_animals"
    ANIMATION = "animation"


class VideoBase(BaseModel):
    video_id: str
    title: str
    channel: str
    views: int
    likes: int
    comments: int
    category: str
    duration: str
    duration_seconds: int
    published_at: datetime
    hours_ago: int
    url: str
    thumbnail_url: Optional[str] = None
    group_category: VideoCategoryEnum


class VideoResponse(VideoBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class VideoListResponse(BaseModel):
    total: int
    videos: List[VideoResponse]


class ScrapeResponse(BaseModel):
    success: bool
    message: str
    total_found: int
    total_saved: int
    scrape_log_id: int


class DashboardStats(BaseModel):
    total_videos: int
    total_channels: int
    total_views: int
    total_likes: int
    avg_views: float
    avg_duration_seconds: float
    videos_by_category: dict
    top_videos: List[VideoResponse]
    top_channels: List[dict]
    recent_scrapes: List[dict]

