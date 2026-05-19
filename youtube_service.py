# =============================================================
# youtube_service.py
# =============================================================
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
import asyncio
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import config


class YouTubeService:
    def __init__(self):
        self.api_key = config.YOUTUBE_API_KEY
        self.youtube = None
    
    def _get_client(self):
        """Get or create YouTube API client"""
        if not self.youtube:
            self.youtube = build("youtube", "v3", developerKey=self.api_key)
        return self.youtube
    
    def _parse_duration(self, duration_str: str) -> tuple[str, int]:
        """Parse ISO 8601 duration to readable string and seconds"""
        match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
        if not match:
            return "?", 0
        
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        
        total_seconds = hours * 3600 + minutes * 60 + seconds
        
        if hours:
            duration_str = f"{hours}h {minutes}m {seconds}s"
        elif minutes:
            duration_str = f"{minutes}m {seconds}s"
        else:
            duration_str = f"{seconds}s"
        
        return duration_str, total_seconds
    
    def _passes_content_filter(self, title: str, channel: str, category_id: str) -> bool:
        """Check if video passes content filters"""
        title_lower = title.lower()
        channel_lower = channel.lower()
        combined = f"{title_lower} {channel_lower}"
        
        # Check category
        if category_id not in config.ALLOWED_CATEGORIES:
            return False
        
        # Check blocked words
        for bad_word in config.MUST_NOT_CONTAIN:
            if bad_word in title_lower:
                return False
        
        # Check required words
        for good_word in config.MUST_CONTAIN_ANY:
            if good_word in combined:
                return True
        
        return False
    
    async def search_keyword(self, keyword: str, published_after: str) -> List[str]:
        """Search for videos with a keyword"""
        youtube = self._get_client()
        video_ids = []
        
        # Search both medium (4-20 min) and long (>20 min)
        for duration in ["medium", "long"]:
            try:
                # Run in thread pool since YouTube API is synchronous
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: youtube.search().list(
                        part="snippet",
                        q=keyword,
                        type="video",
                        regionCode=config.REGION_CODE,
                        maxResults=config.MAX_PER_KEYWORD,
                        order="viewCount",
                        relevanceLanguage="te",
                        safeSearch="strict",
                        publishedAfter=published_after,
                        videoDuration=duration,
                    ).execute()
                )
                
                ids = [item["id"]["videoId"] for item in response.get("items", [])]
                video_ids.extend(ids)
                
            except HttpError as e:
                print(f"Error searching keyword '{keyword}': {e}")
                continue
        
        return list(set(video_ids))
    
    async def get_video_details(self, video_ids: List[str]) -> List[Dict[str, Any]]:
        """Get detailed information for videos"""
        if not video_ids:
            return []
        
        youtube = self._get_client()
        videos_data = []
        
        # Process in batches of 50
        for i in range(0, len(video_ids), 50):
            batch_ids = video_ids[i:i+50]
            
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: youtube.videos().list(
                        part="snippet,statistics,contentDetails",
                        id=",".join(batch_ids),
                    ).execute()
                )
                
                for item in response.get("items", []):
                    snippet = item["snippet"]
                    statistics = item.get("statistics", {})
                    content_details = item.get("contentDetails", {})
                    
                    title = snippet.get("title", "")
                    channel = snippet.get("channelTitle", "")
                    category_id = snippet.get("categoryId", "")
                    
                    # Apply content filter
                    if not self._passes_content_filter(title, channel, category_id):
                        continue
                    
                    # Parse duration
                    raw_duration = content_details.get("duration", "PT0S")
                    duration_str, duration_seconds = self._parse_duration(raw_duration)
                    
                    # Check minimum duration
                    if duration_seconds <= config.MIN_DURATION_SECONDS:
                        continue
                    
                    # Parse published date
                    published_str = snippet.get("publishedAt", "")
                    published_at = datetime.strptime(published_str, "%Y-%m-%dT%H:%M:%SZ")
                    hours_ago = int((datetime.utcnow() - published_at).total_seconds() / 3600)
                    
                    # Determine category group
                    group_category = self._determine_category_group(title, channel)
                    
                    video_data = {
                        "video_id": item["id"],
                        "title": title,
                        "channel": channel,
                        "views": int(statistics.get("viewCount", 0)),
                        "likes": int(statistics.get("likeCount", 0)),
                        "comments": int(statistics.get("commentCount", 0)),
                        "category": self._get_category_name(category_id),
                        "category_id": category_id,
                        "duration": duration_str,
                        "duration_seconds": duration_seconds,
                        "published_at": published_at,
                        "hours_ago": hours_ago,
                        "url": f"https://www.youtube.com/watch?v={item['id']}",
                        "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                        "group_category": group_category,
                    }
                    videos_data.append(video_data)
                    
            except HttpError as e:
                print(f"Error fetching video details: {e}")
                continue
            
            # Rate limiting
            await asyncio.sleep(0.5)
        
        return videos_data
    
    def _determine_category_group(self, title: str, channel: str) -> str:
        """Determine if video belongs to birds/animals or animation category"""
        combined = f"{title.lower()} {channel.lower()}"
        
        # Priority 1: Birds & Animals
        animal_keywords = [
            "bird", "birds", "chilaka", "pichuka", "kaki", "pavuram",
            "animal", "animals", "elephant", "lion", "monkey", "rabbit",
            "turtle", "fox", "deer", "tiger", "bear", "horse", "panchatantra",
            "నీతి", "neethi", "neeti", "జంతువుల", "animal stories", "panchatantra"
        ]
        
        for keyword in animal_keywords:
            if keyword in combined:
                return "birds_animals"
        
        # Priority 2: Animation
        animation_keywords = [
            "cartoon", "animation", "animated", "toons", "anime",
            "cartoons", "2d animation", "3d animation"
        ]
        
        for keyword in animation_keywords:
            if keyword in combined:
                return "animation"
        
        # Default to animation
        return "animation"
    
    def _get_category_name(self, category_id: str) -> str:
        """Get category name from ID"""
        categories = {
            "1": "Film & Animation",
            "2": "Autos & Vehicles",
            "10": "Music",
            "15": "Pets & Animals",
            "17": "Sports",
            "19": "Travel & Events",
            "20": "Gaming",
            "22": "People & Blogs",
            "23": "Comedy",
            "24": "Entertainment",
            "25": "News & Politics",
            "26": "How-to & Style",
            "27": "Education",
            "28": "Science & Technology",
            "29": "Nonprofits",
        }
        return categories.get(category_id, "Other")
    
    def get_published_after(self) -> str:
        """Get ISO format date for N days ago"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=config.DAYS_BACK)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")