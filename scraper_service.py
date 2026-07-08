import asyncio
from typing import List, Dict, Any, Tuple

from config import config
from youtube_service import YouTubeService
from schemas import VideoBase, VideoCategoryEnum


class ScraperService:
    def __init__(self):
        self.youtube_service = YouTubeService()
    
    async def scrape_all_videos(self) -> Tuple[List[VideoBase], Dict[str, Any]]:
        """
        Scrape all videos using the enhanced YouTube service.
        Returns the 6 final categories: birds, animals, rhymes, cartoon,
        bedtime, moral (moral now also absorbs the old "animation" and
        "stories" buckets).
        """
        
        result = await self.youtube_service.fetch_all_videos()
        
        videos_data = result.get("videos", [])
        
        # Map string category to enum. Default falls back to MORAL (not
        # STORIES, which no longer exists) since moral is now the catch-all.
        group_map = {
            "birds": VideoCategoryEnum.BIRDS,
            "animals": VideoCategoryEnum.ANIMALS,
            "rhymes": VideoCategoryEnum.RHYMES,
            "cartoon": VideoCategoryEnum.CARTOON,
            "bedtime": VideoCategoryEnum.BEDTIME,
            "moral": VideoCategoryEnum.MORAL,
        }
        
        videos = []
        for data in videos_data:
            try:
                group_category = data.get("group_category", "moral")
                data["group_category"] = group_map.get(group_category, VideoCategoryEnum.MORAL)
                
                if "content_hash" in data:
                    del data["content_hash"]
                
                if 'published_at' in data and hasattr(data['published_at'], 'isoformat'):
                    data['published_at'] = data['published_at'].isoformat()
                
                video = VideoBase(**data)
                videos.append(video)
            except Exception as e:
                print(f"Error creating video: {e}")
                print(f"Problematic data: {data.get('title', 'Unknown')}")
                continue
        
        stats = {
            "total_videos_found": len(videos_data),
            "videos_after_filter": len(videos),
            "quota_used": self.youtube_service.quota_used,
            "quota_limit": self.youtube_service.quota_limit,
            "quota_percentage": (self.youtube_service.quota_used / self.youtube_service.quota_limit) * 100 if self.youtube_service.quota_limit else 0,
            "search_calls_used": self.youtube_service.search_calls_used,
            "search_calls_limit": self.youtube_service.search_calls_limit,
            "duplicates_filtered": self.youtube_service.metrics["duplicates_filtered"],
            "quality_filtered": self.youtube_service.metrics["quality_filtered"],
            "total_time_seconds": self.youtube_service.metrics.get("total_time_seconds", 0),
        }
        
        print(f"\n✅ Scraping completed: {len(videos)} videos saved")
        print(f"🔍 Search calls used: {stats['search_calls_used']}/{stats['search_calls_limit']} (today, resets midnight PT)")
        print(f"📊 Unit quota used: {self.youtube_service.quota_used}/{self.youtube_service.quota_limit} ({stats['quota_percentage']:.1f}%)")
        print(f"🔄 Duplicates filtered: {stats['duplicates_filtered']}")
        print(f"🎨 Quality filtered: {stats['quality_filtered']}")
        print(f"⏱️  Time taken: {stats['total_time_seconds']:.2f} seconds")
        
        category_counts = {}  
        for v in videos:
            cat = v.group_category.value
            category_counts[cat] = category_counts.get(cat, 0) + 1
        
        print(f"\n📁 Category breakdown:")
        for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"   {cat}: {count} videos")
        
        return videos, stats