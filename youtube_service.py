import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Tuple, Set, Optional
import asyncio
import hashlib
from collections import defaultdict
from dataclasses import dataclass, asdict
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import config

# Define IST timezone
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class VideoData:
    """Type-safe video data structure"""
    video_id: str
    title: str
    channel: str
    views: int
    likes: int
    comments: int
    duration: str
    duration_seconds: int
    category: str
    group_category: str
    published_at: datetime
    url: str
    thumbnail: str
    content_hash: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VideoCache:
    """In-memory cache for duplicate detection"""
    
    def __init__(self, ttl_minutes: int = 60):
        self.cache: Dict[str, Tuple[datetime, str]] = {}
        self.ttl = timedelta(minutes=ttl_minutes)
    
    def get_key(self, title: str, channel: str, duration: int) -> str:
        """Generate cache key from video attributes"""
        normalized_title = re.sub(r'[^\w\s]', '', title.lower().strip())
        normalized_channel = channel.lower().strip()
        content = f"{normalized_title}|{normalized_channel}|{duration}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def is_duplicate(self, title: str, channel: str, duration: int) -> bool:
        """Check if video is a duplicate"""
        cache_key = self.get_key(title, channel, duration)
        
        if cache_key in self.cache:
            timestamp, _ = self.cache[cache_key]
            if datetime.now(IST) - timestamp < self.ttl:
                return True
            else:
                del self.cache[cache_key]
        return False
    
    def add_video(self, title: str, channel: str, duration: int, video_id: str):
        """Add video to cache"""
        cache_key = self.get_key(title, channel, duration)
        self.cache[cache_key] = (datetime.now(IST), video_id)
    
    def clear_expired(self):
        """Remove expired cache entries"""
        now = datetime.now(IST)
        expired = [k for k, (ts, _) in self.cache.items() if now - ts > self.ttl]
        for key in expired:
            del self.cache[key]


class BatchProcessor:
    """Handles batch operations for better performance"""
    
    def __init__(self, batch_size: int = 50, delay_seconds: float = 0.1):
        self.batch_size = batch_size
        self.delay_seconds = delay_seconds
    
    async def process_batches(self, items: List, processor_func, **kwargs):
        """Process items in batches with rate limiting"""
        results = []
        
        for i in range(0, len(items), self.batch_size):
            batch = items[i:i + self.batch_size]
            batch_results = await processor_func(batch, **kwargs)
            results.extend(batch_results)
            
            # Adaptive delay based on quota usage
            if i + self.batch_size < len(items):
                await asyncio.sleep(self.delay_seconds)
        
        return results


class YouTubeService:
    def __init__(self):
        self.api_key = config.YOUTUBE_API_KEY
        self.youtube = None
        
        # ============================================================
        # QUOTA SETTINGS
        # ============================================================
        
        self.quota_used = 0
        self.quota_limit = 4000
        self.quota_lock = asyncio.Lock()
        
        # ============================================================
        # DUPLICATE DETECTION
        # ============================================================
        
        self.video_cache = VideoCache(ttl_minutes=120)  # 2 hour cache
        self.seen_urls: Set[str] = set()
        self.seen_hashes: Set[str] = set()
        
        # ============================================================
        # BATCH PROCESSING
        # ============================================================
        
        self.batch_processor = BatchProcessor(batch_size=50, delay_seconds=0.1)
        
        # ============================================================
        # ENHANCED KEYWORDS WITH WEIGHTS
        # ============================================================
        
        # Weighted keywords for better search quality
        self.SEARCH_QUERIES = {
            "high_priority": [
                # High-value content
                ("Telugu moral stories", 1.5),
                ("Telugu panchatantra stories", 1.5),
                ("Neethi Kathalu telugu", 1.5),
                ("Birds stories telugu", 1.5),  # Increased priority
                ("Chilaka Kathalu", 1.5),  
                ("Telugu bird stories for kids", 1.4),
                ("Chilaka stories in telugu", 1.4),
                ("Telugu nursery rhymes", 1.3),
                ("Telugu animation stories", 1.3),
            ],
            "medium_priority": [
                # Mid-value content
                ("Telugu stories for kids", 1.0),
                ("Telugu kathalu", 1.0),
                ("Telugu cartoon stories", 1.0),
                ("Kids rhymes telugu", 1.0),
                ("Atha kodalu stories", 1.0),
            ],
            "long_tail": [
                # Long-tail keywords
                ("Telugu bedtime stories", 0.8),
                ("Telugu fairy tales", 0.8),
                ("Educational stories telugu", 0.8),
            ]
        }
        
        # ============================================================
        # ENHANCED FILTERS
        # ============================================================
        
        self.MUST_CONTAIN_ANY = [
            "story", "stories", "kathalu", "katha",
            "moral", "neethi", "neeti",
            "kids", "children", "child",
            "rhymes", "nursery", "cartoon", "animation",
            "song", "songs",
            "chilaka", "pichuka", "pavuram", "kaki",
            "panchatantra", "bedtime",
            
            # Telugu script
            "కథ", "కథలు", "నీతి", "పిల్లల",
        ]
        
        self.MUST_NOT_CONTAIN = [
            "trailer", "teaser", "movie", "film", "cinema",
            "review", "reaction", "interview",
            "gaming", "gameplay", "gta", "minecraft", "freefire",
            "news", "breaking", "election", "government",
            "cricket", "ipl", "match", "football",
            "web series", "serial", "prank", "vlog",
            "adult", "18+", "romance", "couple", "short film",
            "live", "streaming", "podcast", "behind the scenes",
            
            # ============================================================
            # HINDI LANGUAGE BLOCKERS
            # ============================================================
            "hindi", "हिंदी", "हिन्दी",  # Hindi in English and Devanagari
            "hindi story", "hindi stories", "hindi kahani",
            "hindi rhymes", "hindi cartoon", "hindi animation",
            "akbar birbal", "birbal", "अकबर", "बीरबल",
            "panchtantra stories", "panchatantra hindi",
            "moral stories in hindi", "hindi moral",
            "hindi nursery rhymes", "hindi kids",
            "bal kahani", "bachon ki kahaniyan",
            "hindi me", "hindi mai",  # "in Hindi"
        ]
        
        # Channel blacklist (low-quality sources)
        self.CHANNEL_BLACKLIST = {
            "t series", "zeemusic", "sony music", 
            "tips official", "wave music", "speed records",
        }
        
        # ============================================================
        # ALLOWED CATEGORIES
        # ============================================================
        
        self.ALLOWED_CATEGORIES = {
            "1",   # Film & Animation
            "15",  # Pets & Animals
            "22",  # People & Blogs
            "23",  # Comedy
            "24",  # Entertainment
            "27",  # Education
        }
        
        self.CATEGORY_MAP = {
            "1": "Film & Animation",
            "15": "Pets & Animals",
            "22": "People & Blogs",
            "23": "Comedy",
            "24": "Entertainment",
            "27": "Education",
        }
        
        # ============================================================
        # PERFORMANCE METRICS
        # ============================================================
        
        self.metrics = {
            "total_searches": 0,
            "total_videos_found": 0,
            "duplicates_filtered": 0,
            "quality_filtered": 0,
            "api_calls": 0,
            "start_time": None,
        }
    
    # ============================================================
    # PROPERTIES
    # ============================================================
    
    @property
    def ALL_KEYWORDS(self) -> List[str]:
        """Backward compatibility - flatten all search queries"""
        all_keywords = []
        for priority_level, queries in self.SEARCH_QUERIES.items():
            for query_data in queries:
                if isinstance(query_data, tuple):
                    all_keywords.append(query_data[0])
                else:
                    all_keywords.append(query_data)
        return all_keywords
    
    # ============================================================
    # QUOTA MANAGEMENT
    # ============================================================
    
    async def check_quota(self, required_units: int = 100) -> bool:
        """Check if enough quota is available"""
        async with self.quota_lock:
            if self.quota_used + required_units > self.quota_limit:
                print(f"⚠ Quota limit reached! Used: {self.quota_used}/{self.quota_limit}")
                return False
            self.quota_used += required_units
            return True
    
    def _get_client(self):
        if not self.youtube:
            self.youtube = build(
                "youtube",
                "v3",
                developerKey=self.api_key,
                cache_discovery=False,
            )
        return self.youtube
    
    # ============================================================
    # DURATION PARSER
    # ============================================================
    
    def _parse_duration(self, duration_str: str) -> Tuple[str, int]:
        """Parse ISO 8601 duration with improved error handling"""
        if not duration_str or duration_str == "PT0S":
            return "0s", 0
        
        match = re.match(
            r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
            duration_str
        )
        
        if not match:
            match = re.match(r"(\d+):(\d+):(\d+)", duration_str)
            if match:
                hours = int(match.group(1))
                minutes = int(match.group(2))
                seconds = int(match.group(3))
                total = hours * 3600 + minutes * 60 + seconds
                return f"{hours}h {minutes}m", total
        
        if not match:
            return "?", 0
        
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        
        total = hours * 3600 + minutes * 60 + seconds
        
        if hours:
            return f"{hours}h {minutes}m", total
        elif minutes:
            return f"{minutes}m {seconds}s", total
        return f"{seconds}s", total
    
    # ============================================================
    # GROUP DETERMINATION
    # ============================================================
    
    def _determine_group(self, title: str, channel: str) -> str:
        """Categorize with priority scoring"""
        combined = f"{title.lower()} {channel.lower()}"
        
        categories = {
            "birds": ([
                r"\bbird\b", r"\bbirds\b",
                r"\bchilaka\b", r"\bpichuka\b", r"\bpavuram\b", r"\bkaki\b",
                "chilaka kathalu", "birds stories",
            ], 100),
            
            "rhymes": ([
                r"\brhyme\b", r"\brhymes\b", r"\bnursery\b",
                r"\bsong\b", r"\bsongs\b", r"\blullaby\b",
                "nursery rhymes", "kids rhymes",
            ], 90),
            
            "cartoon": ([
                r"\bcartoon\b", r"\bcartoons\b",
                "animated cartoon", "telugu cartoon",
            ], 80),
            
            "animation": ([
                r"\banimation\b",
                "animated story", "animation story",
            ], 70),
            
            "bedtime": ([
                r"\bbedtime\b", "sleep story", "night story",
            ], 60),
            
            "moral": ([
                r"\bmoral\b", r"\bneethi\b", r"\bneeti\b",
                r"\bpanchatantra\b", "atha kodalu", "neethi kathalu",
            ], 50),
            
            "stories": ([
                r"\bstory\b", r"\bstories\b",
                r"\bkatha\b", r"\bkathalu\b",
                "kids story", "telugu stories",
            ], 10),
        }
        
        best_category = "stories"
        best_score = -1
        
        for category, (patterns, priority) in categories.items():
            for pattern in patterns:
                if re.search(pattern, combined, re.IGNORECASE):
                    if priority > best_score:
                        best_score = priority
                        best_category = category
                    break
        
        return best_category
    
    # ============================================================
    # SEARCH METHODS
    # ============================================================
    
    async def search_keyword(
        self,
        keyword: str,
        published_after: str,
        priority: float = 1.0
    ) -> List[str]:
        """Search with priority-based quota management"""
        
        if not await self.check_quota(100):
            return []
        
        youtube = self._get_client()
        video_ids = []
        
        try:
            max_results = int(15 * priority) if priority > 1 else 10
            
            loop = asyncio.get_event_loop()
            
            response = await loop.run_in_executor(
                None,
                lambda: youtube.search().list(
                    part="snippet",
                    q=keyword,
                    type="video",
                    regionCode="IN",
                    maxResults=max_results,
                    order="viewCount",
                    relevanceLanguage="te",
                    safeSearch="strict",
                    publishedAfter=published_after,
                    videoDuration="medium",
                ).execute()
            )
            
            ids = [
                item["id"]["videoId"]
                for item in response.get("items", [])
            ]
            
            new_ids = []
            for vid in ids:
                if vid not in self.seen_urls:
                    new_ids.append(vid)
                    self.seen_urls.add(vid)
            
            video_ids.extend(new_ids)
            
            self.metrics["total_searches"] += 1
            self.metrics["total_videos_found"] += len(ids)
            self.metrics["duplicates_filtered"] += (len(ids) - len(new_ids))
            
            print(f"✓ {keyword} (priority={priority}) → {len(new_ids)}/{len(ids)} new videos")
            
        except HttpError as e:
            print(f"⚠ Error '{keyword}': {e}")
            if "quota" in str(e).lower():
                self.quota_used = self.quota_limit
        
        return list(set(video_ids))
    
    async def search_all_keywords(self, published_after: str) -> List[str]:
        """Search using all keyword categories with prioritization"""
        all_video_ids = []
        
        for priority_level, queries in self.SEARCH_QUERIES.items():
            for query_data in queries:
                if isinstance(query_data, tuple):
                    keyword, priority = query_data
                else:
                    keyword, priority = query_data, 1.0
                
                video_ids = await self.search_keyword(keyword, published_after, priority)
                all_video_ids.extend(video_ids)
                await asyncio.sleep(0.2)
        
        return list(set(all_video_ids))
    
    # ============================================================
    # VIDEO DETAILS METHODS (FIXED)
    # ============================================================
    
    async def fetch_batch_details(self, batch_ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch details for a batch of videos"""
        youtube = self._get_client()
        videos_data = []
        
        try:
            loop = asyncio.get_event_loop()
            
            response = await loop.run_in_executor(
                None,
                lambda: youtube.videos().list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(batch_ids),
                ).execute()
            )
            
            items = response.get("items", [])
            print(f"  📦 Processing batch of {len(items)} videos...")
            
            for item in items:
                try:
                    video = await self.process_video_item(item)
                    if video and isinstance(video, dict):
                        videos_data.append(video)
                except Exception as e:
                    print(f"    ⚠️ Error processing item: {e}")
                    continue
            
            await self.check_quota(len(batch_ids))
            
        except HttpError as e:
            print(f"  ✗ Batch Details Error: {e}")
        except Exception as e:
            print(f"  ✗ Unexpected error: {e}")
        
        return videos_data
    
    async def process_video_item(self, item: Dict) -> Optional[Dict[str, Any]]:
        """Process individual video item with all filters"""
        
        snippet = item["snippet"]
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        
        video_id = item["id"]
        title = snippet.get("title", "")
        channel = snippet.get("channelTitle", "")
        channel_id = snippet.get("channelId", "")
        cat_id = snippet.get("categoryId", "")
        
        # Fast filters
        if cat_id not in self.ALLOWED_CATEGORIES:
            return None
        
        title_lower = title.lower()
        channel_lower = channel.lower()
        
        if any(bad in channel_lower for bad in self.CHANNEL_BLACKLIST):
            self.metrics["quality_filtered"] += 1
            return None
        
        if any(bad in title_lower for bad in self.MUST_NOT_CONTAIN):
            self.metrics["quality_filtered"] += 1
            return None
        
        raw_dur = content.get("duration", "PT0S")
        duration_text, duration_sec = self._parse_duration(raw_dur)
        
        group = self._determine_group(title, channel)

        # Duration limits
        if group == "rhymes":
            if duration_sec < 120 or duration_sec > 1200:
                return None
        elif group == "birds":
            if duration_sec < 60 or duration_sec > 900:
                return None
        else:
            if duration_sec < 180 or duration_sec > 1800:
                return None
        
        combined = f"{title_lower} {channel_lower}"
        if not any(good in combined for good in self.MUST_CONTAIN_ANY):
            return None
        
        if self.video_cache.is_duplicate(title, channel, duration_sec):
            self.metrics["duplicates_filtered"] += 1
            return None
        
        content_hash = hashlib.md5(
            f"{title_lower}|{channel_lower}|{duration_sec}".encode()
        ).hexdigest()
        
        if content_hash in self.seen_hashes:
            self.metrics["duplicates_filtered"] += 1
            return None
        
        # ============================================================
        # TIMEZONE HANDLING - FIXED
        # ============================================================
        
        # Step 1: Parse YouTube published time (UTC)
        published_str = snippet.get("publishedAt", "")
        published_at = datetime.strptime(published_str, "%Y-%m-%dT%H:%M:%SZ")
        published_at_utc = published_at.replace(tzinfo=timezone.utc)
        
        # Step 2: Calculate hours_ago using UTC (for display purposes)
        now_utc = datetime.now(timezone.utc)
        hours_ago = int((now_utc - published_at_utc).total_seconds() / 3600)
        
        # Step 3: Strip timezone for database storage (since DB uses TIMESTAMP WITHOUT TIME ZONE)
        published_at_naive = published_at_utc.replace(tzinfo=None)
        
        # ============================================================
        # END TIMEZONE HANDLING
        # ============================================================
        
        channel_url = f"https://youtube.com/channel/{channel_id}" if channel_id else ""
        
        video_data = {
            "video_id": video_id,
            "title": title,
            "channel": channel,
            "channel_id": channel_id,
            "channel_url": channel_url,
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
            "duration": duration_text,
            "duration_seconds": duration_sec,
            "category": self.CATEGORY_MAP.get(cat_id, "Unknown"),
            "category_id": cat_id,
            "group_category": group,
            "published_at": published_at_naive,  # <-- NAIVE datetime for DB
            "hours_ago": hours_ago,
            "url": f"https://youtube.com/watch?v={video_id}",
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "content_hash": content_hash,
        }
        
        self.video_cache.add_video(title, channel, duration_sec, video_id)
        self.seen_hashes.add(content_hash)
        
        return video_data
    
    async def get_video_details(self, video_ids: List[str]) -> List[Dict[str, Any]]:
        """Get details with batch processing - FIXED: No datetime conversion here"""
        
        if not video_ids:
            return []
        
        print(f"\n📥 Fetching details for {len(video_ids)} videos...")
        
        videos_data = await self.batch_processor.process_batches(
            video_ids,
            self.fetch_batch_details
        )
        
        all_videos = []
        for batch_result in videos_data:
            if isinstance(batch_result, list):
                all_videos.extend(batch_result)
            elif isinstance(batch_result, dict):
                all_videos.append(batch_result)
        
        print(f"✅ Successfully fetched {len(all_videos)} video details")
        return all_videos
    
    # ============================================================
    # DATABASE PREPARATION
    # ============================================================
    
    def prepare_for_database(self, videos: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Prepare video data for efficient database storage"""
        
        if not videos:
            return {"videos": [], "summary": {}}
        
        grouped = defaultdict(list)
        for video in videos:
            category = video.get("group_category", "stories")
            grouped[category].append(video)
        
        for category in grouped:
            grouped[category].sort(key=lambda x: x.get("views", 0), reverse=True)
        
        summary = {
            "total_videos": len(videos),
            "categories": {
                cat: len(videos_list) for cat, videos_list in grouped.items()
            },
            "total_views": sum(v.get("views", 0) for v in videos),
            "avg_duration": sum(v.get("duration_seconds", 0) for v in videos) / len(videos) if videos else 0,
            "date_range": {
                "oldest": min(v.get("published_at") for v in videos) if videos else None,
                "newest": max(v.get("published_at") for v in videos) if videos else None,
            },
            "top_channels": self._get_top_channels(videos, 10),
        }
        
        return {
            "videos": videos,
            "grouped_videos": dict(grouped),
            "summary": summary,
            "metadata": {
                # Keep database metadata in UTC (as string)
                "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
                "total_quota_used": self.quota_used,
                "metrics": self.metrics,
            }
        }
    
    def _get_top_channels(self, videos: List[Dict], limit: int = 10) -> List[Dict]:
        """Extract top channels by video count"""
        channel_counts = defaultdict(int)
        channel_views = defaultdict(int)
        
        for video in videos:
            channel = video.get("channel", "Unknown")
            channel_counts[channel] += 1
            channel_views[channel] += video.get("views", 0)
        
        top_channels = []
        for channel in sorted(channel_counts, key=channel_counts.get, reverse=True)[:limit]:
            top_channels.append({
                "name": channel,
                "video_count": channel_counts[channel],
                "total_views": channel_views[channel],
            })
        
        return top_channels
    
    # ============================================================
    # MAIN FETCH METHOD
    # ============================================================
    
    async def fetch_all_videos(self) -> Dict[str, Any]:
        """Main method to fetch all videos with all improvements"""
        
        # Update metrics start time to IST
        self.metrics["start_time"] = datetime.now(IST)
        
        print("🚀 Starting YouTube video fetch...")
        print("=" * 50)
        
        published_after = self.get_published_after()
        
        print("📡 Searching for videos...")
        video_ids = await self.search_all_keywords(published_after)
        
        print(f"\n📊 Found {len(video_ids)} unique videos after deduplication")
        
        print("\n📥 Fetching video details...")
        videos = await self.get_video_details(video_ids)
        
        # Update elapsed calculation with IST
        elapsed = (
            datetime.now(IST) - self.metrics["start_time"]
        ).total_seconds()
        self.metrics["total_time_seconds"] = elapsed
        
        print("\n💾 Preparing data for database...")
        db_ready_data = self.prepare_for_database(videos)
        
        print("\n" + "=" * 50)
        print("✅ FETCH COMPLETE")
        print("=" * 50)
        print(f"📊 Total videos fetched: {len(videos)}")
        print(f"🎯 Categories: {db_ready_data['summary']['categories']}")
        print(f"⏱️  Time taken: {elapsed:.2f} seconds")
        print(f"💾 Quota used: {self.quota_used}/{self.quota_limit}")
        print(f"🔄 Duplicates filtered: {self.metrics['duplicates_filtered']}")
        print(f"🎨 Quality filtered: {self.metrics['quality_filtered']}")
        
        return db_ready_data
    
    # ============================================================
    # DATE FILTER
    # ============================================================
    
    def get_published_after(self, days: int = None) -> str:
        """Get ISO format date for filtering - Uses config.DAYS_BACK"""
        if days is None:
            days = config.DAYS_BACK
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")