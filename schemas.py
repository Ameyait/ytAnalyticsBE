from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List
from enum import Enum


class VideoCategoryEnum(str, Enum):
    RHYMES = "rhymes"
    STORIES = "stories"
    CARTOON = "cartoon"
    ANIMATION = "animation"  # NEW: Separate animation category
    BIRDS = "birds"
    BEDTIME = "bedtime"
    MORAL = "moral"


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
    matched_keywords: Optional[List[str]] = []
    matched_terms: Optional[str] = ""
    keyword_count: Optional[int] = 0
    search_rank: Optional[int] = 0

    class Config:
        from_attributes = True


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