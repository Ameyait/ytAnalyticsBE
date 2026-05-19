# =============================================================
# config.py
# =============================================================
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Database
    DB_USERNAME = os.getenv("DB_USERNAME")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_NAME = os.getenv("DB_NAME")

    DATABASE_URL = f"mysql+aiomysql://{DB_USERNAME}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    # For MySQL use: f"mysql+aiomysql://{DB_USERNAME}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    # Security
    SECRET_KEY = os.getenv("SECRET_KEY")
    ALGORITHM = "HS256"

    # YouTube API
    YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

    # App Settings
    MAX_PER_KEYWORD = 25
    DAYS_BACK = 3
    MIN_DURATION_SECONDS = 181  # > 180 seconds
    REGION_CODE = "IN"

    # Keyword Groups
    BIRDS_ANIMALS_KEYWORDS = [
        "Chilaka Kathalu", "Pichuka Kathalu", "Kaki Kathalu",
        "Pavuram Kathalu", "Birds Stories Telugu", "Birds stories telugu kids",
        "Animal stories Telugu", "Animal stories Telugu kids", "Elephant stories Telugu",
        "Lion stories Telugu", "Monkey stories Telugu", "Crow Fox stories Telugu",
        "Rabbit Turtle stories Telugu", "Telugu animated animal stories",
        "Panchatantra Telugu animals", "Panchatantra stories Telugu",
        "Neethi Kathalu animals Telugu"
    ]

    ANIMATION_KEYWORDS = [
        "Telugu animation", "Telugu animated stories", "Telugu animated cartoon",
        "Telugu cartoons kids", "Telugu cartoon episodes", "Kids cartoons Telugu",
        "Telugu 2D animation kids", "Telugu 3D animation kids",
        "Animated moral stories Telugu", "Educational animated stories Telugu",
        "Telugu cartoon stories kids"
    ]

    # Content Filters
    ALLOWED_CATEGORIES = {"1", "22", "24", "27", "15"}  # 1=Film&Animation, 22=People&Blogs, 24=Entertainment, 27=Education, 15=Pets&Animals

    MUST_CONTAIN_ANY = [
        "story", "stories", "kathalu", "kathali", "katha", "katalu",
        "chilaka", "pichuka", "pavuram", "kaki", "tuni", "bujji",
        "parrot", "crow", "sparrow", "pigeon", "bird", "birds",
        "animal", "animals", "elephant", "enaugu", "lion", "monkey",
        "rabbit", "turtle", "fox", "deer", "tiger", "bear", "horse",
        "పిచుక", "చిలక", "పావురం", "కాకి", "ఏనుగు",
        "cartoon", "cartoons", "animation", "animated", "toons", "anime",
        "moral", "neethi", "neeti", "panchatantra", "hitopadesha",
        "kids", "children", "child", "baby", "balalu", "బాలలు",
        "కథలు", "కథ", "నీతి", "కార్టూన్", "జంతువుల"
    ]

    MUST_NOT_CONTAIN = [
        "rhyme", "rhymes", "nursery", "lullaby", "song", "songs",
        "balaganapam", "గేయాలు", "పాటలు",
        "trailer", "teaser", "movie", "film", "cinema", "theatre",
        "review", "reaction", "interview", "press meet",
        "remix", "dj", "bhajan", "rap", "gaana", "album", "audio",
        "lyrical video", "lyric video", "full video song",
        "gaming", "gameplay", "gta", "minecraft", "freefire", "free fire",
        "bgmi", "pubg", "roblox", "fortnite", "among us",
        "news", "breaking", "live news", "election", "vote", "government",
        "parliament", "budget", "politics", "update", "latest news",
        "cricket", "ipl", "match", "highlights", "football", "kabaddi",
        "bollywood", "bigg boss", "web series", "serial", "natak",
        "bhojpuri", "punjabi", "haryanvi",
        "unboxing", "vlog", "prank", "challenge", "hack", "tutorial",
        "adult", "18+", "hot", "sexy", "romance", "couple"
    ]


config = Config()