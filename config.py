# config.py
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

    # Security
    SECRET_KEY = os.getenv("SECRET_KEY")

    # YouTube API
    YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

    # App Settings (matching standalone script)
    MAX_PER_KEYWORD = 20  # Matches standalone
    DAYS_BACK = 3  # Or 4, but standalone uses 3
    MIN_DURATION_SECONDS = 190  # Matches standalone
    REGION_CODE = "IN"


config = Config()