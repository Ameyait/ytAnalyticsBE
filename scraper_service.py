# =============================================================
# scraper_service.py (FIXED VERSION)
# =============================================================
import asyncio
from typing import List, Dict, Any, Set, Tuple
from datetime import datetime

from config import config
from youtube_service import YouTubeService
from schemas import VideoBase, VideoCategoryEnum


class ScraperService:
    def __init__(self):
        self.youtube_service = YouTubeService()
    
    async def scrape_all_videos(self) -> Tuple[List[VideoBase], Dict[str, Any]]:
        """
        Scrape videos from all keywords
        Returns: (list of videos, stats dict)
        """
        published_after = self.youtube_service.get_published_after()
        
        # Combine all keywords with their categories
        keywords_with_categories = [
            (kw, VideoCategoryEnum.BIRDS_ANIMALS) 
            for kw in config.BIRDS_ANIMALS_KEYWORDS
        ] + [
            (kw, VideoCategoryEnum.ANIMATION) 
            for kw in config.ANIMATION_KEYWORDS
        ]
        
        # Collect all video IDs
        video_id_to_categories: Dict[str, Set[str]] = {}
        keyword_stats = []
        
        print(f"Starting scrape for {len(keywords_with_categories)} keywords...")
        
        # Search for videos using each keyword
        for keyword, category in keywords_with_categories:
            print(f"  Searching: {keyword}")
            video_ids = await self.youtube_service.search_keyword(keyword, published_after)
            
            for vid_id in video_ids:
                if vid_id not in video_id_to_categories:
                    video_id_to_categories[vid_id] = set()
                video_id_to_categories[vid_id].add(category.value)
            
            keyword_stats.append({
                "keyword": keyword,
                "category": category.value,
                "videos_found": len(video_ids)
            })
            
            # Rate limiting
            await asyncio.sleep(0.5)
        
        # Get unique video IDs
        unique_video_ids = list(video_id_to_categories.keys())
        print(f"Found {len(unique_video_ids)} unique videos, fetching details...")
        
        # Fetch video details (these already have group_category from youtube_service)
        videos_data = await self.youtube_service.get_video_details(unique_video_ids)
        
        # Convert to VideoBase objects - DON'T add group_category again
        videos = []
        for video_data in videos_data:
            try:
                # Remove category_id if present (not in VideoBase schema)
                if "category_id" in video_data:
                    del video_data["category_id"]
                
                # Create VideoBase object directly (group_category is already in video_data)
                video = VideoBase(**video_data)
                videos.append(video)
            except Exception as e:
                print(f"Error creating VideoBase object: {e}")
                print(f"Video data keys: {video_data.keys()}")
                continue
        
        stats = {
            "keywords_searched": len(keywords_with_categories),
            "total_videos_found": len(unique_video_ids),
            "videos_after_filter": len(videos),
            "keyword_stats": keyword_stats,
        }
        
        print(f"Scraping completed: {len(videos)} videos saved after filtering")
        
        return videos, stats