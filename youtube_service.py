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
        # QUOTA SETTINGS — two-bucket model (search.list has its own
        # 100-calls/day bucket, separate from the shared 10,000-unit
        # pool used by videos.list etc.). Resets daily at midnight PT.
        # ============================================================
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
        # search.list's `q` param supports "-" (NOT) per single TERM, not
        # per phrase, so every token here is one word. Applied to every
        # search below, regardless of language.
        self.NEGATIVE_QUERY = (
            "-love -romance -romantic -boyfriend -girlfriend "
            "-kiss -kissing -lust -adult -18+ "
            "-movie -film -serial -webseries -episode "
            "-prank -vlog -reaction -shorts"
        )
        
        # ============================================================
        # SEARCH QUERIES — 6 FINAL CATEGORIES, PRIORITY-TIERED
        # ============================================================
        # "moral", "animation", and "stories" are now ONE merged category
        # called "moral" (per request) — it absorbs the old animation
        # search terms as an extra tier, and is the ONLY category where
        # Hindi and Tamil content is allowed (2 dedicated tiers below,
        # each with its own relevanceLanguage). Every other category
        # (animals, birds, rhymes, cartoon, bedtime) stays Telugu-only.
        #
        # Each tuple = (query, priority_weight, max_results, language).
        # Tiering by priority weight stops a low-weight generic term from
        # drowning out a high-weight niche one when YouTube sorts by
        # viewCount within a single OR'd call.
        #
        # Total = 16 search.list calls/run → 32/day at 2 scrapes/day,
        # still comfortably under the real 100-calls/day search bucket.
        # ============================================================
        
        self.SEARCH_QUERIES: Dict[str, List[Tuple[str, float, int, str]]] = {
            "moral": [
                # tier 1 (2.0) — Telugu: core moral + Atha Kodalu
                ("Telugu Moral Stories|Moral Stories in Telugu|Neethi Kathalu Telugu|Neethi Katha Telugu|"
                 "Atha Kodalu|Atha Kodalu Stories|Atha vs Kodalu|Atha Kodalu Telugu|Atha Kodalu Kathalu|"
                 "Atta Kodalu Telugu|తెలుగు కథలు|నీతి కథలు|తెలుగు నీతి కథలు|పంచతంత్ర కథలు|అత్త కోడలు|అత్త కోడలు కథలు",
                 2.0, 50, "te"),
                # tier 2 (1.9) — Telugu: general stories + panchatantra
                ("Telugu Stories|Telugu Kathalu|Stories in Telugu|Panchatantra Telugu|Telugu Panchatantra Stories|"
                 "Kodalu Kathalu|Animated Telugu Moral Story|Animated Panchatantra Telugu",
                 1.9, 50, "te"),
                # tier 3 (1.8) — Telugu: kids/educational framing
                ("Kids Telugu Stories|Children Telugu Stories|Telugu Kids Stories|Educational Telugu Stories|"
                 "Telugu Story for Kids|Animated Telugu Stories",
                 1.8, 40, "te"),
                # tier 4 (1.7) — Telugu: smaller long-tail
                ("Telugu Learning Stories|Telugu Bedtime Moral Stories",
                 1.7, 30, "te"),
                # tier 5 (1.9) — Telugu: animation, folded in from the old
                # separate "animation" category (now merged into moral)
                ("Telugu Animation Stories|Animation Stories Telugu|3D Animation Telugu Stories|Kids Animation Telugu",
                 1.9, 40, "te"),
                # tier 6 — HINDI (moral is the ONLY category allowing Hindi)
                ("Moral Stories in Hindi|Hindi Moral Stories for Kids|Neeti Kahani Hindi|Panchatantra Kahaniyan|"
                 "Akbar Birbal Kahani|Hindi Kahaniyan for Kids",
                 1.8, 50, "hi"),
                # tier 7 — TAMIL (moral is the ONLY category allowing Tamil)
                ("Moral Stories in Tamil|Tamil Kathaigal|Panchatantra Kathaigal Tamil|Neethi Kathaigal Tamil|"
                 "Tamil Moral Stories for Kids",
                 1.8, 50, "ta"),
            ],
            "animals": [
                ("Animal Stories Telugu|Animals Stories Telugu|Telugu Animal Stories|Animal Kathalu Telugu|"
                 "సింహం కథ|పులి కథ|ఏనుగు కథ|కోతి కథ|కుందేలు కథ|నక్క కథ|జింక కథ",
                 2.0, 50, "te"),
                ("Wild Animal Stories Telugu|Jungle Stories Telugu|Lion Stories Telugu|Tiger Stories Telugu|"
                 "Elephant Stories Telugu|Monkey Stories Telugu|Rabbit Stories Telugu|Fox Stories Telugu|"
                 "Lion Kathalu Telugu|Tiger Kathalu Telugu|Monkey Kathalu Telugu|Elephant Kathalu Telugu",
                 1.9, 50, "te"),
                ("Deer Stories Telugu|Dog Stories Telugu|Cat Stories Telugu|Bear Stories Telugu|Wolf Stories Telugu",
                 1.8, 30, "te"),
            ],
            "birds": [
                ("Bird Stories Telugu|Birds Stories Telugu|Chilaka Stories|Chilaka Kathalu|Pichuka Stories|"
                 "Pichuka Kathalu|Pavuram Stories|Pavuram Kathalu|Kaki Stories|Kaki Kathalu|"
                 "చిలక కథలు|పిచుక కథలు|కాకి కథలు|పావురం కథలు",
                 2.0, 50, "te"),
                ("Bird Cartoon Stories Telugu|Crow Stories Telugu|Parrot Stories Telugu",
                 1.9, 30, "te"),
            ],
            "rhymes": [
                ("Telugu Nursery Rhymes|Nursery Rhymes Telugu|Telugu Nursery Rhymes for Kids|Telugu Rhymes|"
                 "Kids Rhymes Telugu|Telugu Kids Rhymes",
                 1.9, 50, "te"),
                ("Telugu Kids Songs|Kids Songs Telugu|ABC Songs Telugu|Learning Songs Telugu",
                 1.8, 30, "te"),
            ],
            "bedtime": [
                ("Telugu Bedtime Stories|Kids Bedtime Stories Telugu|Telugu Fairy Tales|Sleep Stories Telugu|"
                 "Night Stories Telugu|Magical Stories Telugu|Bedtime Moral Stories Telugu",
                 1.9, 40, "te"),
            ],
            "cartoon": [
                ("Telugu Cartoon Stories|Kids Cartoon Telugu|Cartoon Stories Telugu|Telugu Kids Cartoon Story",
                 1.9, 40, "te"),
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
            
            # Hindi / Tamil story-words — safety net so moral-category
            # Hindi/Tamil videos with no English words still pass the
            # whitelist (they're only ALLOWED THROUGH later if
            # _determine_group also classifies them as "moral")
            "kahani", "kahaniyan", "kathai", "kathaigal",
            "कहानी", "कहानियां", "नैतिक",
            "கதை", "கதைகள்", "நீதி",
            
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
            # MUSIC/FILM JUNK — needed because YouTube category 10 (Music)
            # is allowed, so real kids' rhymes/songs get in without
            # opening the door to movie songs
            # ============================================================
            "item song", "music video", "audio song", "video song",
            "lyrical video", "full video song", "album", "jukebox",
            "audio jukebox", "dj songs", "remix",

            # ============================================================
            # ROMANCE / LOVE — backstop behind NEGATIVE_QUERY above.
            # Blocked everywhere, INCLUDING moral — the Hindi/Tamil
            # exception below is about LANGUAGE, not about content type.
            # ============================================================
            "love", "love story", "love stories", "romance", "romantic",
            "boyfriend", "girlfriend", "crush", "proposal", "kiss", "kissing",
            "honeymoon", "dating", "relationship", "lust", "affair", "hot", "sexy",
            "ప్రేమ", "ప్రేమ కథ", "లవ్", "రోమాన్స్", "ముద్దు", "సెక్స్", "వ్యభిచారం",

            # ============================================================
            # OTHER STRICT NEGATIVES (block everywhere, any category)
            # ============================================================
            "astrology", "health tips", "beauty tips", "makeup",
            "recipe", "cooking", "travel vlog", "comedy show", "standup",
            "memes", "status", "viral", "challenge", "experiment",
            "wedding", "festival vlog", "horror", "ghost", "devotional",
            "crime", "murder", "dance", "shorts",
        ]
        
        # ============================================================
        # LANGUAGE GATE — Telugu-only EVERYWHERE EXCEPT "moral"
        # ============================================================
        # "moral" (now merged with animation + stories) is explicitly
        # allowed to include Hindi and Tamil content too. Every other
        # category must stay Telugu-only, so these terms are only
        # checked in process_video_item when group != "moral". Terms
        # that specifically describe HINDI MORAL content (e.g. "akbar
        # birbal", "panchatantra hindi") are deliberately NOT here,
        # since that's exactly the content the moral category now wants.
        # ============================================================
        self.NON_MORAL_LANGUAGE_BLOCKERS = [
            "hindi", "हिंदी", "हिन्दी", "hindi me", "hindi mai",
            "hindi rhymes", "hindi cartoon", "hindi animation", "hindi nursery rhymes",
            "hindi kids", "hindi bird", "hindi birds", "hindi animal", "hindi animals",
            "hindi bedtime",
            "tamil", "தமிழ்",
            "tamil rhymes", "tamil cartoon", "tamil animation", "tamil nursery rhymes",
            "tamil kids", "tamil bird", "tamil birds", "tamil animal", "tamil animals",
            "tamil bedtime",
        ]
        
        # Channel blacklist (low-quality sources)
        self.CHANNEL_BLACKLIST = {
            "t series", "zeemusic", "sony music", 
            "tips official", "wave music", "speed records",
        }
        
        # ============================================================
        # ALLOWED CATEGORIES (YouTube's own content categories)
        # ============================================================
        
        self.ALLOWED_CATEGORIES = {
            "1",   # Film & Animation
            "10",  # Music — Nursery Rhymes / Kids Songs channels often file here
            "15",  # Pets & Animals — relevant for the "animals" bucket
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
            for query, _, _, _ in query_list:
                keywords.append(query)
        return keywords
    
    # ============================================================
    # QUOTA MANAGEMENT — dual bucket, resets daily at midnight Pacific
    # ============================================================
    
    async def _maybe_reset_daily_quota(self):
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
    # GROUP DETERMINATION — 6 final buckets. "moral" is the merged
    # moral + animation + stories catch-all (lowest priority = default).
    # ============================================================
    
    def _determine_group(self, title: str, channel: str) -> str:
        """Categorize into exactly one of:
        birds, animals, rhymes, cartoon, bedtime, moral
        """
        combined = f"{title.lower()} {channel.lower()}"
        
        categories = {
            "birds": ([
                r"\bbird\b", r"\bbirds\b",
                r"\bchilaka\b", r"\bpichuka\b", r"\bpavuram\b", r"\bkaki\b",
                "chilaka kathalu", "birds stories", "bird stories",
            ], 90),
            
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
            ], 80),
            
            "cartoon": ([
                r"\bcartoon\b", r"\bcartoons\b",
                "animated cartoon", "telugu cartoon",
            ], 85),
            
            "bedtime": ([
                r"\bbedtime\b", "sleep story", "night story",
        
            ], 60),
            
            # MERGED: moral + animation + stories, now the lowest-priority
            # catch-all (was "stories" before). Also matches Hindi/Tamil
            # story-words since this is the one category that allows them.
            "moral": ([
                r"\bmoral\b","telugu moral stories" r"\bneethi\b", r"\bneeti\b",
                r"\bpanchatantra\b", "atha kodalu", "neethi kathalu",
                r"\banimation\b", "animated story", "animation story",
                r"\bstory\b", r"\bstories\b",
                r"\bkatha\b", r"\bkathalu\b",
                "kids story", "telugu stories",
                r"\bfairy\s*tale", "fairy tales", "fairytale",
                r"\beducational\b", "educational story", "educational stories",
                "kahani", "kahaniyan", "kathai", "kathaigal",
                "birbal", "akbar birbal", "panchatantra hindi",
                "बीरबल", "अकबर", "पंचतंत्र", "नैतिक", "कहानी",
            ], 100),
        }
        
        best_category = "moral"  # default/fallback, since moral absorbed "stories"
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
        language: str = "te",
    ) -> List[str]:
        """Run one quota-efficient OR-combined search for one priority tier
        of a final category, with romance/junk excluded at the API level.
        `language` sets relevanceLanguage — "te" for every category except
        moral's dedicated Hindi ("hi") and Tamil ("ta") tiers.
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
                    relevanceLanguage=language,
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
            
            print(f"✓ [{category}/{language}] priority={priority} → {len(new_ids)}/{len(ids)} new "
                  f"(search calls: {self.search_calls_used}/{self.search_calls_limit} today)")
            
        except HttpError as e:
            print(f"⚠ Error '{category}' ({query}): {e}")
            if "quota" in str(e).lower():
                self.search_calls_used = self.search_calls_limit
        
        return list(set(video_ids))
    
    async def search_all_keywords(self, published_after: str) -> List[str]:
        """Search using priority-tiered OR-combined queries per category.

        16 calls total per run (2 scrapes/day = 32/day) vs. the real
        100-calls/day search bucket ceiling.
        """
        all_video_ids = []
        
        for category, query_list in self.SEARCH_QUERIES.items():
            for tier_idx, (query, priority, max_results, language) in enumerate(query_list, 1):
                video_ids = await self.search_keyword(category, query, published_after, priority, max_results, language)
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
            
            # videos.list costs a flat 1 unit PER CALL, regardless of batch size
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
        
        # Universal negatives — block everywhere, any category, any language
        if any(bad in title_lower for bad in self.MUST_NOT_CONTAIN):
            self.metrics["quality_filtered"] += 1
            return None
        
        raw_dur = content.get("duration", "PT0S")
        duration_text, duration_sec = self._parse_duration(raw_dur)
        
        # Determine topic FIRST (language-independent) so the language
        # gate below can be conditional on the result.
        group = self._determine_group(title, channel)
        
        # LANGUAGE GATE: Telugu-only for every category except "moral"
        if group != "moral":
            if any(bad in title_lower for bad in self.NON_MORAL_LANGUAGE_BLOCKERS):
                self.metrics["quality_filtered"] += 1
                return None

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
        
        published_str = snippet.get("publishedAt", "")
        published_at = datetime.strptime(published_str, "%Y-%m-%dT%H:%M:%SZ")
        published_at_utc = published_at.replace(tzinfo=timezone.utc)
        
        now_utc = datetime.now(timezone.utc)
        hours_ago = int((now_utc - published_at_utc).total_seconds() / 3600)
        
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
            category = video.get("group_category", "moral")
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
        
        self.metrics["start_time"] = datetime.now(IST)
        
        print("🚀 Starting YouTube video fetch...")
        print("=" * 50)
        
        published_after = self.get_published_after()
        
        print("📡 Searching for videos...")
        video_ids = await self.search_all_keywords(published_after)
        
        print(f"\n📊 Found {len(video_ids)} unique videos after deduplication")
        
        print("\n📥 Fetching video details...")
        videos = await self.get_video_details(video_ids)
        
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