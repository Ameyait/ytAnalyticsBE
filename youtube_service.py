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
        self.quota_limit = 10000
        self.quota_lock = asyncio.Lock()
        
        # ============================================================
        # DUPLICATE DETECTION
        # ============================================================
        
        self.video_cache = VideoCache(ttl_minutes=120)
        self.seen_urls: Set[str] = set()
        self.seen_hashes: Set[str] = set()
        
        # ============================================================
        # BATCH PROCESSING
        # ============================================================
        
        self.batch_processor = BatchProcessor(batch_size=50, delay_seconds=0.1)
        
        # ============================================================
        # TARGET: 150-200 VIDEOS PER CATEGORY
        # ============================================================
        self.TARGET_VIDEOS_PER_CATEGORY = 180
        self.MAX_PAGES_PER_SEARCH = 3  # 3 pages per query (3 * 50 = 150 videos per query)
        
        # ============================================================
        # LANGUAGE FILTERING - STRICTLY TELUGU ONLY
        # ============================================================
        
        self.TELUGU_SCRIPT_RANGE = re.compile(r'[\u0C00-\u0C7F]', re.UNICODE)
        
        self.TELUGU_MUST_CONTAIN = [
            "కథ", "కథలు", "నీతి", "నీతి కథ", "పంచతంత్ర",
            "పిల్లల", "పిల్లల కథ", "పక్షి", "పక్షులు",
            "చిలక", "పిచుక", "పావురం", "కాకి",
            "నర్సరీ", "పాటలు", "పాట", "కార్టూన్", "అనిమేషన్",
            "నిద్ర", "నిద్ర కథ", "అమ్మ", "నాన్న",
            "బాలల", "చిన్నారి", "విద్యా", "తెలుగు",
            "అభ్యాస", "నేర్చుకో", "అద్భుత", "అద్భుత కథ",
            "తెలివైన", "కాకి", "పాము", "అత్త", "కోడలు",
        ]
        
        self.NON_TELUGU_BLOCKERS = [
            "hindi", "हिंदी", "हिन्दी", "tamil", "தமிழ்",
            "malayalam", "മലയാളം", "kannada", "ಕನ್ನಡ",
            "marathi", "मराठी", "bengali", "বাংলা",
            "odia", "ଓଡ଼ିଆ", "gujarati", "ગુજરાતી", "punjabi", "ਪੰਜਾਬੀ",
            "hindi story", "tamil story", "malayalam story", "kannada story",
            "hindi rhymes", "tamil rhymes", "malayalam rhymes",
            "dubbed in", "hindi version", "tamil version",
            "english", "english story", "english rhymes",
        ]
        
        # ============================================================
        # OPTIMIZED: MULTIPLE FOCUSED SEARCH QUERIES PER CATEGORY
        # ============================================================
        
        self.SEARCH_QUERIES: Dict[str, List[Tuple[str, float]]] = {
            # MORAL STORIES - 20 queries
            "moral": [
                ("Telugu Moral Stories", 1.5),
                ("Telugu Stories", 1.4),
                ("Telugu Kathalu", 1.4),
                ("Neethi Kathalu Telugu", 1.5),
                ("Neethi Katha Telugu", 1.5),
                ("Moral Story Telugu", 1.4),
                ("Panchatantra Telugu", 1.3),
                ("Telugu Fairy Tales", 1.3),
                ("Kids Telugu Stories", 1.4),
                ("Children Telugu Stories", 1.3),
                ("తెలుగు నీతి కథలు", 1.5),
                ("తెలుగు పంచతంత్ర కథలు", 1.4),
                ("నీతి కథలు తెలుగు", 1.5),
                ("తెలుగు నీతి కథలు పిల్లల కోసం", 1.4),
                ("తెలుగు మంచి కథలు", 1.3),
                ("తెలుగు నీతి కథ", 1.4),
                ("Telugu Moral Stories for Kids", 1.3),
                ("Telugu Bedtime Stories", 1.2),
                ("Telugu Learning Stories", 1.2),
                ("Educational Telugu Stories", 1.2),
            ],
            
            # ATHA KODALU STORIES - 15 queries
            "athakodalu": [
                ("అత్త కోడలు", 1.6),
                ("అత్త కోడలు కథ", 1.6),
                ("అత్త కోడలు కథలు", 1.5),
                ("Atha Kodalu", 1.5),
                ("Atha vs Kodalu", 1.5),
                ("Telugu Atha Kodalu", 1.4),
                ("Telugu Atha Kodalu Stories", 1.4),
                ("కొడలి కథలు", 1.4),
                ("కొడలి మాయా కథ", 1.3),
                ("అత్త కోడలు తెలుగు కథ", 1.5),
                ("Telugu Inlaw Stories", 1.2),
                ("Atha Kodalu Telugu", 1.4),
                ("అత్త vs కోడలు", 1.3),
                ("మాయా కోడలు", 1.3),
                ("తెలివైన కోడలు", 1.3),
            ],
            
            # BIRD STORIES - 15 queries
            "birds": [
                ("Bird Stories Telugu", 1.5),
                ("Telugu Bird Stories", 1.5),
                ("పక్షి కథ", 1.5),
                ("పక్షుల కథలు", 1.4),
                ("చిలక కథ", 1.4),
                ("చిలక కథలు", 1.3),
                ("పిచుక కథ", 1.3),
                ("పావురం కథ", 1.3),
                ("కాకి కథ", 1.4),
                ("కాకి పాము కథ", 1.5),
                ("తెలివైన కాకి", 1.4),
                ("Telugu Crow Stories", 1.3),
                ("Telugu Parrot Stories", 1.3),
                ("Birds Moral Stories Telugu", 1.3),
                ("పక్షుల నీతి కథలు", 1.4),
            ],
            
            # RHYMES - 15 queries
            "rhymes": [
                ("Telugu Nursery Rhymes", 1.5),
                ("Telugu Rhymes", 1.4),
                ("Kids Rhymes Telugu", 1.4),
                ("Nursery Rhymes Telugu", 1.5),
                ("Telugu Kids Songs", 1.4),
                ("Telugu ABC Songs", 1.3),
                ("Telugu Learning Songs", 1.3),
                ("Telugu Baby Songs", 1.3),
                ("తెలుగు నర్సరీ రైమ్స్", 1.5),
                ("తెలుగు పిల్లల పాటలు", 1.4),
                ("తెలుగు రైమ్స్ పిల్లల కోసం", 1.4),
                ("తెలుగు అభ్యాస పాటలు", 1.3),
                ("తెలుగు పాటలు పిల్లలు", 1.3),
                ("Telugu Educational Rhymes", 1.3),
                ("తెలుగు కిడ్స్ సాంగ్స్", 1.3),
            ],
            
            # ANIMATION - 15 queries
            "animation": [
                ("Telugu Animation Stories", 1.5),
                ("Telugu Cartoon Stories", 1.4),
                ("Animated Telugu Story", 1.4),
                ("Animated Moral Story Telugu", 1.4),
                ("Telugu Kids Animation", 1.3),
                ("Telugu Fairy Tales", 1.3),
                ("తెలుగు యానిమేషన్ కథలు", 1.5),
                ("యానిమేటెడ్ తెలుగు నీతి కథ", 1.4),
                ("యానిమేషన్ పంచతంత్ర తెలుగు", 1.3),
                ("తెలుగు యానిమేషన్ స్టోరీస్", 1.3),
                ("తెలుగు కార్టూన్ కథలు", 1.4),
                ("Animated Stories for Kids Telugu", 1.3),
                ("3D Animation Stories Telugu", 1.2),
                ("Telugu Animated Stories", 1.4),
                ("Cartoon Stories Telugu", 1.3),
            ],
            
            # BEDTIME / FAIRY TALES - 15 queries
            "bedtime": [
                ("Telugu Bedtime Stories", 1.5),
                ("Telugu Fairy Tales", 1.5),
                ("తెలుగు నిద్ర కథలు", 1.5),
                ("తెలుగు అద్భుత కథలు", 1.4),
                ("తెలుగు నైట్ స్టోరీస్", 1.3),
                ("తెలుగు స్లీప్ కథలు", 1.3),
                ("తెలుగు ఫెయిరీ టేల్స్", 1.4),
                ("Telugu Night Stories", 1.3),
                ("Telugu Sleep Stories", 1.3),
                ("అద్భుత కథలు తెలుగు", 1.4),
                ("Telugu Magical Stories", 1.3),
                ("Fairy Tales in Telugu", 1.4),
                ("Telugu Bedtime Stories for Kids", 1.3),
                ("తెలుగు పిల్లల నిద్ర కథలు", 1.3),
                ("Telugu Lullaby Stories", 1.2),
            ],
            
            # GENERAL STORIES - 20 queries
            "stories": [
                ("Telugu Stories", 1.5),
                ("Telugu Kathalu", 1.4),
                ("తెలుగు కథలు", 1.5),
                ("తెలుగు బాలల కథలు", 1.4),
                ("Telugu Kids Stories", 1.4),
                ("Kids Story Telugu", 1.3),
                ("Telugu Moral Stories", 1.4),
                ("Telugu Fairy Tales", 1.3),
                ("Telugu Bedtime Stories", 1.3),
                ("Educational Telugu Stories", 1.3),
                ("Telugu Children Stories", 1.3),
                ("తెలుగు పిల్లల కథలు", 1.4),
                ("Telugu Animated Stories", 1.3),
                ("Telugu Story for Kids", 1.3),
                ("తెలుగు కథలు పిల్లల కోసం", 1.4),
                ("Telugu Moral Stories for Children", 1.3),
                ("Telugu Short Stories", 1.2),
                ("తెలుగు విద్యా కథలు", 1.3),
                ("Telugu Learning Stories", 1.2),
                ("Telugu Panchatantra Stories", 1.3),
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
            "bedtime", "fairy", "fairytale", "tales",
            "educational",
            "కథ", "కథలు", "నీతి", "పిల్లల", "పక్షి",
            "తెలివైన", "పాము", "అత్త", "కోడలు", "అద్భుత",
        ]
        
        self.MUST_NOT_CONTAIN = [
    "movie", "film", "cinema",
    "trailer", "teaser",
    "serial", "episode",

    "love", "romance", "romantic",
    "couple", "dating",
    "boyfriend", "girlfriend",

    "adult", "18+", "sex", "sexual", "sexy",
    "hot", "affair",

    "ప్రేమ", "రోమాన్స్",
    "సెక్స్", "లవ్",

    "murder", "kill", "crime",
    "gangster", "ghost",

    "gaming", "gameplay",
    "gta", "minecraft",
    "free fire", "pubg",

    "news", "breaking",
    "election",

    "cricket", "ipl",

    "review", "reaction",
    "interview", "prank",
    "vlog", "podcast",
    "live",
]
        self.CHANNEL_BLACKLIST = {
            "t series", "zeemusic", "sony music", 
            "tips official", "wave music", "speed records",
        }
        
        # ============================================================
        # ALLOWED CATEGORIES
        # ============================================================
        
        self.ALLOWED_CATEGORIES = {
            "1", "15", "22", "23", "24", "27"
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
            "non_telugu_filtered": 0,
            "duration_filtered": 0,
            "category_filtered": 0,
            "keyword_filtered": 0,
            "api_calls": 0,
            "pages_fetched": 0,
            "start_time": None,
            "rejection_reasons": defaultdict(int),
        }
        
        # ============================================================
        # DISCOVERED TELUGU CHANNELS
        # ============================================================
        self.telugu_channels: Set[str] = set()
        self.channel_cache: Dict[str, Dict] = {}
    
    # ============================================================
    # PROPERTIES
    # ============================================================
    
    @property
    def ALL_KEYWORDS(self) -> List[str]:
        """Backward compatibility - flatten all search queries"""
        all_queries = []
        for queries in self.SEARCH_QUERIES.values():
            for query, _ in queries:
                all_queries.append(query)
        return all_queries
    
    # ============================================================
    # QUOTA MANAGEMENT
    # ============================================================
    
    async def check_quota(self, required_units: int = 100) -> bool:
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
    # LANGUAGE DETECTION
    # ============================================================
    
    def _is_telugu_only(self, text: str) -> bool:
        text = text.lower().strip()
        
        if re.search(self.TELUGU_SCRIPT_RANGE, text):
            return True
        
        telugu_translit_patterns = [
            r'\btelugu\b', r'\btelegu\b',
            r'\btelugu story\b', r'\btelugu stories\b',
            r'\btelugu rhymes\b', r'\btelugu songs\b',
            r'\btelugu cartoon\b', r'\btelugu animation\b',
        ]
        
        for pattern in telugu_translit_patterns:
            if re.search(pattern, text):
                return True
        
        return False
    
    def _has_telugu_keywords(self, text: str) -> bool:
        text = text.lower()
        for keyword in self.TELUGU_MUST_CONTAIN:
            if keyword.lower() in text:
                return True
        return False
    
    def _is_non_telugu_language(self, title: str, channel: str, description: str = "") -> bool:
        combined_text = f"{title} {channel} {description}".lower()
        
        if self._is_telugu_only(combined_text) or self._has_telugu_keywords(combined_text):
            return False
        
        for blocker in self.NON_TELUGU_BLOCKERS:
            if blocker.lower() in combined_text:
                return True
        
        non_telugu_indicators = [
            r'\bhindi\b', r'\btamil\b', r'\bmalayalam\b', 
            r'\bkannada\b', r'\bmarathi\b', r'\bbengali\b',
            r'\bodiya\b', r'\bgujarati\b', r'\bpunjabi\b',
            r'\btelugu version\b', r'\bhindi version\b',
            r'\btamil version\b', r'\bmalayalam version\b',
            r'\bkannada version\b', r'\bdubbed in\b',
        ]
        
        for pattern in non_telugu_indicators:
            if re.search(pattern, combined_text):
                return True
        
        return False
    
    # ============================================================
    # DURATION PARSER
    # ============================================================
    
    def _parse_duration(self, duration_str: str) -> Tuple[str, int]:
        if not duration_str or duration_str == "PT0S":
            return "0s", 0
        
        match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
        
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
    # GROUP DETERMINATION - EXPANDED
    # ============================================================
    
    def _determine_group(self, title: str, channel: str) -> str:
        combined = f"{title.lower()} {channel.lower()}"
        
        categories = {
            "birds": ([
                r"\bbird\b", r"\bbirds\b",
                r"\bchilaka\b", r"\bpichuka\b", r"\bpavuram\b", r"\bkaki\b",
                "chilaka kathalu", "birds stories", "bird stories",
                "పక్షి", "పక్షులు", "చిలక", "పిచుక", "పావురం", "కాకి",
                "కాకి పాము", "crow snake", "తెలివైన కాకి",
            ], 100),
            
            "rhymes": ([
                r"\brhyme\b", r"\brhymes\b", r"\bnursery\b",
                r"\bsong\b", r"\bsongs\b", r"\blullaby\b",
                "nursery rhymes", "kids rhymes", "learning song", "learning songs",
                "abc rhymes", "alphabet song",
                "నర్సరీ", "పాట", "పాటలు",
            ], 90),
            
            "cartoon": ([
                r"\bcartoon\b", r"\bcartoons\b",
                "animated cartoon", "telugu cartoon",
                "కార్టూన్",
            ], 80),
            
            "animation": ([
                r"\banimation\b",
                "animated story", "animation story",
                "యానిమేషన్",
            ], 70),
            
            "bedtime": ([
                r"\bbedtime\b", "sleep story", "night story",
                r"\bfairy\s*tale", "fairy tales", "fairytale",
                "నిద్ర", "అద్భుత",
            ], 60),
            
            "moral": ([
                r"\bmoral\b", r"\bneethi\b", r"\bneeti\b",
                r"\bpanchatantra\b", "atha kodalu", "neethi kathalu",
                "నీతి", "పంచతంత్ర", "అత్త", "కోడలు",
            ], 50),
            
            "stories": ([
                r"\bstory\b", r"\bstories\b",
                r"\bkatha\b", r"\bkathalu\b",
                "kids story", "telugu stories",
                r"\beducational\b", "educational story", "educational stories",
                "కథ", "కథలు", "బాలల", "చిన్నారి", "విద్యా",
                "అద్భుతమైన",
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
    # SEARCH WITH MULTIPLE QUERIES PER CATEGORY
    # ============================================================
    
    async def search_single_query(
        self,
        category: str,
        query: str,
        published_after: str,
        priority: float = 1.0,
    ) -> List[str]:
        """Execute a single search query with pagination"""
        
        if not await self.check_quota(100):
            return []
        
        youtube = self._get_client()
        video_ids = []
        page_token = None
        pages_fetched = 0
        
        while pages_fetched < self.MAX_PAGES_PER_SEARCH:
            try:
                loop = asyncio.get_event_loop()
                
                response = await loop.run_in_executor(
                    None,
                    lambda: youtube.search().list(
                        part="snippet",
                        q=query,
                        type="video",
                        regionCode="IN",
                        maxResults=50,
                        pageToken=page_token,
                        order="date",
                        relevanceLanguage="te",
                        safeSearch="strict",
                        publishedAfter=published_after,
                        videoDuration="medium",
                    ).execute()
                )
                
                pages_fetched += 1
                self.metrics["pages_fetched"] += 1
                self.metrics["api_calls"] += 1
                
                items = response.get("items", [])
                
                if not items:
                    break
                
                # Extract video IDs
                ids = [item["id"]["videoId"] for item in items]
                
                # Track channels
                for item in items:
                    channel_id = item["snippet"].get("channelId")
                    if channel_id:
                        self.telugu_channels.add(channel_id)
                
                # Deduplicate
                new_ids = []
                for vid in ids:
                    if vid not in self.seen_urls:
                        new_ids.append(vid)
                        self.seen_urls.add(vid)
                
                video_ids.extend(new_ids)
                
                # Get next page token
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
                
                await asyncio.sleep(0.2)
                
            except HttpError as e:
                print(f"    ⚠️ Search error for '{query}': {e}")
                if "quota" in str(e).lower():
                    self.quota_used = self.quota_limit
                break
            except Exception as e:
                print(f"    ⚠️ Unexpected error: {e}")
                break
        
        return list(set(video_ids))
    
    async def search_all_queries(self, published_after: str) -> List[str]:
        """Execute all search queries across all categories"""
        
        all_video_ids = []
        total_queries = sum(len(queries) for queries in self.SEARCH_QUERIES.values())
        query_count = 0
        
        print(f"📊 Total queries to execute: {total_queries}")
        print("-" * 40)
        
        for category, queries in self.SEARCH_QUERIES.items():
            print(f"\n📂 Category: {category} ({len(queries)} queries)")
            
            category_videos = []
            for query, priority in queries:
                query_count += 1
                print(f"  [{query_count}/{total_queries}] 🔍 '{query[:40]}...'")
                
                video_ids = await self.search_single_query(
                    category, query, published_after, priority
                )
                category_videos.extend(video_ids)
                
                print(f"    → Found {len(video_ids)} new videos")
                
                # Rate limiting between queries
                await asyncio.sleep(0.3)
            
            all_video_ids.extend(category_videos)
            print(f"  ✅ Category total: {len(category_videos)} videos")
            
            # Delay between categories
            await asyncio.sleep(0.5)
        
        return list(set(all_video_ids))
    
    # ============================================================
    # CHANNEL-BASED FETCHING
    # ============================================================
    
    async def fetch_channel_uploads(
        self,
        channel_id: str,
        published_after: str,
        max_videos: int = 50
    ) -> List[str]:
        """Fetch videos from a channel's uploads playlist"""
        
        if not await self.check_quota(1):
            return []
        
        youtube = self._get_client()
        video_ids = []
        
        try:
            loop = asyncio.get_event_loop()
            
            channel_response = await loop.run_in_executor(
                None,
                lambda: youtube.channels().list(
                    part="contentDetails",
                    id=channel_id
                ).execute()
            )
            
            if not channel_response.get("items"):
                return []
            
            uploads_playlist_id = channel_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            
            page_token = None
            pages_fetched = 0
            
            while pages_fetched < 5:
                playlist_response = await loop.run_in_executor(
                    None,
                    lambda: youtube.playlistItems().list(
                        part="snippet",
                        playlistId=uploads_playlist_id,
                        maxResults=50,
                        pageToken=page_token
                    ).execute()
                )
                
                pages_fetched += 1
                self.metrics["api_calls"] += 1
                
                for item in playlist_response.get("items", []):
                    published_at_str = item["snippet"]["publishedAt"]
                    if published_at_str >= published_after:
                        video_id = item["snippet"]["resourceId"]["videoId"]
                        if video_id not in self.seen_urls:
                            video_ids.append(video_id)
                            self.seen_urls.add(video_id)
                
                page_token = playlist_response.get("nextPageToken")
                if not page_token or len(video_ids) >= max_videos:
                    break
                
                await asyncio.sleep(0.2)
            
        except HttpError as e:
            print(f"    ⚠️ Channel fetch error: {e}")
        except Exception as e:
            print(f"    ⚠️ Unexpected channel error: {e}")
        
        return video_ids
    
    async def fetch_from_top_channels(
        self,
        published_after: str,
        max_channels: int = 30
    ) -> List[str]:
        """Fetch videos from top discovered Telugu channels"""
        
        if not self.telugu_channels:
            print("  ⚠️ No channels discovered yet")
            return []
        
        print(f"\n📺 Fetching from {min(len(self.telugu_channels), max_channels)} channels...")
        
        all_video_ids = []
        channels_list = list(self.telugu_channels)[:max_channels]
        
        for idx, channel_id in enumerate(channels_list, 1):
            print(f"  🎬 Channel {idx}/{len(channels_list)}")
            video_ids = await self.fetch_channel_uploads(
                channel_id, published_after, max_videos=100
            )
            all_video_ids.extend(video_ids)
            print(f"    → Found {len(video_ids)} recent videos")
            
            await asyncio.sleep(0.3)
        
        return list(set(all_video_ids))
    
    # ============================================================
    # VIDEO DETAILS METHODS
    # ============================================================
    
    async def fetch_batch_details(self, batch_ids: List[str]) -> List[Dict[str, Any]]:
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
            self.metrics["api_calls"] += 1
            
            for item in items:
                try:
                    video = await self.process_video_item(item)
                    if video and isinstance(video, dict):
                        videos_data.append(video)
                except Exception as e:
                    continue
            
            await self.check_quota(len(batch_ids))
            
        except HttpError as e:
            print(f"  ✗ Batch Details Error: {e}")
        except Exception as e:
            print(f"  ✗ Unexpected error: {e}")
        
        return videos_data
    
    async def process_video_item(self, item: Dict) -> Optional[Dict[str, Any]]:
        snippet = item["snippet"]
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})
        
        video_id = item["id"]
        title = snippet.get("title", "")
        channel = snippet.get("channelTitle", "")
        channel_id = snippet.get("channelId", "")
        cat_id = snippet.get("categoryId", "")
        description = snippet.get("description", "")
        
        # Check 1: Category
        if cat_id not in self.ALLOWED_CATEGORIES:
            reason = f"category {cat_id} not allowed"
            self.metrics["rejection_reasons"][reason] += 1
            self.metrics["category_filtered"] += 1
            return None
        
        title_lower = title.lower()
        channel_lower = channel.lower()
        
        # Check 2: Language - Non-Telugu
        if self._is_non_telugu_language(title, channel, description):
            reason = "non-Telugu language detected"
            self.metrics["rejection_reasons"][reason] += 1
            self.metrics["non_telugu_filtered"] += 1
            return None
        
        # Check 3: Telugu script/keywords
        combined_text = f"{title} {description}".lower()
        if not self._is_telugu_only(combined_text) and not self._has_telugu_keywords(combined_text):
            if "telugu" not in title_lower and "telugu" not in channel_lower:
                reason = "no Telugu script or keywords found"
                self.metrics["rejection_reasons"][reason] += 1
                self.metrics["non_telugu_filtered"] += 1
                return None
        
        # Check 4: Channel blacklist
        if any(bad in channel_lower for bad in self.CHANNEL_BLACKLIST):
            reason = "channel blacklisted"
            self.metrics["rejection_reasons"][reason] += 1
            self.metrics["quality_filtered"] += 1
            return None
        
        # Check 5: Must not contain
        if any(bad in title_lower for bad in self.MUST_NOT_CONTAIN):
            reason = "contains blocked word"
            self.metrics["rejection_reasons"][reason] += 1
            self.metrics["quality_filtered"] += 1
            return None
        
        # Check 6: Duration
        raw_dur = content.get("duration", "PT0S")
        duration_text, duration_sec = self._parse_duration(raw_dur)
        
        group = self._determine_group(title, channel)
        
        # Duration limits
        if group == "rhymes":
            if duration_sec < 120 or duration_sec > 1200:
                reason = f"rhymes duration {duration_sec}s"
                self.metrics["rejection_reasons"][reason] += 1
                self.metrics["duration_filtered"] += 1
                return None
        elif group == "birds":
            if duration_sec < 60 or duration_sec > 900:
                reason = f"birds duration {duration_sec}s"
                self.metrics["rejection_reasons"][reason] += 1
                self.metrics["duration_filtered"] += 1
                return None
        else:
            if duration_sec < 180 or duration_sec > 1800:
                reason = f"duration {duration_sec}s"
                self.metrics["rejection_reasons"][reason] += 1
                self.metrics["duration_filtered"] += 1
                return None
        
        # Check 7: Must contain keywords
        combined = f"{title_lower} {channel_lower}"
        if not any(good in combined for good in self.MUST_CONTAIN_ANY):
            reason = "missing required keywords"
            self.metrics["rejection_reasons"][reason] += 1
            self.metrics["keyword_filtered"] += 1
            return None
        
        # Check 8: Duplicate detection
        if self.video_cache.is_duplicate(title, channel, duration_sec):
            reason = "duplicate video"
            self.metrics["rejection_reasons"][reason] += 1
            self.metrics["duplicates_filtered"] += 1
            return None
        
        content_hash = hashlib.md5(
            f"{title_lower}|{channel_lower}|{duration_sec}".encode()
        ).hexdigest()
        
        if content_hash in self.seen_hashes:
            reason = "duplicate hash"
            self.metrics["rejection_reasons"][reason] += 1
            self.metrics["duplicates_filtered"] += 1
            return None
        
        # Timezone handling
        published_str = snippet.get("publishedAt", "")
        published_at = datetime.strptime(published_str, "%Y-%m-%dT%H:%M:%SZ")
        published_at_utc = published_at.replace(tzinfo=timezone.utc)
        
        now_utc = datetime.now(timezone.utc)
        hours_ago = int((now_utc - published_at_utc).total_seconds() / 3600)
        
        published_at_naive = published_at_utc.replace(tzinfo=None)
        
        video_data = {
            "video_id": video_id,
            "title": title,
            "channel": channel,
            "channel_id": channel_id,
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
            "duration": duration_text,
            "duration_seconds": duration_sec,
            "category": self.CATEGORY_MAP.get(cat_id, "Unknown"),
            "category_id": cat_id,
            "group_category": group,
            "published_at": published_at_naive,
            "hours_ago": hours_ago,
            "url": f"https://youtube.com/watch?v={video_id}",
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "content_hash": content_hash,
            "is_telugu": True,
        }
        
        self.video_cache.add_video(title, channel, duration_sec, video_id)
        self.seen_hashes.add(content_hash)
        
        return video_data
    
    async def get_video_details(self, video_ids: List[str]) -> List[Dict[str, Any]]:
        if not video_ids:
            return []
        
        print(f"\n📥 Fetching details for {len(video_ids)} videos...")
        
        import random
        random.shuffle(video_ids)
        
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
    # SORT AND FILTER BY DATE
    # ============================================================
    
    def filter_by_date_range(self, videos: List[Dict], days: int) -> List[Dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_naive = cutoff.replace(tzinfo=None)
        
        filtered = []
        for video in videos:
            published = video.get("published_at")
            if published and isinstance(published, datetime):
                if published >= cutoff_naive:
                    filtered.append(video)
        
        return filtered
    
    # ============================================================
    # DATABASE PREPARATION
    # ============================================================
    
    def prepare_for_database(self, videos: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not videos:
            return {"videos": [], "summary": {}}
        
        grouped = defaultdict(list)
        for video in videos:
            category = video.get("group_category", "stories")
            grouped[category].append(video)
        
        for category in grouped:
            grouped[category].sort(key=lambda x: x.get("views", 0), reverse=True)
        
        final_grouped = {}
        for category, videos_list in grouped.items():
            final_grouped[category] = videos_list[:self.TARGET_VIDEOS_PER_CATEGORY]
        
        final_videos = []
        for videos_list in final_grouped.values():
            final_videos.extend(videos_list)
        
        summary = {
            "total_videos": len(final_videos),
            "categories": {
                cat: len(videos_list) for cat, videos_list in final_grouped.items()
            },
            "total_views": sum(v.get("views", 0) for v in final_videos),
            "avg_duration": sum(v.get("duration_seconds", 0) for v in final_videos) / len(final_videos) if final_videos else 0,
            "date_range": {
                "oldest": min(v.get("published_at") for v in final_videos) if final_videos else None,
                "newest": max(v.get("published_at") for v in final_videos) if final_videos else None,
            },
            "top_channels": self._get_top_channels(final_videos, 10),
        }
        
        return {
            "videos": final_videos,
            "grouped_videos": dict(final_grouped),
            "summary": summary,
            "metadata": {
                "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
                "total_quota_used": self.quota_used,
                "metrics": self.metrics,
                "language": "Telugu Only",
                "channels_discovered": len(self.telugu_channels),
                "target_per_category": self.TARGET_VIDEOS_PER_CATEGORY,
                "total_queries": sum(len(queries) for queries in self.SEARCH_QUERIES.values()),
            }
        }
    
    def _get_top_channels(self, videos: List[Dict], limit: int = 10) -> List[Dict]:
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
        """Main method with multiple focused search queries"""
        
        self.metrics["start_time"] = datetime.now(IST)
        
        print("🚀 Starting YouTube video fetch...")
        print("🎯 Language: TELUGU ONLY")
        print(f"🎯 Target: {self.TARGET_VIDEOS_PER_CATEGORY} videos per category")
        print("=" * 60)
        
        days_back = config.DAYS_BACK
        published_after = self.get_published_after(days_back)
        print(f"📅 Fetching videos from last {days_back} days")
        print(f"📅 Published after: {published_after}")
        print("=" * 60)
        
        # STEP 1: SEARCH WITH MULTIPLE QUERIES
        print("\n🔍 STEP 1: Searching with multiple focused queries...")
        print("-" * 40)
        
        search_video_ids = await self.search_all_queries(published_after)
        print(f"\n📊 Search found {len(search_video_ids)} unique videos")
        
        # STEP 2: FETCH FROM DISCOVERED CHANNELS
        print("\n📺 STEP 2: Fetching from discovered channels...")
        print("-" * 40)
        
        channel_video_ids = await self.fetch_from_top_channels(published_after, max_channels=30)
        print(f"\n📊 Channel fetch found {len(channel_video_ids)} additional videos")
        
        # STEP 3: COMBINE AND DEDUPLICATE
        print("\n🔄 STEP 3: Combining and deduplicating...")
        print("-" * 40)
        
        all_video_ids = list(set(search_video_ids + channel_video_ids))
        print(f"📊 Total unique video IDs: {len(all_video_ids)}")
        
        # STEP 4: FETCH DETAILS
        print("\n📥 STEP 4: Fetching video details...")
        print("-" * 40)
        
        videos = await self.get_video_details(all_video_ids)
        
        # STEP 5: FILTER BY DATE RANGE
        print(f"\n📅 STEP 5: Filtering videos from last {days_back} days...")
        print("-" * 40)
        
        videos = self.filter_by_date_range(videos, days_back)
        print(f"📊 Videos after date filter: {len(videos)}")
        
        # STEP 6: SORT BY VIEWS
        print("\n📊 STEP 6: Sorting by views...")
        print("-" * 40)
        
        videos.sort(key=lambda x: x.get("views", 0), reverse=True)
        
        # STEP 7: PREPARE FOR DATABASE
        print("\n💾 STEP 7: Preparing data for database...")
        print("-" * 40)
        
        db_ready_data = self.prepare_for_database(videos)
        
        # FINAL SUMMARY
        elapsed = (
            datetime.now(IST) - self.metrics["start_time"]
        ).total_seconds()
        self.metrics["total_time_seconds"] = elapsed
        
        print("\n" + "=" * 60)
        print("✅ FETCH COMPLETE")
        print("=" * 60)
        print(f"📊 Total videos fetched: {len(db_ready_data['videos'])}")
        print(f"🎯 Categories: {db_ready_data['summary']['categories']}")
        print(f"⏱️  Time taken: {elapsed:.2f} seconds")
        print(f"💾 Quota used: {self.quota_used}/{self.quota_limit}")
        print(f"📄 Pages fetched: {self.metrics['pages_fetched']}")
        print(f"🔄 Duplicates filtered: {self.metrics['duplicates_filtered']}")
        print(f"🎨 Quality filtered: {self.metrics['quality_filtered']}")
        print(f"🗣️  Non-Telugu filtered: {self.metrics['non_telugu_filtered']}")
        print(f"⏱️  Duration filtered: {self.metrics['duration_filtered']}")
        print(f"📂 Category filtered: {self.metrics['category_filtered']}")
        print(f"📺 Channels discovered: {len(self.telugu_channels)}")
        print(f"📊 Total queries executed: {sum(len(queries) for queries in self.SEARCH_QUERIES.values())}")
        print("=" * 60)
        
        return db_ready_data
    
    # ============================================================
    # DATE FILTER
    # ============================================================
    
    def get_published_after(self, days: int = None) -> str:
        if days is None:
            days = config.DAYS_BACK
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")