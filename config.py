import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ============================================================
    # PostgreSQL Database Configuration
    # ============================================================
    DB_USERNAME = os.getenv("DB_USERNAME", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "youtube_db")

    DATABASE_URL = (
        f"postgresql+asyncpg://{DB_USERNAME}:{DB_PASSWORD}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )

    # ============================================================
    # Security
    # ============================================================
    SECRET_KEY = os.getenv("SECRET_KEY", "your_secret_key")

    # ============================================================
    # YouTube API
    # ============================================================
    YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

    # ============================================================
    # Application Settings
    # ============================================================
    MAX_PER_KEYWORD = int(os.getenv("MAX_PER_KEYWORD", 20))
    DAYS_BACK = int(os.getenv("DAYS_BACK", 3))
    MIN_DURATION_SECONDS = int(os.getenv("MIN_DURATION_SECONDS", 190))
    REGION_CODE = os.getenv("REGION_CODE", "IN")


config = Config()