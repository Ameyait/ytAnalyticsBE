# =============================================================
# crud.py (UPDATED with all filters)
# =============================================================
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from models import Video, ScrapeLog, VideoCategory
from schemas import VideoBase


class VideoCRUD:
    @staticmethod
    async def create_video(db: AsyncSession, video_data: VideoBase) -> Video:
        """Create a new video record"""
        video = Video(**video_data.model_dump())
        db.add(video)
        await db.flush()
        return video

    @staticmethod
    async def get_video_by_id(db: AsyncSession, video_id: str) -> Optional[Video]:
        """Get video by YouTube video ID"""
        result = await db.execute(
            select(Video).where(Video.video_id == video_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_or_update_video(db: AsyncSession, video_data: VideoBase) -> Video:
        """Create or update video record"""
        existing = await VideoCRUD.get_video_by_id(db, video_data.video_id)
        
        if existing:
            # Update existing
            for key, value in video_data.model_dump().items():
                setattr(existing, key, value)
            existing.updated_at = datetime.utcnow()
            await db.flush()
            return existing
        else:
            # Create new
            return await VideoCRUD.create_video(db, video_data)

    @staticmethod
    async def bulk_create_or_update(db: AsyncSession, videos_data: List[VideoBase]) -> List[Video]:
        """Bulk create or update videos"""
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
        category: Optional[VideoCategory] = None,
        search: Optional[str] = None,
        sort_by: str = "views",
        sort_order: str = "desc",
        # NEW FILTERS START HERE
        min_views: Optional[int] = None,
        max_views: Optional[int] = None,
        min_duration: Optional[int] = None,
        max_duration: Optional[int] = None,
        hours_ago_max: Optional[int] = None,
        channel: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None
    ) -> tuple[List[Video], int]:
        """
        Get all videos with pagination and advanced filtering
        
        Filters:
        - category: birds_animals or animation
        - search: search in title and channel
        - min_views/max_views: filter by view count
        - min_duration/max_duration: filter by duration in seconds
        - hours_ago_max: get videos from last N hours
        - channel: filter by channel name (partial match)
        - from_date/to_date: date range filter
        """
        query = select(Video)
        
        # Apply filters
        if category:
            query = query.where(Video.group_category == category)
        
        if search:
            query = query.where(
                Video.title.ilike(f"%{search}%") | Video.channel.ilike(f"%{search}%")
            )
        
        # View count filters
        if min_views is not None:
            query = query.where(Video.views >= min_views)
        
        if max_views is not None:
            query = query.where(Video.views <= max_views)
        
        # Duration filters
        if min_duration is not None:
            query = query.where(Video.duration_seconds >= min_duration)
        
        if max_duration is not None:
            query = query.where(Video.duration_seconds <= max_duration)
        
        # Hours ago filter (e.g., last 48 hours)
        if hours_ago_max is not None:
            query = query.where(Video.hours_ago <= hours_ago_max)
        
        # Channel filter (partial match)
        if channel:
            query = query.where(Video.channel.ilike(f"%{channel}%"))
        
        # Date range filters
        if from_date:
            query = query.where(Video.published_at >= from_date)
        
        if to_date:
            query = query.where(Video.published_at <= to_date)
        
        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar()
        
        # Apply sorting
        # Validate sort_by to prevent SQL injection
        allowed_sort_fields = ["views", "likes", "comments", "published_at", "duration_seconds", "created_at", "hours_ago"]
        if sort_by not in allowed_sort_fields:
            sort_by = "views"
        
        sort_column = getattr(Video, sort_by, Video.views)
        if sort_order == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(sort_column)
        
        # Apply pagination
        query = query.offset(skip).limit(limit)
        
        result = await db.execute(query)
        videos = result.scalars().all()
        
        return videos, total

    @staticmethod
    async def get_dashboard_stats(db: AsyncSession) -> Dict[str, Any]:
        """Get dashboard statistics"""
        # Basic stats
        total_videos_result = await db.execute(select(func.count()).select_from(Video))
        total_videos = total_videos_result.scalar()
        
        total_channels_result = await db.execute(
            select(func.count(func.distinct(Video.channel)))
        )
        total_channels = total_channels_result.scalar()
        
        total_views_result = await db.execute(select(func.sum(Video.views)))
        total_views = total_views_result.scalar() or 0
        
        total_likes_result = await db.execute(select(func.sum(Video.likes)))
        total_likes = total_likes_result.scalar() or 0
        
        # Averages
        avg_views_result = await db.execute(select(func.avg(Video.views)))
        avg_views = float(avg_views_result.scalar() or 0)
        
        avg_duration_result = await db.execute(select(func.avg(Video.duration_seconds)))
        avg_duration = float(avg_duration_result.scalar() or 0)
        
        # Videos by category
        birds_count_result = await db.execute(
            select(func.count()).where(Video.group_category == VideoCategory.BIRDS_ANIMALS)
        )
        animation_count_result = await db.execute(
            select(func.count()).where(Video.group_category == VideoCategory.ANIMATION)
        )
        
        videos_by_category = {
            "birds_animals": birds_count_result.scalar(),
            "animation": animation_count_result.scalar(),
        }
        
        # Top 10 videos
        top_videos_result = await db.execute(
            select(Video).order_by(desc(Video.views)).limit(10)
        )
        top_videos = top_videos_result.scalars().all()
        
        # Top 10 channels by views
        top_channels_result = await db.execute(
            select(
                Video.channel,
                func.sum(Video.views).label("total_views"),
                func.count(Video.id).label("video_count")
            )
            .group_by(Video.channel)
            .order_by(desc("total_views"))
            .limit(10)
        )
        top_channels = [
            {"channel": row[0], "total_views": row[1], "video_count": row[2]}
            for row in top_channels_result.all()
        ]
        
        # Recent scrapes
        recent_scrapes_result = await db.execute(
            select(ScrapeLog).order_by(desc(ScrapeLog.started_at)).limit(10)
        )
        recent_scrapes = recent_scrapes_result.scalars().all()
        
        return {
            "total_videos": total_videos,
            "total_channels": total_channels,
            "total_views": total_views,
            "total_likes": total_likes,
            "avg_views": avg_views,
            "avg_duration_seconds": avg_duration,
            "videos_by_category": videos_by_category,
            "top_videos": top_videos,
            "top_channels": top_channels,
            "recent_scrapes": [
                {
                    "id": log.id,
                    "started_at": log.started_at,
                    "completed_at": log.completed_at,
                    "total_videos_saved": log.total_videos_saved,
                    "status": log.status
                }
                for log in recent_scrapes
            ]
        }

    # Add these methods to VideoCRUD class in crud.py

    @staticmethod
    async def delete_old_videos(db: AsyncSession, days: int = 3) -> int:
        """Delete videos older than specified days"""
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
        """Get total number of videos"""
        result = await db.execute(select(func.count()).select_from(Video))
        return result.scalar() or 0
class ScrapeLogCRUD:
    @staticmethod
    async def create_log(db: AsyncSession) -> ScrapeLog:
        """Create a new scrape log"""
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
        """Update scrape log"""
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
    

