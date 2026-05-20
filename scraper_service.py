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
        
        all_keywords = []
        for kw_list in self.youtube_service.ALL_KEYWORD_GROUPS.values():
            all_keywords.extend(kw_list)
        
        print(f"Starting optimized scrape for {len(all_keywords)} keywords...")
        print(f"Looking for videos posted after: {published_after}")
        
        all_video_ids = []
        for keyword in all_keywords:
            print(f"  Searching: {keyword}")
            video_ids = await self.youtube_service.search_keyword(keyword, published_after)
            all_video_ids.extend(video_ids)
            await asyncio.sleep(0.3)
        
        unique_ids = list(set(all_video_ids))
        print(f"Found {len(unique_ids)} unique videos, fetching details...")
        
        videos_data = await self.youtube_service.get_video_details(unique_ids)
        
        videos = []
        for data in videos_data:
            try:
                group_map = {
                    "rhymes": VideoCategoryEnum.RHYMES,
                    "stories": VideoCategoryEnum.STORIES,
                    "cartoon": VideoCategoryEnum.CARTOON,
                    "birds": VideoCategoryEnum.BIRDS,
                    "bedtime": VideoCategoryEnum.BEDTIME,
                    "moral": VideoCategoryEnum.MORAL,
                }
                data["group_category"] = group_map.get(data["group_category"], VideoCategoryEnum.STORIES)
                video = VideoBase(**data)
                videos.append(video)
            except Exception as e:
                print(f"Error creating video: {e}")
                continue
        
        stats = {
            "keywords_searched": len(all_keywords),
            "total_videos_found": len(unique_ids),
            "videos_after_filter": len(videos),
        }
        
        print(f"\n✅ Scraping completed: {len(videos)} videos saved")
        print(f"📊 Quota used: {self.youtube_service.quota_used}/{self.youtube_service.quota_limit}")
        
        return videos, stats