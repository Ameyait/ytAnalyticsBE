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
        Scrape all videos using the enhanced YouTube service
        Returns all 7 categories: birds, rhymes, cartoon, animation, bedtime, moral, stories
        """
        
        # Use the new fetch_all_videos method for optimized search
        result = await self.youtube_service.fetch_all_videos()
        
        videos_data = result.get("videos", [])
        
        # Map string category to enum
        group_map = {
            "birds": VideoCategoryEnum.BIRDS,
            "rhymes": VideoCategoryEnum.RHYMES,
            "cartoon": VideoCategoryEnum.CARTOON,
            "animation": VideoCategoryEnum.ANIMATION,
            "bedtime": VideoCategoryEnum.BEDTIME,
            "moral": VideoCategoryEnum.MORAL,
            "stories": VideoCategoryEnum.STORIES,
        }
        
        # Convert to VideoBase objects
        videos = []
        for data in videos_data:
            try:
                group_category = data.get("group_category", "stories")
                data["group_category"] = group_map.get(group_category, VideoCategoryEnum.STORIES)
                
                # Remove content_hash as it's not in VideoBase
                if "content_hash" in data:
                    del data["content_hash"]
                
                # ✅ FIX: Convert datetime to string if it's a datetime object
                if 'published_at' in data and hasattr(data['published_at'], 'isoformat'):
                    data['published_at'] = data['published_at'].isoformat()
                
                video = VideoBase(**data)
                videos.append(video)
            except Exception as e:
                print(f"Error creating video: {e}")
                print(f"Problematic data: {data.get('title', 'Unknown')}")
                continue
        
        # Calculate statistics
        stats = {
            "total_videos_found": len(videos_data),
            "videos_after_filter": len(videos),
            "quota_used": self.youtube_service.quota_used,
            "quota_limit": self.youtube_service.quota_limit,
            "quota_percentage": (self.youtube_service.quota_used / self.youtube_service.quota_limit) * 100 if self.youtube_service.quota_limit else 0,
            "duplicates_filtered": self.youtube_service.metrics["duplicates_filtered"],
            "quality_filtered": self.youtube_service.metrics["quality_filtered"],
            "total_time_seconds": self.youtube_service.metrics.get("total_time_seconds", 0),
        }
        
        print(f"\n✅ Scraping completed: {len(videos)} videos saved")
        print(f"📊 Quota used: {self.youtube_service.quota_used}/{self.youtube_service.quota_limit} ({stats['quota_percentage']:.1f}%)")
        print(f"🔄 Duplicates filtered: {stats['duplicates_filtered']}")
        print(f"🎨 Quality filtered: {stats['quality_filtered']}")
        print(f"⏱️  Time taken: {stats['total_time_seconds']:.2f} seconds")
        
        # Print category breakdown
        category_counts = {}
        for v in videos:
            cat = v.group_category.value
            category_counts[cat] = category_counts.get(cat, 0) + 1
        
        print(f"\n📁 Category breakdown:")
        for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"   {cat}: {count} videos")
        
        return videos, stats