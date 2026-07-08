from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List
from enum import Enum


class VideoCategoryEnum(str, Enum):
    RHYMES = "rhymes"
    CARTOON = "cartoon"
    BIRDS = "birds"
    BEDTIME = "bedtime"
    MORAL = "moral"      # merged: moral + animation + stories
    ANIMALS = "animals"


class VideoBase(BaseModel):
    video_id: str
    title: str
    channel: str
    channel_id: Optional[str] = None
    channel_url: Optional[str] = None
    views: int = 0
    likes: int = 0
    comments: int = 0
    category: Optional[str] = None
    category_id: Optional[str] = None
    duration: str = ""
    duration_seconds: int = 0
    published_at: datetime
    hours_ago: int = 0
    url: str = ""
    thumbnail_url: Optional[str] = None
    group_category: VideoCategoryEnum
    matched_keywords: Optional[List[str]] = []
    matched_terms: Optional[str] = ""
    search_rank: Optional[int] = 0
    keyword_count: Optional[int] = 0
    
    class Config:
        from_attributes = True