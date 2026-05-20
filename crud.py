from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from models import Video, ScrapeLog
from schemas import VideoBase


class VideoCRUD:
    @staticmethod
    async def create_video(db: AsyncSession, video_data: VideoBase) -> Video:
        video = Video(**video_data.model_dump())
        db.add(video)
        await db.flush()
        return video

    @staticmethod
    async def get_video_by_id(db: AsyncSession, video_id: str) -> Optional[Video]:
        result = await db.execute(
            select(Video).where(Video.video_id == video_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_or_update_video(db: AsyncSession, video_data: VideoBase) -> Video:
        existing = await VideoCRUD.get_video_by_id(db, video_data.video_id)
        
        if existing:
            for key, value in video_data.model_dump().items():
                setattr(existing, key, value)
            existing.updated_at = datetime.utcnow()
            await db.flush()
            return existing
        else:
            return await VideoCRUD.create_video(db, video_data)

    @staticmethod
    async def bulk_create_or_update(db: AsyncSession, videos_data: List[VideoBase]) -> List[Video]:
        videos = []
        for video_data in videos_data:
            video = await VideoCRUD.create_or_update_video(db, video_data)
            videos.append(video)
        await db.flush()
        return videos

    @staticmethod
    async def get_all_videos(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        category: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "views",
        sort_order: str = "desc",
        min_views: Optional[int] = None,
        max_views: Optional[int] = None,
        min_duration: Optional[int] = None,
        max_duration: Optional[int] = None,
        hours_ago_max: Optional[int] = None,
        channel: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None
    ) -> tuple[List[Video], int]:
        query = select(Video)
        
        if category:
            query = query.where(Video.group_category == category)
        
        if search:
            query = query.where(
                Video.title.ilike(f"%{search}%") | Video.channel.ilike(f"%{search}%")
            )
        
        if min_views is not None:
            query = query.where(Video.views >= min_views)
        if max_views is not None:
            query = query.where(Video.views <= max_views)
        
        if min_duration is not None:
            query = query.where(Video.duration_seconds >= min_duration)
        if max_duration is not None:
            query = query.where(Video.duration_seconds <= max_duration)
        
        if hours_ago_max is not None:
            query = query.where(Video.hours_ago <= hours_ago_max)
        
        if channel:
            query = query.where(Video.channel.ilike(f"%{channel}%"))
        
        if from_date:
            query = query.where(Video.published_at >= from_date)
        if to_date:
            query = query.where(Video.published_at <= to_date)
        
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar()
        
        allowed_sort_fields = ["views", "likes", "comments", "published_at", "duration_seconds", "created_at", "hours_ago"]
        if sort_by not in allowed_sort_fields:
            sort_by = "views"
        
        sort_column = getattr(Video, sort_by, Video.views)
        if sort_order == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(sort_column)
        
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        videos = result.scalars().all()
        
        return videos, total

    @staticmethod
    async def delete_old_videos(db: AsyncSession, days: int = 3) -> int:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        result = await db.execute(
            select(Video).where(Video.published_at < cutoff_date)
        )
        old_videos = result.scalars().all()
        for video in old_videos:
            await db.delete(video)
        await db.flush()
        return len(old_videos)

    @staticmethod
    async def get_total_count(db: AsyncSession) -> int:
        result = await db.execute(select(func.count()).select_from(Video))
        return result.scalar() or 0


class ScrapeLogCRUD:
    @staticmethod
    async def create_log(db: AsyncSession) -> ScrapeLog:
        log = ScrapeLog()
        db.add(log)
        await db.flush()
        return log
    
    @staticmethod
    async def update_log(
        db: AsyncSession,
        log_id: int,
        total_found: int = None,
        total_saved: int = None,
        status: str = None,
        error_message: str = None
    ):
        result = await db.execute(select(ScrapeLog).where(ScrapeLog.id == log_id))
        log = result.scalar_one_or_none()
        if log:
            if total_found is not None:
                log.total_videos_found = total_found
            if total_saved is not None:
                log.total_videos_saved = total_saved
            if status is not None:
                log.status = status
            if error_message is not None:
                log.error_message = error_message
            if status == "completed" or status == "failed":
                log.completed_at = datetime.utcnow()
            await db.flush()
        return log