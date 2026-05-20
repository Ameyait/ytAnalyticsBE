import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple
import asyncio
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import config


class YouTubeService:
    def __init__(self):
        self.api_key = config.YOUTUBE_API_KEY
        self.youtube = None
        self.quota_used = 0
        self.quota_limit = 10000
        
        # =============================================================
        # FULL KEYWORD GROUPS - MATCHING STANDALONE SCRIPT
        # =============================================================
        
        self.GROUP_RHYMES = [
            "Telugu rhymes",
            "Kids rhymes Telugu",
            "Telugu Nursery Rhymes",
            "Telugu Nursery Rhymes For Kids",
            "Kids songs Telugu",
            "Telugu kids rhymes",
            "Nursery rhymes Telugu",
        ]
        
        self.GROUP_STORIES = [
            "kids story telugu",
            "Kids storys Telugu",
            "Telugu kids storys",
            "new telugu storys",
            "new telugu kathalu",
            "storys in telugu",
            "stories in telugu",
            "children storys Telugu",
            "telugu children storys",
        ]
        
        self.GROUP_CARTOON = [
            "Telugu cartoon",
            "telugu cartoons",
        ]
        
        self.GROUP_BIRDS = [
            "Birds Stories Telugu",
            "Birds stories telugu",
            "Chilaka stories Telugu",
            "Pichuka stories Telugu",
            "Pavuram stories Telugu",
            "Kaki stories Telugu",
            "Chilaka Kathalu",
            "Pichuka Kathalu",
            "Pavuram Kathalu",
            "Kaki Kathalu",
        ]
        
        self.GROUP_BEDTIME = [
            "Bedtime stories Telugu",
            "Bedtime stories kids Telugu",
        ]
        
        self.GROUP_MORAL = [
            "Neethi Kathalu Telugu",
            "Elephant stories Telugu",
        ]
        
        self.ALL_KEYWORD_GROUPS = {
            "rhymes": self.GROUP_RHYMES,
            "stories": self.GROUP_STORIES,
            "cartoon": self.GROUP_CARTOON,
            "birds": self.GROUP_BIRDS,
            "bedtime": self.GROUP_BEDTIME,
            "moral": self.GROUP_MORAL,
        }
        
        # =============================================================
        # FULL CONTENT FILTERS - MATCHING STANDALONE SCRIPT
        # =============================================================
        
        self.MUST_CONTAIN_ANY = [
            # Telugu story words
            "story","stories","kathalu","kathali","katha","katalu",
            # Rhymes / songs for kids
            "rhymes","rhyme","nursery","lullaby","balaganapam",
            # Animation / cartoon
            "cartoon","cartoons","animation","animated","toons",
            # Moral / bedtime
            "moral","neethi","neeti","bedtime","fairy","tales","tale",
            # Bird characters
            "chilaka","pichuka","pavuram","kaki","tuni","bujji",
            # Animals
            "elephant","enaugu","పిచుక","చిలక","పావురం","కాకి","ఏనుగు",
            # Kids / children
            "kids","children","child","baby","balalu","బాలలు",
            # Telugu script story words
            "కథలు","కథ","నీతి","కార్టూన్",
            # Panchatantra / classic
            "panchatantra","hitopadesha","atha","kodalu",
            # Song for kids
            "song","songs","గేయాలు",
        ]
        
        self.MUST_NOT_CONTAIN = [
            # Trailers / movies
            "trailer","teaser","movie","film","cinema","theatre",
            "review","reaction","interview","press meet",
            # Music (adult)
            "remix","dj","bhajan","rap","gaana","album","audio",
            "lyrical video","lyric video","full video song",
            # Gaming
            "gaming","gameplay","gta","minecraft","freefire","free fire",
            "bgmi","pubg","roblox","fortnite","among us",
            # News / politics
            "news","breaking","live news","election","vote","government",
            "parliament","budget","politics","update","latest news",
            # Sports
            "cricket","ipl","match","highlights","football","kabaddi",
            # Adult / regional films
            "bollywood","bigg boss","web series","serial","natak",
            "bhojpuri","punjabi","haryanvi","marathi film","kannada movie",
            # Tech / other
            "unboxing","vlog","prank","challenge","hack","tutorial",
            # Adult content signals
            "adult","18+","hot","sexy","romance","couple","husband","wife",
        ]
        
        self.ALLOWED_CATEGORIES = {"1", "22", "24", "27", "15"}
        
        self.CATEGORY_MAP = {
            "1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music",
            "15": "Pets & Animals", "17": "Sports", "19": "Travel & Events",
            "20": "Gaming", "22": "People & Blogs", "23": "Comedy",
            "24": "Entertainment", "25": "News & Politics", "26": "How-to & Style",
            "27": "Education", "28": "Science & Technology", "29": "Nonprofits",
        }
    
    def _get_client(self):
        if not self.youtube:
            self.youtube = build("youtube", "v3", developerKey=self.api_key)
        return self.youtube
    
    def _parse_duration(self, duration_str: str) -> Tuple[str, int]:
        match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
        if not match:
            return "?", 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        total = hours * 3600 + minutes * 60 + seconds
        if hours:
            return f"{hours}h {minutes}m {seconds}s", total
        elif minutes:
            return f"{minutes}m {seconds}s", total
        return f"{seconds}s", total
    
    def _determine_group(self, title: str, channel: str) -> str:
        combined = f"{title.lower()} {channel.lower()}"
        
        # Check each group (matching standalone script logic)
        if any(kw in combined for kw in ["rhyme", "nursery", "lullaby", "balaganapam", "song", "songs", "గేయాలు"]):
            return "rhymes"
        
        if any(kw in combined for kw in ["bedtime", "fairy", "tale", "night"]):
            return "bedtime"
        
        if any(kw in combined for kw in ["moral", "neethi", "neeti", "panchatantra", "hitopadesha"]):
            return "moral"
        
        if any(kw in combined for kw in ["bird", "chilaka", "pichuka", "pavuram", "kaki", "animal", "elephant", "lion", "monkey", "rabbit", "turtle", "fox", "deer", "tiger", "bear", "horse", "panchatantra", "జంతువుల"]):
            return "birds"
        
        if any(kw in combined for kw in ["cartoon", "animation", "animated", "toons", "anime", "2d", "3d"]):
            return "cartoon"
        
        return "stories"
    
    async def search_keyword(self, keyword: str, published_after: str) -> List[str]:
        """Search BOTH medium AND long videos - matching standalone script"""
        youtube = self._get_client()
        video_ids = []
        
        # Search both "medium" AND "long" (like standalone script)
        for duration in ["medium", "long"]:
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda d=duration: youtube.search().list(
                        part="snippet",
                        q=keyword,
                        type="video",
                        regionCode=config.REGION_CODE,
                        maxResults=config.MAX_PER_KEYWORD,
                        order="viewCount",
                        relevanceLanguage="te",
                        safeSearch="strict",
                        publishedAfter=published_after,
                        videoDuration=d,
                    ).execute()
                )
                ids = [item["id"]["videoId"] for item in response.get("items", [])]
                video_ids.extend(ids)
                self.quota_used += 100
            except HttpError as e:
                print(f"    ⚠️  Error '{keyword}' [{duration}]: {e}")
        
        return list(set(video_ids))
    
    async def get_video_details(self, video_ids: List[str]) -> List[Dict[str, Any]]:
        if not video_ids:
            return []
        
        youtube = self._get_client()
        videos_data = []
        
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
                    stats = item.get("statistics", {})
                    content = item.get("contentDetails", {})
                    
                    title = snippet.get("title", "")
                    channel = snippet.get("channelTitle", "")
                    cat_id = snippet.get("categoryId", "")
                    
                    # ── STRICT FILTER (matching standalone) ──
                    if cat_id not in self.ALLOWED_CATEGORIES:
                        continue
                    
                    title_lower = title.lower()
                    if any(bad in title_lower for bad in self.MUST_NOT_CONTAIN):
                        continue
                    
                    combined = f"{title_lower} {channel.lower()}"
                    if not any(good in combined for good in self.MUST_CONTAIN_ANY):
                        continue
                    
                    # ── DURATION CHECK (matching standalone) ──
                    raw_dur = content.get("duration", "PT0S")
                    _, duration_sec = self._parse_duration(raw_dur)
                    if duration_sec < config.MIN_DURATION_SECONDS:  # Use < not <=
                        continue
                    
                    published_str = snippet.get("publishedAt", "")
                    published_at = datetime.strptime(published_str, "%Y-%m-%dT%H:%M:%SZ")
                    hours_ago = int((datetime.utcnow() - published_at).total_seconds() / 3600)
                    
                    group = self._determine_group(title, channel)
                    
                    video_data = {
                        "video_id": item["id"],
                        "title": title,
                        "channel": channel,
                        "views": int(stats.get("viewCount", 0)),
                        "likes": int(stats.get("likeCount", 0)),
                        "comments": int(stats.get("commentCount", 0)),
                        "category": self.CATEGORY_MAP.get(cat_id, "Other"),
                        "duration": self._parse_duration(raw_dur)[0],
                        "duration_seconds": duration_sec,
                        "published_at": published_at,
                        "hours_ago": hours_ago,
                        "url": f"https://www.youtube.com/watch?v={item['id']}",
                        "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                        "group_category": group,
                        "matched_keywords": [],
                        "matched_terms": "",
                        "keyword_count": 0,
                        "search_rank": 0,
                    }
                    videos_data.append(video_data)
                    
                self.quota_used += len(batch_ids)
                print(f"    ✓ Processed batch {i//50 + 1}, found {len(videos_data)} videos")
                
            except HttpError as e:
                print(f"    ✗ Error fetching details: {e}")
            
            await asyncio.sleep(0.3)
        
        return videos_data
    
    def get_published_after(self) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(days=config.DAYS_BACK)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")