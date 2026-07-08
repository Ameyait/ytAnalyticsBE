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

# Define timezones
IST = ZoneInfo("Asia/Kolkata")
PACIFIC = ZoneInfo("America/Los_Angeles")  # YouTube quota resets at midnight PT


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
        # QUOTA SETTINGS — CORRECTED TWO-BUCKET MODEL (verified July 2026)
        # ============================================================
        # Since the June 1, 2026 YouTube Data API change, search.list has
        # its OWN separate quota bucket, capped at 100 CALLS/day, and each
        # call costs 1 unit *within that bucket* — it no longer draws from
        # the shared 10,000-unit/day pool used by videos.list, channels.list,
        # etc. (Official source: the search.list reference page — "Quota
        # impact: 100 calls per day. A call to this method has a quota cost
        # of 1 unit in the Search Queries quota bucket.")
        #
        # videos.list (used to fetch stats/details) is also a flat 1 unit
        # PER CALL regardless of how many video IDs or parts you request —
        # NOT 1 unit per video ID. (Official quota table: videos.list = 1.)
        #
        # Both buckets reset at midnight Pacific Time daily — we track that
        # explicitly below instead of only resetting on process restart.
        self.search_calls_used = 0
        self.search_calls_limit = 90     # real hard ceiling is 100/day; keep a small buffer
        self.quota_used = 0              # shared 10,000-unit pool (videos.list etc.)
        self.quota_limit = 9000          # real ceiling is 10,000/day; keep a small buffer
        self.quota_lock = asyncio.Lock()
        self._quota_reset_date = datetime.now(PACIFIC).date()
        
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
        # NEGATIVE QUERY — blocks romance/junk at the YouTube API level
        # ============================================================
        # search.list's `q` param supports the boolean "-" (NOT) operator
        # per-TERM (not per-phrase), so every token here is a single word.
        # Appended to EVERY search below so YouTube itself excludes these
        # before we ever spend a result slot on them (up to 50 results per
        # call, so keeping junk out at the source matters more than
        # filtering it out afterward).
        self.NEGATIVE_QUERY = (
            "-love -romance -romantic -boyfriend -girlfriend "
            "-kiss -kissing -lust -adult -18+ "
            "-movie -film -serial -webseries -episode "
            "-prank -vlog -reaction -shorts"
        )
        
        # ============================================================
        # QUOTA-OPTIMIZED, PRIORITY-TIERED SEARCH QUERIES
        # ============================================================
        # Each category is a LIST of (query, priority_weight, max_results)
        # tuples instead of one giant query, because YouTube sorts by
        # viewCount: OR-ing a 2.0-priority niche term (e.g. "Atha Kodalu
        # Kathalu") together with a 1.7-priority generic term (e.g. "Telugu
        # Learning Stories") in the SAME call lets the generic/high-view
        # term dominate the top-50 results, drowning out the niche one.
        # Splitting by priority tier gives every weight band its own
        # dedicated call so nothing gets crowded out.
        #
        # 8 final classification buckets (see _determine_group): moral,
        # animals, birds, rhymes, animation, cartoon, bedtime, stories
        # (stories has no dedicated search — it's the fallback for videos
        # that match generic terms in the "moral" tiers below but no
        # moral/neethi/panchatantra-specific keyword).
        #
        # Total = 14 search.list calls per full run. At 2 scrapes/day
        # that's 28 calls/day, well inside the real 100/day search-call
        # ceiling (and the 90/day self-imposed buffer above).
        # ============================================================
        
        self.SEARCH_QUERIES: Dict[str, List[Tuple[str, float, int]]] = {
            "moral": [
                ("Telugu Moral Stories|Moral Stories in Telugu|Neethi Kathalu Telugu|Neethi Katha Telugu|"
                 "Atha Kodalu|Atha Kodalu Stories|Atha vs Kodalu|Atha Kodalu Telugu|Atha Kodalu Kathalu|"
                 "Atta Kodalu Telugu|తెలుగు కథలు|నీతి కథలు|తెలుగు నీతి కథలు|పంచతంత్ర కథలు|అత్త కోడలు|అత్త కోడలు కథలు",
                 2.0, 50),
                ("Telugu Stories|Telugu Kathalu|Stories in Telugu|Panchatantra Telugu|Telugu Panchatantra Stories|"
                 "Kodalu Kathalu|Animated Telugu Moral Story|Animated Panchatantra Telugu",
                 1.9, 50),
                ("Kids Telugu Stories|Children Telugu Stories|Telugu Kids Stories|Educational Telugu Stories|"
                 "Telugu Story for Kids|Animated Telugu Stories",
                 1.8, 40),
                ("Telugu Learning Stories|Telugu Bedtime Moral Stories",
                 1.7, 30),
            ],
            "animals": [
                ("Animal Stories Telugu|Animals Stories Telugu|Telugu Animal Stories|Animal Kathalu Telugu|"
                 "సింహం కథ|పులి కథ|ఏనుగు కథ|కోతి కథ|కుందేలు కథ|నక్క కథ|జింక కథ",
                 2.0, 50),
                ("Wild Animal Stories Telugu|Jungle Stories Telugu|Lion Stories Telugu|Tiger Stories Telugu|"
                 "Elephant Stories Telugu|Monkey Stories Telugu|Rabbit Stories Telugu|Fox Stories Telugu|"
                 "Lion Kathalu Telugu|Tiger Kathalu Telugu|Monkey Kathalu Telugu|Elephant Kathalu Telugu",
                 1.9, 50),
                ("Deer Stories Telugu|Dog Stories Telugu|Cat Stories Telugu|Bear Stories Telugu|Wolf Stories Telugu",
                 1.8, 30),
            ],
            "birds": [
                ("Bird Stories Telugu|Birds Stories Telugu|Chilaka Stories|Chilaka Kathalu|Pichuka Stories|"
                 "Pichuka Kathalu|Pavuram Stories|Pavuram Kathalu|Kaki Stories|Kaki Kathalu|"
                 "చిలక కథలు|పిచుక కథలు|కాకి కథలు|పావురం కథలు",
                 2.0, 50),
                ("Bird Cartoon Stories Telugu|Crow Stories Telugu|Parrot Stories Telugu",
                 1.9, 30),
            ],
            "rhymes": [
                ("Telugu Nursery Rhymes|Nursery Rhymes Telugu|Telugu Nursery Rhymes for Kids|Telugu Rhymes|"
                 "Kids Rhymes Telugu|Telugu Kids Rhymes",
                 1.9, 50),
                ("Telugu Kids Songs|Kids Songs Telugu|ABC Songs Telugu|Learning Songs Telugu",
                 1.8, 30),
            ],
            "animation": [
                ("Telugu Animation Stories|Animated Telugu Stories|Animated Telugu Moral Story|"
                 "Animated Panchatantra Telugu|Animation Stories Telugu|3D Animation Telugu Stories|"
                 "Kids Animation Telugu",
                 1.9, 50),
            ],
            "bedtime": [
                ("Telugu Bedtime Stories|Kids Bedtime Stories Telugu|Telugu Fairy Tales|Sleep Stories Telugu|"
                 "Night Stories Telugu|Magical Stories Telugu|Bedtime Moral Stories Telugu",
                 1.9, 40),
            ],
            "cartoon": [
                ("Telugu Cartoon Stories|Kids Cartoon Telugu|Cartoon Stories Telugu|Telugu Kids Cartoon Story",
                 1.9, 40),
            ],
        }
        
        # ============================================================
        # ENHANCED FILTERS
        # ============================================================
        
        self.MUST_CONTAIN_ANY = [
            "story", "stories", "kathalu", "katha",
            "moral", "neethi", "neeti", "panchatantra",
            "kids", "children", "child",
            "rhymes", "nursery", "cartoon", "animation",
            "song", "songs", "learning",
            "bird", "birds", "chilaka", "pichuka", "pavuram", "kaki",
            "bedtime", "fairy", "fairytale", "fairy tale", "tales",
            "educational",
            
            # Animals category
            "animal", "animals", "jungle", "lion", "tiger", "elephant",
            "monkey", "rabbit", "fox", "deer", "dog", "cat", "bear", "wolf",
            
            # Telugu script
            "కథ", "కథలు", "నీతి", "పిల్లల", "పక్షి",
            "సింహం", "పులి", "ఏనుగు", "కోతి", "కుందేలు", "నక్క", "జింక",
        ]
        
        self.MUST_NOT_CONTAIN = [
            "trailer", "teaser", "movie", "film", "cinema",
            "review", "reaction", "interview",
            "gaming", "gameplay", "gta", "minecraft", "freefire", "pubg", "bgmi",
            "news", "breaking", "election", "government", "politics",
            "cricket", "ipl", "match", "football",
            "web series", "serial", "prank", "vlog",
            "adult", "18+", "short film",
            "live", "streaming", "podcast", "behind the scenes",

            # ============================================================
            # MUSIC/FILM JUNK — needed now that YouTube category 10 (Music)
            # is allowed (see ALLOWED_CATEGORIES below), so real kids'
            # rhymes/songs get in without opening the door to movie songs
            # ============================================================
            "item song", "music video", "audio song", "video song",
            "lyrical video", "full video song", "album", "jukebox",
            "audio jukebox", "dj songs", "remix",

            # ============================================================
            # ROMANCE / LOVE — backstop behind NEGATIVE_QUERY above (in
            # case a video slips through the API-level exclusion, e.g. the
            # term only appears in the description, not the title)
            # ============================================================
            "love", "love story", "love stories", "romance", "romantic",
            "boyfriend", "girlfriend", "crush", "proposal", "kiss", "kissing",
            "honeymoon", "dating", "relationship", "lust", "affair", "hot", "sexy",
            "ప్రేమ", "ప్రేమ కథ", "లవ్", "రోమాన్స్", "ముద్దు", "సెక్స్", "వ్యభిచారం",

            # ============================================================
            # OTHER STRICT NEGATIVES FROM ORIGINAL SPEC (previously missing)
            # ============================================================
            "astrology", "health tips", "beauty tips", "makeup",
            "recipe", "cooking", "travel vlog", "comedy show", "standup",
            "memes", "status", "viral", "challenge", "experiment",
            "wedding", "festival vlog", "horror", "ghost", "devotional",
            "crime", "murder", "dance", "shorts",
            
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
            "10",  # Music — many Nursery Rhymes / Kids Songs channels file
                   # here instead of Education, so excluding it was silently
                   # killing recall for the "rhymes" bucket specifically.
                   # Safe to allow because MUST_NOT_CONTAIN above now blocks
                   # item songs / music videos / albums / jukeboxes.
            "15",  # Pets & Animals — relevant now for the "animals" bucket too
            "22",  # People & Blogs
            "23",  # Comedy
            "24",  # Entertainment
            "27",  # Education
        }
        
        self.CATEGORY_MAP = {
            "1": "Film & Animation",
            "10": "Music",
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
        """Backward compatibility - flatten all search queries (all tiers)"""
        keywords = []
        for query_list in self.SEARCH_QUERIES.values():
            for query, _, _ in query_list:
                keywords.append(query)
        return keywords
    
    # ============================================================
    # QUOTA MANAGEMENT — dual bucket, resets daily at midnight Pacific
    # ============================================================
    
    async def _maybe_reset_daily_quota(self):
        """YouTube resets both quota buckets at midnight Pacific Time.
        The service instance lives for the lifetime of the app process
        (created once in ScraperService.__init__), so without this check
        quota_used/search_calls_used would just accumulate forever and
        eventually self-throttle every run even though YouTube's real
        quota had long since reset."""
        today = datetime.now(PACIFIC).date()
        if today != self._quota_reset_date:
            print(f"🔄 New day in Pacific Time ({today}) — resetting quota counters")
            self.search_calls_used = 0
            self.quota_used = 0
            self._quota_reset_date = today
    
    async def check_search_quota(self) -> bool:
        """search.list has its own 100-calls/day bucket (1 unit/call)."""
        async with self.quota_lock:
            await self._maybe_reset_daily_quota()
            if self.search_calls_used + 1 > self.search_calls_limit:
                print(f"⚠ Search-call limit reached! Used: {self.search_calls_used}/{self.search_calls_limit} today")
                return False
            self.search_calls_used += 1
            return True
    
    async def check_quota(self, required_units: int = 1) -> bool:
        """Shared 10,000-unit/day pool (videos.list, channels.list, etc.)."""
        async with self.quota_lock:
            await self._maybe_reset_daily_quota()
            if self.quota_used + required_units > self.quota_limit:
                print(f"⚠ Unit quota limit reached! Used: {self.quota_used}/{self.quota_limit}")
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
    # GROUP DETERMINATION (maps every sub-keyword to 1 of 8 final buckets)
    # ============================================================
    
    def _determine_group(self, title: str, channel: str) -> str:
        """Categorize with priority scoring into exactly one of:
        birds, animals, rhymes, cartoon, animation, bedtime, moral, stories
        """
        combined = f"{title.lower()} {channel.lower()}"
        
        categories = {
            "birds": ([
                r"\bbird\b", r"\bbirds\b",
                r"\bchilaka\b", r"\bpichuka\b", r"\bpavuram\b", r"\bkaki\b",
                "chilaka kathalu", "birds stories", "bird stories",
            ], 100),
            
            "animals": ([
                r"\banimal\b", r"\banimals\b", r"\bjungle\b", "wild animal",
                r"\blion\b", r"\btiger\b", r"\belephant\b", r"\bmonkey\b",
                r"\brabbit\b", r"\bfox\b", r"\bdeer\b", r"\bdog\b", r"\bcat\b",
                r"\bbear\b", r"\bwolf\b",
                "సింహం", "పులి", "ఏనుగు", "కోతి", "కుందేలు", "నక్క", "జింక",
            ], 95),
            
            "rhymes": ([
                r"\brhyme\b", r"\brhymes\b", r"\bnursery\b",
                r"\bsong\b", r"\bsongs\b", r"\blullaby\b",
                "nursery rhymes", "kids rhymes", "learning song", "learning songs",
                "abc rhymes", "abc songs", "alphabet song",
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
                r"\bfairy\s*tale", "fairy tales", "fairytale",
            ], 60),
            
            "moral": ([
                r"\bmoral\b", r"\bneethi\b", r"\bneeti\b",
                r"\bpanchatantra\b", "atha kodalu", "neethi kathalu",
            ], 50),
            
            "stories": ([
                r"\bstory\b", r"\bstories\b",
                r"\bkatha\b", r"\bkathalu\b",
                "kids story", "telugu stories",
                r"\beducational\b", "educational story", "educational stories",
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
        category: str,
        query: str,
        published_after: str,
        priority: float = 1.0,
        max_results: int = 50,
    ) -> List[str]:
        """Run one quota-efficient OR-combined search for one priority tier
        of a final category, with romance/junk excluded at the API level.
        """
        
        if not await self.check_search_quota():
            return []
        
        youtube = self._get_client()
        video_ids = []
        full_query = f"{query} {self.NEGATIVE_QUERY}"
        
        try:
            loop = asyncio.get_event_loop()
            
            response = await loop.run_in_executor(
                None,
                lambda: youtube.search().list(
                    part="snippet",
                    q=full_query,
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
            
            print(f"✓ [{category}] priority={priority} → {len(new_ids)}/{len(ids)} new "
                  f"(search calls: {self.search_calls_used}/{self.search_calls_limit} today)")
            
        except HttpError as e:
            print(f"⚠ Error '{category}' ({query}): {e}")
            if "quota" in str(e).lower():
                self.search_calls_used = self.search_calls_limit
        
        return list(set(video_ids))
    
    async def search_all_keywords(self, published_after: str) -> List[str]:
        """Search using priority-tiered OR-combined queries per category.

        14 calls total per run (2 scrapes/day = 28/day) vs. the real
        100-calls/day search bucket ceiling — comfortable headroom while
        covering far more sub-keywords than a single query per category.
        """
        all_video_ids = []
        
        for category, query_list in self.SEARCH_QUERIES.items():
            for tier_idx, (query, priority, max_results) in enumerate(query_list, 1):
                video_ids = await self.search_keyword(category, query, published_after, priority, max_results)
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
            
            # videos.list costs a flat 1 unit PER CALL, regardless of how
            # many IDs are in the batch (up to 50) or how many `part`
            # values are requested — NOT 1 unit per video ID.
            await self.check_quota(1)
            
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
                "search_calls_used": self.search_calls_used,
                "search_calls_limit": self.search_calls_limit,
                "total_quota_used": self.quota_used,
                "quota_limit": self.quota_limit,
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
        print(f"🔍 Search calls used: {self.search_calls_used}/{self.search_calls_limit} (resets midnight PT)")
        print(f"💾 Unit quota used: {self.quota_used}/{self.quota_limit} (shared pool)")
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