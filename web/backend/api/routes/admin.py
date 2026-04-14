"""
Admin API Routes - Authentication required

Only configuration and control operations that modify state.
All read-only monitoring endpoints have been moved to public.py.
"""
import os
import uuid
import shutil
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, Any, Dict, List

from core.database import get_db
from core.config import settings, read_algvex_env, write_algvex_env
from models import SocialLink, CopyTradingLink, SiteSettings
from services import config_service
from api.deps import get_current_admin

router = APIRouter(prefix="/admin", tags=["Admin"])

# Upload directory for logos and assets
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ============================================================================
# Schemas
# ============================================================================
class SocialLinkUpdate(BaseModel):
    platform: str
    url: Optional[str] = None
    enabled: bool = True


class CopyTradingLinkCreate(BaseModel):
    exchange: str
    name: str
    url: Optional[str] = None
    trader_id: Optional[str] = None
    enabled: bool = True
    icon: Optional[str] = None
    sort_order: int = 0


class CopyTradingLinkUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    trader_id: Optional[str] = None
    enabled: Optional[bool] = None
    icon: Optional[str] = None
    sort_order: Optional[int] = None


class ConfigUpdate(BaseModel):
    path: str
    value: Any


class ConfigBatchUpdate(BaseModel):
    updates: List[ConfigUpdate]


class ServiceAction(BaseModel):
    action: str  # restart, stop, start
    confirm: bool = False


class TelegramConfigUpdate(BaseModel):
    """Partial update for Telegram-related env vars."""
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    TELEGRAM_NOTIFICATION_BOT_TOKEN: Optional[str] = None
    TELEGRAM_NOTIFICATION_CHAT_ID: Optional[str] = None


# ============================================================================
# Strategy Configuration
# ============================================================================
@router.get("/config")
async def get_strategy_config(admin=Depends(get_current_admin)):
    """Get full strategy configuration"""
    config = config_service.read_strategy_config()
    return config


@router.get("/config/sections")
async def get_config_sections(admin=Depends(get_current_admin)):
    """Get configuration organized by sections for UI"""
    sections = config_service.get_config_sections()
    return sections


@router.get("/config/value")
async def get_config_value(
    path: str,
    admin=Depends(get_current_admin)
):
    """Get a specific configuration value by path"""
    value = config_service.get_config_value(path)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Config path not found: {path}")
    return {"path": path, "value": value}


@router.put("/config")
async def update_strategy_config(
    update: ConfigUpdate,
    admin=Depends(get_current_admin)
):
    """Update a specific configuration value"""
    success = config_service.update_config_value(update.path, update.value)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update configuration")
    return {"success": True, "message": f"Updated {update.path}", "requires_restart": True}


@router.put("/config/batch")
async def update_config_batch(
    batch: ConfigBatchUpdate,
    admin=Depends(get_current_admin)
):
    """Update multiple configuration values at once"""
    results = []
    for update in batch.updates:
        success = config_service.update_config_value(update.path, update.value)
        results.append({
            "path": update.path,
            "success": success
        })

    failed = [r for r in results if not r["success"]]
    if failed:
        return {
            "success": False,
            "message": f"Some updates failed",
            "results": results,
            "requires_restart": True
        }

    return {
        "success": True,
        "message": f"Updated {len(results)} values",
        "results": results,
        "requires_restart": True
    }


@router.put("/config/full")
async def update_full_config(
    config: dict,
    admin=Depends(get_current_admin)
):
    """Update full strategy configuration"""
    success = config_service.write_strategy_config(config)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update configuration")
    return {"success": True, "message": "Configuration updated", "requires_restart": True}


# ============================================================================
# Service Control
# ============================================================================
@router.get("/service/status")
async def get_service_status(admin=Depends(get_current_admin)):
    """Get detailed service status"""
    return config_service.get_service_status()


@router.post("/service/control")
async def control_service(
    action: ServiceAction,
    admin=Depends(get_current_admin)
):
    """Control the trading service (restart/stop/start)"""
    if not action.confirm:
        raise HTTPException(
            status_code=400,
            detail="Please confirm the action by setting confirm=true"
        )

    if action.action == "restart":
        success, message = config_service.restart_service()
    elif action.action == "stop":
        success, message = config_service.stop_service()
    elif action.action == "start":
        success, message = config_service.start_service()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action.action}")

    if not success:
        raise HTTPException(status_code=500, detail=message)

    return {"success": True, "message": message}


# ============================================================================
# Logs
# ============================================================================
@router.get("/service/logs")
async def get_service_logs(
    lines: int = 100,
    source: str = "journalctl",
    admin=Depends(get_current_admin)
):
    """
    Get recent service logs

    source: "journalctl" or "file"
    """
    if lines > 1000:
        lines = 1000

    if source == "file":
        logs = config_service.get_log_file_content(lines)
    else:
        logs = config_service.get_recent_logs(lines)

    return {"logs": logs, "source": source, "lines": lines}


# ============================================================================
# System Info & Diagnostics
# ============================================================================
@router.get("/system/info")
async def get_system_info(admin=Depends(get_current_admin)):
    """Get system information"""
    return config_service.get_system_info()


@router.get("/system/diagnostics")
async def run_diagnostics(admin=Depends(get_current_admin)):
    """Run system diagnostics"""
    return config_service.run_diagnostics()


# ============================================================================
# Telegram Configuration (reads/writes ~/.env.algvex)
# ============================================================================

TELEGRAM_ENV_KEYS = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_NOTIFICATION_BOT_TOKEN",
    "TELEGRAM_NOTIFICATION_CHAT_ID",
]


def _mask_token(value: str) -> str:
    """Mask a token for display: show last 4 chars only."""
    if not value or len(value) <= 4:
        return value
    return "*" * (len(value) - 4) + value[-4:]


@router.get("/telegram-config")
async def get_telegram_config(admin=Depends(get_current_admin)):
    """Read Telegram configuration from ~/.env.algvex (tokens masked)."""
    env_data = read_algvex_env()
    result = {}
    for key in TELEGRAM_ENV_KEYS:
        raw = env_data.get(key, "")
        if "TOKEN" in key and raw:
            result[key] = _mask_token(raw)
        else:
            result[key] = raw
    result["env_path"] = str(settings.ALGVEX_ENV_PATH)
    return result


@router.put("/telegram-config")
async def update_telegram_config(
    data: TelegramConfigUpdate,
    admin=Depends(get_current_admin),
):
    """Update Telegram configuration in ~/.env.algvex.

    Only non-None fields are updated.  A field set to empty string removes the key.
    Token fields that look masked (contain consecutive '*') are skipped to prevent
    accidentally overwriting the real token with the masked placeholder.
    """
    updates: Dict[str, str] = {}
    for key in TELEGRAM_ENV_KEYS:
        val = getattr(data, key, None)
        if val is None:
            continue  # field not provided → skip
        if "TOKEN" in key and val and "***" in val:
            continue  # masked placeholder → skip
        updates[key] = val

    if not updates:
        return {"success": True, "message": "No changes"}

    ok = write_algvex_env(updates)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to write env file")

    return {
        "success": True,
        "message": f"Updated {len(updates)} Telegram config(s). Restart trading service to take effect.",
        "requires_restart": True,
        "updated_keys": list(updates.keys()),
    }


# ============================================================================
# Social Links
# ============================================================================
@router.get("/social-links")
async def list_social_links(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin)
):
    """List all social links (including disabled)"""
    result = await db.execute(select(SocialLink))
    links = result.scalars().all()
    return [
        {
            "id": link.id,
            "platform": link.platform,
            "url": link.url,
            "enabled": link.enabled,
        }
        for link in links
    ]


@router.put("/social-links/{platform}")
async def update_social_link(
    platform: str,
    data: SocialLinkUpdate,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin)
):
    """Update or create a social link"""
    result = await db.execute(
        select(SocialLink).where(SocialLink.platform == platform)
    )
    link = result.scalar_one_or_none()

    if link:
        link.url = data.url
        link.enabled = data.enabled
    else:
        link = SocialLink(
            platform=platform,
            url=data.url,
            enabled=data.enabled
        )
        db.add(link)

    await db.commit()
    return {"success": True, "platform": platform}


# ============================================================================
# Copy Trading Links
# ============================================================================
@router.get("/copy-trading")
async def list_copy_trading_links(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin)
):
    """List all copy trading links"""
    result = await db.execute(
        select(CopyTradingLink).order_by(CopyTradingLink.sort_order)
    )
    links = result.scalars().all()
    return [
        {
            "id": link.id,
            "exchange": link.exchange,
            "name": link.name,
            "url": link.url,
            "trader_id": link.trader_id,
            "enabled": link.enabled,
            "icon": link.icon,
            "sort_order": link.sort_order,
        }
        for link in links
    ]


@router.post("/copy-trading")
async def create_copy_trading_link(
    data: CopyTradingLinkCreate,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin)
):
    """Create a new copy trading link"""
    link = CopyTradingLink(
        exchange=data.exchange,
        name=data.name,
        url=data.url,
        trader_id=data.trader_id,
        enabled=data.enabled,
        icon=data.icon,
        sort_order=data.sort_order,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return {"success": True, "id": link.id}


@router.put("/copy-trading/{link_id}")
async def update_copy_trading_link(
    link_id: int,
    data: CopyTradingLinkUpdate,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin)
):
    """Update a copy trading link"""
    result = await db.execute(
        select(CopyTradingLink).where(CopyTradingLink.id == link_id)
    )
    link = result.scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="Link not found")

    if data.name is not None:
        link.name = data.name
    if data.url is not None:
        link.url = data.url
    if data.trader_id is not None:
        link.trader_id = data.trader_id
    if data.enabled is not None:
        link.enabled = data.enabled
    if data.icon is not None:
        link.icon = data.icon
    if data.sort_order is not None:
        link.sort_order = data.sort_order

    await db.commit()
    return {"success": True, "id": link_id}


@router.delete("/copy-trading/{link_id}")
async def delete_copy_trading_link(
    link_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin)
):
    """Delete a copy trading link"""
    result = await db.execute(
        select(CopyTradingLink).where(CopyTradingLink.id == link_id)
    )
    link = result.scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="Link not found")

    await db.delete(link)
    await db.commit()
    return {"success": True}


# ============================================================================
# Site Settings
# ============================================================================
@router.get("/settings")
async def list_site_settings(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin)
):
    """List all site settings"""
    result = await db.execute(select(SiteSettings))
    settings_list = result.scalars().all()
    return {s.key: s.value for s in settings_list}


@router.put("/settings/{key}")
async def update_site_setting(
    key: str,
    value: str,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin)
):
    """Update a site setting"""
    result = await db.execute(
        select(SiteSettings).where(SiteSettings.key == key)
    )
    setting = result.scalar_one_or_none()

    if setting:
        setting.value = value
    else:
        setting = SiteSettings(key=key, value=value)
        db.add(setting)

    await db.commit()
    return {"success": True, "key": key}


# ============================================================================
# File Upload (Logo, Favicon, etc.)
# ============================================================================
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml", "image/x-icon"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


@router.post("/upload/logo")
async def upload_logo(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin)
):
    """Upload site logo"""
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: PNG, JPG, GIF, WebP, SVG, ICO"
        )

    # Check file size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")

    # Generate unique filename
    ext = os.path.splitext(file.filename)[1] or ".png"
    filename = f"logo_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    # Save file
    with open(filepath, "wb") as f:
        f.write(content)

    # Save to database (use public URL path)
    logo_url = f"/api/public/uploads/{filename}"
    result = await db.execute(
        select(SiteSettings).where(SiteSettings.key == "logo_url")
    )
    setting = result.scalar_one_or_none()

    if setting:
        # Delete old logo file if exists
        old_filename = setting.value.split("/")[-1] if setting.value else None
        if old_filename:
            old_path = os.path.join(UPLOAD_DIR, old_filename)
            if os.path.exists(old_path):
                os.remove(old_path)
        setting.value = logo_url
    else:
        setting = SiteSettings(key="logo_url", value=logo_url)
        db.add(setting)

    await db.commit()
    return {"success": True, "url": logo_url, "filename": filename}


@router.post("/upload/favicon")
async def upload_favicon(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin)
):
    """Upload site favicon"""
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: PNG, JPG, GIF, WebP, SVG, ICO"
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")

    ext = os.path.splitext(file.filename)[1] or ".ico"
    filename = f"favicon_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(content)

    favicon_url = f"/api/public/uploads/{filename}"
    result = await db.execute(
        select(SiteSettings).where(SiteSettings.key == "favicon_url")
    )
    setting = result.scalar_one_or_none()

    if setting:
        old_filename = setting.value.split("/")[-1] if setting.value else None
        if old_filename:
            old_path = os.path.join(UPLOAD_DIR, old_filename)
            if os.path.exists(old_path):
                os.remove(old_path)
        setting.value = favicon_url
    else:
        setting = SiteSettings(key="favicon_url", value=favicon_url)
        db.add(setting)

    await db.commit()
    return {"success": True, "url": favicon_url, "filename": filename}


@router.get("/uploads/{filename}")
async def get_uploaded_file(filename: str, admin=Depends(get_current_admin)):
    """Serve uploaded files (admin only)"""
    # Path traversal protection: reject any path components
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    filepath = os.path.join(UPLOAD_DIR, filename)
    # Verify resolved path is within UPLOAD_DIR
    if not os.path.realpath(filepath).startswith(os.path.realpath(UPLOAD_DIR)):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(filepath)



# =============================================================================
# NOTE: All read-only monitoring endpoints (performance, trades, signals,
# layer-orders, safety-events, sltp-adjustments, trade-evaluation,
# extended-reflections, feature-snapshots, trading-memory, quality-analysis)
# have been moved to public.py — no authentication required.
# Admin routes now only contain write/control operations.
# =============================================================================
