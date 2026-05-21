import asyncio
from typing import List, Dict, Any, Tuple

from config import config
from youtube_service import YouTubeService
from schemas import VideoBase, VideoCategoryEnum


class ScraperService:
    def __init__(self):
        self.youtube_service = YouTubeService()
    
    async def scrape_all_videos(self) -> Tuple[List[VideoBase], Dict[str, Any]]:
        published_after = self.youtube_service.get_published_after()
        
        # Use optimized keyword list (6 keywords total)
        all_keywords = self.youtube_service.ALL_KEYWORDS
        
        print(f"🚀 OPTIMIZED SCRAPE - Using {len(all_keywords)} broad keywords")
        print(f"   Keywords: {all_keywords}")
        print(f"   Looking for videos posted after: {published_after}")
        
        all_video_ids = []
        for keyword in all_keywords:
            print(f"  🔍 Searching: {keyword}")
            video_ids = await self.youtube_service.search_keyword(keyword, published_after)
            all_video_ids.extend(video_ids)
            await asyncio.sleep(0.3)
        
        unique_ids = list(set(all_video_ids))
        print(f"📊 Found {len(unique_ids)} unique videos, fetching details...")
        
        videos_data = await self.youtube_service.get_video_details(unique_ids)
        
        videos = []
        for data in videos_data:
            try:
                # Map to only Moral and Birds categories
                group_map = {
                    "moral": VideoCategoryEnum.MORAL,
                    "birds": VideoCategoryEnum.BIRDS,
                }
                data["group_category"] = group_map.get(data["group_category"], VideoCategoryEnum.MORAL)
                video = VideoBase(**data)
                videos.append(video)
            except Exception as e:
                print(f"Error creating video: {e}")
                continue
        
        stats = {
            "keywords_searched": len(all_keywords),
            "total_videos_found": len(unique_ids),
            "videos_after_filter": len(videos),
            "quota_used": self.youtube_service.quota_used,
            "quota_limit": self.youtube_service.quota_limit,
            "quota_percentage": (self.youtube_service.quota_used / self.youtube_service.quota_limit) * 100,
        }
        
        print(f"\n✅ Scraping completed: {len(videos)} videos saved")
        print(f"📊 Quota used: {self.youtube_service.quota_used}/{self.youtube_service.quota_limit} ({stats['quota_percentage']:.1f}%)")
        
        # Print category breakdown
        category_counts = {}
        for v in videos:
            cat = v.group_category.value
            category_counts[cat] = category_counts.get(cat, 0) + 1
        print(f"📁 Category breakdown: {category_counts}")
        print(f"📈 Optimization savings: ~{(100 - stats['quota_percentage']):.1f}% of daily quota")
        
        return videos, stats