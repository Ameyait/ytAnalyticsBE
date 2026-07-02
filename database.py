from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base

from config import config

# ============================================================
# PostgreSQL Async Engine
# ============================================================
engine = create_async_engine(
    config.DATABASE_URL,
    echo=False,               # Set True while debugging
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,       # Checks connection before using it
    pool_recycle=3600,        # Recycle connections every hour
)

# ============================================================
# Async Session Factory
# ============================================================
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ============================================================
# Base Class
# ============================================================
Base = declarative_base()


# ============================================================
# Database Dependency
# ============================================================
async def get_db():
    """
    FastAPI Dependency
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ============================================================
# Initialize Database
# ============================================================
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ============================================================
# Close Database Engine
# ============================================================
async def close_db():
    await engine.dispose()