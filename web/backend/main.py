"""
AlgVex API - FastAPI Backend for AlgVex Web Interface
"""
import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
# StaticFiles import removed - uploads served via API routes for security
from starlette.middleware.sessions import SessionMiddleware

from core.config import settings, load_algvex_env
from core.database import init_db
from api import public_router, admin_router, auth_router, trading_router, websocket_router, performance_router, srp_router, srp_admin_router, mechanical_router

# Ensure uploads directory exists
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    print(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    # Load AlgVex environment
    load_algvex_env()

    # Initialize database
    await init_db()
    print("Database initialized")

    # Seed default data
    await seed_default_data()

    yield

    # Shutdown
    print("Shutting down...")


async def seed_default_data():
    """Seed default social links and settings"""
    from sqlalchemy import select
    from core.database import async_session_maker
    from models import SocialLink, CopyTradingLink

    async with async_session_maker() as db:
        # Check if already seeded
        result = await db.execute(select(SocialLink))
        if result.scalars().first():
            return

        # Default social links
        social_links = [
            SocialLink(platform="telegram", url=None, enabled=True),          # Notification group
            SocialLink(platform="telegram_chat", url=None, enabled=True),     # Community chat group
            SocialLink(platform="twitter", url=None, enabled=True),
            SocialLink(platform="discord", url=None, enabled=False),
        ]
        db.add_all(social_links)

        # Default copy trading links
        copy_links = [
            CopyTradingLink(
                exchange="binance",
                name="Binance Copy Trading",
                url=None,
                enabled=True,
                icon="binance",
                sort_order=0
            ),
            CopyTradingLink(
                exchange="bybit",
                name="Bybit Copy Trading",
                url=None,
                enabled=False,
                icon="bybit",
                sort_order=1
            ),
            CopyTradingLink(
                exchange="okx",
                name="OKX Copy Trading",
                url=None,
                enabled=False,
                icon="okx",
                sort_order=2
            ),
            CopyTradingLink(
                exchange="bitget",
                name="Bitget Copy Trading",
                url=None,
                enabled=False,
                icon="bitget",
                sort_order=3
            ),
        ]
        db.add_all(copy_links)

        await db.commit()
        print("Default data seeded")


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    description="Algorithmic Cryptocurrency Trading System - Web Interface",
    version=settings.APP_VERSION,
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url="/api/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

# Session middleware (required for OAuth)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    same_site="lax",
    https_only=False,  # Allow cookies over HTTP for proxy setup
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

# Include routers
app.include_router(public_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(trading_router, prefix="/api")
app.include_router(websocket_router, prefix="/api")
app.include_router(performance_router)
app.include_router(srp_router, prefix="/api")
app.include_router(srp_admin_router, prefix="/api")
app.include_router(mechanical_router, prefix="/api")

# Note: uploads are served via /api/public/uploads/{filename} route for security
# (only logo_ and favicon_ prefixes allowed)
# Old static mount removed to avoid route conflicts


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


@app.get("/api")
async def api_root():
    """API root"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/api/docs" if settings.DEBUG else "Disabled in production",
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
