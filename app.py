# =============================================================
# app.py (WITH SCHEDULING & AUTO-CLEANUP)
# =============================================================
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pydantic import BaseModel
import asyncio
from zoneinfo import ZoneInfo  # Python 3.9+ for timezone

from database import get_db, init_db
from crud import VideoCRUD, ScrapeLogCRUD
from scraper_service import ScraperService
from schemas import VideoCategoryEnum
from config import config

# Create scraper service instance
scraper_service = ScraperService()

# IST Timezone
IST = ZoneInfo("Asia/Kolkata")


# =============================================================
# Scheduled Scraper Class
# =============================================================

class ScheduledScraper:
    def __init__(self):
        self.is_running = False
        self.last_scrape_time = None
        self.scheduler_task = None
    
    async def scrape_and_cleanup(self, db: AsyncSession, source: str = "scheduled"):
        """Perform scrape and cleanup old data"""
        if self.is_running:
            print(f"⚠️ Scrape already in progress at {source}")
            return None
        
        self.is_running = True
        try:
            print(f"🔄 Starting {source} scrape at {datetime.now(IST)}")
            
            # Step 1: Run the scrape
            videos, stats = await scraper_service.scrape_all_videos()
            
            # Step 2: Save videos to database
            saved_videos = await VideoCRUD.bulk_create_or_update(db, videos)
            
            # Step 3: Delete videos older than 3 days
            deleted_count = await VideoCRUD.delete_old_videos(db, days=3)
            
            await db.commit()
            
            self.last_scrape_time = datetime.now(IST)
            
            print(f"✅ {source} scrape completed: {len(saved_videos)} new/updated, {deleted_count} old deleted")
            
            return {
                "success": True,
                "new_videos": len(saved_videos),
                "deleted_old": deleted_count,
                "total_videos": await VideoCRUD.get_total_count(db),
                "timestamp": self.last_scrape_time
            }
            
        except Exception as e:
            print(f"❌ {source} scrape failed: {e}")
            await db.rollback()
            return None
        finally:
            self.is_running = False
    
    async def scheduled_scrape_11am(self):
        """Scrape at 11 AM IST"""
        async with AsyncSessionLocal() as db:
            result = await self.scrape_and_cleanup(db, "11AM Schedule")
            if result:
                print(f"📊 11AM Results: {result}")
    
    async def scheduled_scrape_5pm(self):
        """Scrape at 5 PM IST"""
        async with AsyncSessionLocal() as db:
            result = await self.scrape_and_cleanup(db, "5PM Schedule")
            if result:
                print(f"📊 5PM Results: {result}")
    
    async def run_scheduler(self):
        """Run the scheduler that triggers at 11 AM and 5 PM IST"""
        print("🕐 Scheduler started - Will run at 11:00 AM and 5:00 PM IST daily")
        
        while True:
            now = datetime.now(IST)
            
            # Target times
            target_11am = now.replace(hour=11, minute=0, second=0, microsecond=0)
            target_5pm = now.replace(hour=17, minute=0, second=0, microsecond=0)
            
            # Calculate next run time
            if now < target_11am:
                next_run = target_11am
            elif now < target_5pm:
                next_run = target_5pm
            else:
                # Next day 11 AM
                next_run = target_11am + timedelta(days=1)
            
            wait_seconds = (next_run - now).total_seconds()
            
            print(f"⏰ Next scheduled scrape at: {next_run.strftime('%Y-%m-%d %H:%M:%S IST')} (in {wait_seconds/3600:.1f} hours)")
            
            await asyncio.sleep(wait_seconds)
            
            # Check which time it is
            current_time = datetime.now(IST)
            
            if current_time.hour == 11 and current_time.minute < 5:
                # It's around 11 AM
                await self.scheduled_scrape_11am()
            elif current_time.hour == 17 and current_time.minute < 5:
                # It's around 5 PM
                await self.scheduled_scrape_5pm()


# Global scheduler instance
scheduler = ScheduledScraper()


# =============================================================
# Response Models
# =============================================================

class VideoResponse(BaseModel):
    id: int
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
    thumbnail_url: Optional[str]
    group_category: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class VideosResponse(BaseModel):
    success: bool
    total: int
    filters_applied: dict
    videos: List[VideoResponse]


class ScrapeResponse(BaseModel):
    success: bool
    message: str
    scrape_id: int
    new_videos: Optional[int] = None
    deleted_old: Optional[int] = None


class CleanupResponse(BaseModel):
    success: bool
    deleted_count: int
    remaining_videos: int
    message: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events"""
    print("Initializing database...")
    await init_db()
    print("Database initialized")
    
    # Start the scheduler in background
    async def start_scheduler():
        await scheduler.run_scheduler()
    
    # Run scheduler as background task
    scheduler_task = asyncio.create_task(start_scheduler())
    
    print("✅ Scheduler started - Will scrape at 11 AM and 5 PM IST daily")
    print("🗑️ Auto-cleanup enabled - Videos older than 3 days will be deleted automatically")
    
    yield
    
    # Shutdown
    scheduler_task.cancel()
    print("Shutting down...")


# Create FastAPI app
app = FastAPI(
    title="YouTube Telugu Kids Content API",
    description="Single API to get filtered videos and trigger scraping",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================
# SINGLE GET API - Get videos with all filters
# =============================================================

@app.get("/videos", response_model=VideosResponse)
async def get_videos(
    # Pagination
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=500, description="Items per page"),
    
    # Category filters
    category: Optional[str] = Query(None, description="Filter by category: 'birds_animals' or 'animation'"),
    
    # Search filters
    search: Optional[str] = Query(None, description="Search in title and channel"),
    
    # Sort options
    sort_by: str = Query("views", description="Sort field: views, likes, comments, published_at, duration_seconds"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    
    # Range filters
    min_views: Optional[int] = Query(None, ge=0, description="Minimum views"),
    max_views: Optional[int] = Query(None, ge=0, description="Maximum views"),
    min_duration: Optional[int] = Query(None, ge=0, description="Minimum duration in seconds"),
    max_duration: Optional[int] = Query(None, ge=0, description="Maximum duration in seconds"),
    hours_ago_max: Optional[int] = Query(None, ge=0, description="Max hours ago (e.g., 72 for last 3 days)"),
    
    # Date filter
    from_date: Optional[str] = Query(None, description="From date (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="To date (YYYY-MM-DD)"),
    
    # Channel filter
    channel: Optional[str] = Query(None, description="Filter by specific channel"),
    
    db: AsyncSession = Depends(get_db)
):
    """
    SINGLE API TO GET VIDEOS WITH ALL FILTERS
    
    Examples:
    - Get all videos: /videos
    - Get birds & animals: /videos?category=birds_animals
    - Get animations: /videos?category=animation
    - Search: /videos?search=elephant
    - Top 10 most viewed: /videos?limit=10&sort_by=views&sort_order=desc
    - Last 2 days: /videos?hours_ago_max=48
    - High views: /videos?min_views=10000
    - Long videos: /videos?min_duration=600
    - Channel specific: /videos?channel=ETV%20Bal%20Bharat
    """
    
    # Build filters dictionary
    filters = {}
    
    # Category filter
    if category:
        if category not in ["birds_animals", "animation"]:
            raise HTTPException(status_code=400, detail="Category must be 'birds_animals' or 'animation'")
        filters["category"] = category
    
    # Search filter
    if search:
        filters["search"] = search
    
    # Views range
    if min_views is not None:
        filters["min_views"] = min_views
    if max_views is not None:
        filters["max_views"] = max_views
    
    # Duration range
    if min_duration is not None:
        filters["min_duration"] = min_duration
    if max_duration is not None:
        filters["max_duration"] = max_duration
    
    # Hours ago filter
    if hours_ago_max is not None:
        filters["hours_ago_max"] = hours_ago_max
    
    # Channel filter
    if channel:
        filters["channel"] = channel
    
    # Calculate offset for pagination
    skip = (page - 1) * limit
    
    # Get videos from database with filters
    videos, total = await VideoCRUD.get_all_videos(
        db, 
        skip=skip, 
        limit=limit,
        category=filters.get("category"),
        search=filters.get("search"),
        sort_by=sort_by,
        sort_order=sort_order,
        min_views=filters.get("min_views"),
        max_views=filters.get("max_views"),
        min_duration=filters.get("min_duration"),
        max_duration=filters.get("max_duration"),
        hours_ago_max=filters.get("hours_ago_max"),
        channel=filters.get("channel")
    )
    
    # Return response
    return VideosResponse(
        success=True,
        total=total,
        filters_applied={
            "page": page,
            "limit": limit,
            "category": category,
            "search": search,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "min_views": min_views,
            "max_views": max_views,
            "min_duration": min_duration,
            "max_duration": max_duration,
            "hours_ago_max": hours_ago_max,
            "channel": channel
        },
        videos=[VideoResponse.model_validate(v) for v in videos]
    )


# =============================================================
# MANUAL SCRAPE API - Trigger scrape manually
# =============================================================

@app.post("/scrape", response_model=ScrapeResponse)
async def trigger_scrape(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    MANUAL SCRAPE - Trigger YouTube scraping
    
    This will:
    1. Search YouTube for Telugu kids content
    2. Filter for >180s duration
    3. Filter for Birds/Animals & Animation only
    4. Store in database
    5. Auto-delete videos older than 3 days
    
    Returns immediately with scrape ID. Scraping runs in background.
    """
    # Create scrape log
    scrape_log = await ScrapeLogCRUD.create_log(db)
    await db.commit()
    
    # Run scraping in background
    async def run_scrape():
        from database import AsyncSessionLocal
        
        async with AsyncSessionLocal() as new_session:
            try:
                # Run the scraping
                videos, stats = await scraper_service.scrape_all_videos()
                
                # Save videos to database
                saved_videos = await VideoCRUD.bulk_create_or_update(new_session, videos)
                
                # Delete videos older than 3 days
                deleted_count = await VideoCRUD.delete_old_videos(new_session, days=3)
                
                # Update log
                await ScrapeLogCRUD.update_log(
                    new_session,
                    scrape_log.id,
                    total_found=stats["total_videos_found"],
                    total_saved=len(saved_videos),
                    status="completed"
                )
                await new_session.commit()
                
                print(f"✅ Manual scrape completed: {len(saved_videos)} videos saved, {deleted_count} old deleted")
                
            except Exception as e:
                print(f"❌ Manual scrape failed: {e}")
                await ScrapeLogCRUD.update_log(
                    new_session,
                    scrape_log.id,
                    status="failed",
                    error_message=str(e)
                )
                await new_session.commit()
    
    # Add to background tasks
    background_tasks.add_task(run_scrape)
    
    return ScrapeResponse(
        success=True,
        message="Manual scraping started in background. Will auto-delete videos older than 3 days.",
        scrape_id=scrape_log.id
    )


# =============================================================
# DELETE OLD VIDEOS API - Manual cleanup
# =============================================================

@app.post("/cleanup", response_model=CleanupResponse)
async def cleanup_old_videos(
    days: int = Query(3, ge=1, le=30, description="Delete videos older than N days"),
    db: AsyncSession = Depends(get_db)
):
    """
    MANUAL CLEANUP - Delete videos older than specified days
    
    Default: Deletes videos older than 3 days
    """
    deleted_count = await VideoCRUD.delete_old_videos(db, days=days)
    remaining = await VideoCRUD.get_total_count(db)
    await db.commit()
    
    return CleanupResponse(
        success=True,
        deleted_count=deleted_count,
        remaining_videos=remaining,
        message=f"Deleted {deleted_count} videos older than {days} days"
    )


# =============================================================
# SCHEDULER STATUS API
# =============================================================

@app.get("/scheduler/status")
async def get_scheduler_status():
    """Get scheduler status and next run time"""
    now = datetime.now(IST)
    
    # Calculate next run times
    target_11am = now.replace(hour=11, minute=0, second=0, microsecond=0)
    target_5pm = now.replace(hour=17, minute=0, second=0, microsecond=0)
    
    if now < target_11am:
        next_11am = target_11am
    else:
        next_11am = target_11am + timedelta(days=1)
    
    if now < target_5pm:
        next_5pm = target_5pm
    else:
        next_5pm = target_5pm + timedelta(days=1)
    
    return {
        "scheduler_running": True,
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "last_scrape_time": scheduler.last_scrape_time.strftime("%Y-%m-%d %H:%M:%S IST") if scheduler.last_scrape_time else None,
        "is_scraping": scheduler.is_running,
        "next_scheduled_scrapes": {
            "11_AM": next_11am.strftime("%Y-%m-%d %H:%M:%S IST"),
            "5_PM": next_5pm.strftime("%Y-%m-%d %H:%M:%S IST")
        },
        "auto_cleanup_days": 3,
        "schedule_times": ["11:00 AM IST", "5:00 PM IST"]
    }


# =============================================================
# Helper endpoint to check scrape status
# =============================================================

@app.get("/scrape/status/{scrape_id}")
async def get_scrape_status(scrape_id: int, db: AsyncSession = Depends(get_db)):
    """Check the status of a scrape job"""
    from sqlalchemy import select
    from models import ScrapeLog
    
    result = await db.execute(
        select(ScrapeLog).where(ScrapeLog.id == scrape_id)
    )
    log = result.scalar_one_or_none()
    
    if not log:
        raise HTTPException(status_code=404, detail="Scrape log not found")
    
    return {
        "scrape_id": log.id,
        "status": log.status,
        "started_at": log.started_at.strftime("%Y-%m-%d %H:%M:%S") if log.started_at else None,
        "completed_at": log.completed_at.strftime("%Y-%m-%d %H:%M:%S") if log.completed_at else None,
        "total_found": log.total_videos_found,
        "total_saved": log.total_videos_saved,
        "error_message": log.error_message
    }


# =============================================================
# Simple stats endpoint
# =============================================================

@app.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Get simple statistics"""
    from sqlalchemy import select, func
    from models import Video
    
    # Get counts
    total_result = await db.execute(select(func.count()).select_from(Video))
    total_videos = total_result.scalar()
    
    birds_result = await db.execute(
        select(func.count()).where(Video.group_category == "birds_animals")
    )
    animation_result = await db.execute(
        select(func.count()).where(Video.group_category == "animation")
    )
    
    # Get date range of videos
    oldest_result = await db.execute(select(func.min(Video.published_at)))
    newest_result = await db.execute(select(func.max(Video.published_at)))
    
    # Get top channels
    top_channels_result = await db.execute(
        select(Video.channel, func.sum(Video.views).label("total_views"))
        .group_by(Video.channel)
        .order_by(func.sum(Video.views).desc())
        .limit(5)
    )
    
    return {
        "total_videos": total_videos,
        "birds_animals_count": birds_result.scalar() or 0,
        "animation_count": animation_result.scalar() or 0,
        "oldest_video": oldest_result.scalar().strftime("%Y-%m-%d") if oldest_result.scalar() else None,
        "newest_video": newest_result.scalar().strftime("%Y-%m-%d") if newest_result.scalar() else None,
        "top_channels": [{"channel": row[0], "total_views": row[1]} for row in top_channels_result.all()]
    }


# =============================================================
# Root endpoint
# =============================================================

@app.get("/")
async def root():
    """API Documentation"""
    return {
        "api": "YouTube Telugu Kids Content API",
        "version": "1.0.0",
        "auto_schedule": "Runs daily at 11:00 AM and 5:00 PM IST",
        "auto_cleanup": "Automatically deletes videos older than 3 days",
        "endpoints": {
            "GET /videos": "Get videos with all filters",
            "POST /scrape": "MANUAL - Trigger YouTube scraping",
            "POST /cleanup": "MANUAL - Delete old videos",
            "GET /scrape/status/{id}": "Check scrape status",
            "GET /scheduler/status": "Check scheduler status",
            "GET /stats": "Get statistics"
        },
        "examples": {
            "All videos": "/videos",
            "Birds & Animals only": "/videos?category=birds_animals",
            "Animations only": "/videos?category=animation",
            "Search elephant stories": "/videos?search=elephant",
            "Top 10 most viewed": "/videos?limit=10&sort_by=views&sort_order=desc",
            "Last 2 days": "/videos?hours_ago_max=48",
            "Manual scrape": "POST /scrape",
            "Manual cleanup": "POST /cleanup?days=3",
            "Scheduler status": "GET /scheduler/status"
        }
    }


# Import for AsyncSessionLocal
from database import AsyncSessionLocal