from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pydantic import BaseModel
import asyncio
from zoneinfo import ZoneInfo
from sqlalchemy import select, desc
from models import ScrapeLog
from datetime import timezone
from database import get_db, init_db, AsyncSessionLocal
from crud import VideoCRUD, ScrapeLogCRUD
from scraper_service import ScraperService
from schemas import VideoCategoryEnum
from models import Video, ScrapeLog
from config import config

scraper_service = ScraperService()
IST = ZoneInfo("Asia/Kolkata")


class ScheduledScraper:
    def __init__(self):
        self.is_running = False
        self.last_scrape_time = None
    
    async def scrape_and_cleanup(self, db: AsyncSession, source: str = "scheduled"):
        if self.is_running:
            print(f"⚠️ Scrape already in progress at {source}")
            return None
        
        self.is_running = True
        try:
            print(f"🔄 Starting {source} scrape at {datetime.now(IST)}")
            videos, stats = await scraper_service.scrape_all_videos()
            saved_videos = await VideoCRUD.bulk_create_or_update(db, videos)
            deleted_count = await VideoCRUD.delete_old_videos(db, days=3)
            await db.commit()
            self.last_scrape_time = datetime.now(IST)
            print(f" {source} scrape completed: {len(saved_videos)} saved, {deleted_count} deleted")
            return {"success": True, "new_videos": len(saved_videos), "deleted_old": deleted_count}
        except Exception as e:
            print(f" {source} scrape failed: {e}")
            await db.rollback()
            return None
        finally:
            self.is_running = False
    
    async def run_scheduler(self):
        print("🕐 Scheduler started - Will run at 11:00 AM and 5:00 PM IST daily")
        while True:
            now = datetime.now(IST)
            target_11am = now.replace(hour=11, minute=0, second=0, microsecond=0)
            target_5pm = now.replace(hour=17, minute=0, second=0, microsecond=0)
            
            if now < target_11am:
                next_run = target_11am
            elif now < target_5pm:
                next_run = target_5pm
            else:
                next_run = target_11am + timedelta(days=1)
            
            wait_seconds = (next_run - now).total_seconds()
            print(f"⏰ Next scheduled scrape at: {next_run.strftime('%Y-%m-%d %H:%M:%S IST')}")
            await asyncio.sleep(wait_seconds)
            
            current_time = datetime.now(IST)
            async with AsyncSessionLocal() as db:
                if current_time.hour == 11 and current_time.minute < 5:
                    await self.scrape_and_cleanup(db, "11AM Schedule")
                elif current_time.hour == 17 and current_time.minute < 5:
                    await self.scrape_and_cleanup(db, "5PM Schedule")


scheduler = ScheduledScraper()


class VideoResponse(BaseModel):
    id: int
    video_id: str
    title: str
    channel: str
    channel_id: Optional[str] = None  # ADD THIS
    channel_url: Optional[str] = None  # ADD THIS - YouTube channel link
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
    last_refreshed: Optional[str] = None


class ScrapeResponse(BaseModel):
    success: bool
    message: str
    scrape_id: int


class CleanupResponse(BaseModel):
    success: bool
    deleted_count: int
    remaining_videos: int
    message: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Initializing database...")
    await init_db()
    print("Database initialized")
    scheduler_task = asyncio.create_task(scheduler.run_scheduler())
    print("✅ Scheduler started - Will scrape at 11 AM and 5 PM IST daily")
    yield
    scheduler_task.cancel()
    print("Shutting down...")


app = FastAPI(title="YouTube Telugu Kids Content API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://13.127.71.122:3000",
        "http://localhost:3000",
        "http://127.0.0.1:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================
# UPDATED GET VIDEOS ENDPOINT WITH CHANNEL URL
# =============================================================

@app.get("/videos", response_model=VideosResponse)
async def get_videos(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search in title and channel"),
    sort_by: str = Query("views", description="Sort field"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    min_views: Optional[int] = Query(None, ge=0),
    max_views: Optional[int] = Query(None, ge=0),
    min_duration: Optional[int] = Query(None, ge=0),
    max_duration: Optional[int] = Query(None, ge=0),
    hours_ago_max: Optional[int] = Query(None, ge=0),
    channel: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    # Validate category
    allowed_categories = ["rhymes", "stories", "cartoon", "animation", "birds", "bedtime", "moral"]
    if category and category not in allowed_categories:
        raise HTTPException(
            status_code=400, 
            detail=f"Category must be one of: {', '.join(allowed_categories)}"
        )
    
    skip = (page - 1) * limit
    videos, total = await VideoCRUD.get_all_videos(
        db, skip=skip, limit=limit, category=category, search=search,
        sort_by=sort_by, sort_order=sort_order, min_views=min_views,
        max_views=max_views, min_duration=min_duration, max_duration=max_duration,
        hours_ago_max=hours_ago_max, channel=channel
    )
    
    # Get last successful scrape date and time in IST
    result = await db.execute(
        select(ScrapeLog)
        .where(ScrapeLog.status == "completed")
        .order_by(desc(ScrapeLog.completed_at))
        .limit(1)
    )
    last_scrape = result.scalar_one_or_none()
    
    last_refreshed = None
    if last_scrape and last_scrape.completed_at:
        ist_timezone = ZoneInfo("Asia/Kolkata")
        completed_ist = last_scrape.completed_at.replace(tzinfo=timezone.utc).astimezone(ist_timezone)
        last_refreshed = completed_ist.strftime("%d %B %Y at %I:%M:%S %p IST")
    
    return VideosResponse(
        success=True, 
        total=total,
        filters_applied={
            "page": page, 
            "limit": limit, 
            "category": category, 
            "search": search,
            "sort_by": sort_by,
            "sort_order": sort_order
        },
        videos=[VideoResponse.model_validate(v) for v in videos],
        last_refreshed=last_refreshed
    )






@app.post("/scrape", response_model=ScrapeResponse)
async def trigger_scrape(background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    from models import ScrapeLog as ScrapeLogModel
    
    scrape_log = ScrapeLogModel(
        status="running",
        total_videos_found=0,
        total_videos_saved=0
    )
    db.add(scrape_log)
    await db.commit()
    await db.refresh(scrape_log)
    
    async def run_scrape():
        async with AsyncSessionLocal() as new_session:
            try:
                videos, stats = await scraper_service.scrape_all_videos()
                saved_videos = await VideoCRUD.bulk_create_or_update(new_session, videos)
                deleted_count = await VideoCRUD.delete_old_videos(new_session, days=3)
                
                result = await new_session.execute(
                    select(ScrapeLogModel).where(ScrapeLogModel.id == scrape_log.id)
                )
                log = result.scalar_one_or_none()
                if log:
                    log.completed_at = datetime.utcnow()
                    log.total_videos_found = stats["total_videos_found"]
                    log.total_videos_saved = len(saved_videos)
                    log.status = "completed"
                    await new_session.commit()
                
                print(f"✅ Manual scrape completed: {len(saved_videos)} videos saved, {deleted_count} deleted")
            except Exception as e:
                print(f"❌ Manual scrape failed: {e}")
                result = await new_session.execute(
                    select(ScrapeLogModel).where(ScrapeLogModel.id == scrape_log.id)
                )
                log = result.scalar_one_or_none()
                if log:
                    log.status = "failed"
                    log.error_message = str(e)
                    await new_session.commit()
    
    background_tasks.add_task(run_scrape)
    return ScrapeResponse(success=True, message="Scraping started", scrape_id=scrape_log.id)


@app.post("/cleanup", response_model=CleanupResponse)
async def cleanup_old_videos(days: int = Query(3, ge=1, le=30), db: AsyncSession = Depends(get_db)):
    deleted_count = await VideoCRUD.delete_old_videos(db, days=days)
    remaining = await VideoCRUD.get_total_count(db)
    await db.commit()
    return CleanupResponse(success=True, deleted_count=deleted_count, remaining_videos=remaining, message=f"Deleted {deleted_count} videos older than {days} days")



@app.get("/")
async def root():
    return {
        "api": "YouTube Telugu Kids Content API",
        "version": "1.0.0",
        "categories": ["rhymes", "stories", "cartoon", "animation", "birds", "bedtime", "moral"],
        "endpoints": {
            "GET /videos": "Get videos with category filter (includes channel URL and last refreshed date)",
            "GET /animations": "Get only animation videos",
            "GET /cartoons": "Get only cartoon videos",
            "GET /categories/stats": "Get category statistics",
            "POST /scrape": "Trigger manual scrape",
            "POST /cleanup": "Delete old videos",
            "GET /stats": "Get basic statistics with last refreshed date"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)