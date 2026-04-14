"""
Public API Routes - No authentication required

All monitoring/viewing endpoints are public. Only write/control operations
require admin authentication (in admin.py).
"""
import os
import json
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.database import get_db
from core.config import settings
from models import SocialLink, CopyTradingLink, SiteSettings
from services.performance_service import get_performance_service
from services.trade_evaluation_service import get_trade_evaluation_service

router = APIRouter(prefix="/public", tags=["Public"])

# Upload directory for public file access
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")


@router.get("/performance")
async def get_performance(days: int = 30):
    """
    Get trading performance statistics with equity curve.

    Returns aggregated stats + equity_history for charting.
    """
    service = get_performance_service()
    stats = await service.get_performance_stats()
    stats["period_days"] = days

    # Build equity curve from cumulative PnL data
    balance = stats.get("total_equity", 0)
    total_pnl = stats.get("total_pnl", 0)
    base_equity = balance - total_pnl if balance > 0 else 0
    equity_history = []
    for point in stats.get("pnl_curve", []):
        equity_history.append({
            "time": point["date"],
            "value": round(base_equity + point["cumulative_pnl"], 2),
        })

    return {
        **stats,
        "equity_history": equity_history,
        "risk_metrics": {
            "sharpe_ratio": stats.get("sharpe_ratio", 0),
            "max_drawdown": stats.get("max_drawdown_percent", 0),
            "win_rate": stats.get("win_rate", 0),
            "risk_reward": stats.get("risk_reward", 0),
        }
    }


@router.get("/performance/summary")
async def get_performance_summary():
    """Get quick performance summary for homepage"""
    service = get_performance_service()
    stats = await service.get_performance_stats()

    return {
        "total_return_percent": stats["total_pnl_percent"],
        "win_rate": stats["win_rate"],
        "max_drawdown_percent": stats["max_drawdown_percent"],
        "total_trades": stats["total_trades"],
        "last_updated": stats["last_updated"],
    }


@router.get("/social-links")
async def get_social_links(db: AsyncSession = Depends(get_db)):
    """Get all enabled social media links"""
    result = await db.execute(
        select(SocialLink).where(SocialLink.enabled == True)
    )
    links = result.scalars().all()

    return [
        {
            "platform": link.platform,
            "url": link.url,
        }
        for link in links
    ]


@router.get("/copy-trading")
async def get_copy_trading_links(db: AsyncSession = Depends(get_db)):
    """Get all enabled copy trading links"""
    result = await db.execute(
        select(CopyTradingLink)
        .where(CopyTradingLink.enabled == True)
        .order_by(CopyTradingLink.sort_order)
    )
    links = result.scalars().all()

    return [
        {
            "exchange": link.exchange,
            "name": link.name,
            "url": link.url,
            "icon": link.icon,
        }
        for link in links
    ]


@router.get("/site-settings/{key}")
async def get_site_setting(key: str, db: AsyncSession = Depends(get_db)):
    """Get a specific site setting"""
    result = await db.execute(
        select(SiteSettings).where(SiteSettings.key == key)
    )
    setting = result.scalar_one_or_none()

    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")

    return {"key": setting.key, "value": setting.value}


@router.get("/site-branding")
async def get_site_branding(db: AsyncSession = Depends(get_db)):
    """Get site branding settings (logo, favicon, site name)"""
    branding_keys = ["logo_url", "favicon_url", "site_name", "site_tagline"]
    result = await db.execute(
        select(SiteSettings).where(SiteSettings.key.in_(branding_keys))
    )
    settings = result.scalars().all()

    branding = {s.key: s.value for s in settings}

    return {
        "logo_url": branding.get("logo_url"),
        "favicon_url": branding.get("favicon_url"),
        "site_name": branding.get("site_name", "AlgVex"),
        "site_tagline": branding.get("site_tagline"),
    }


@router.get("/uploads/{filename}")
async def get_public_upload(filename: str):
    """Serve uploaded files publicly (logos, favicons)"""
    # Path traversal protection: reject any path components
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Security: only allow specific prefixes for public access
    allowed_prefixes = ["logo_", "favicon_"]
    if not any(filename.startswith(prefix) for prefix in allowed_prefixes):
        raise HTTPException(status_code=403, detail="Access denied")

    filepath = os.path.join(UPLOAD_DIR, filename)
    # Verify resolved path is within UPLOAD_DIR
    if not os.path.realpath(filepath).startswith(os.path.realpath(UPLOAD_DIR)):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(filepath)


@router.get("/system-status")
async def get_system_status():
    """Get basic system status (running/stopped)"""
    from services import config_service

    status = config_service.get_service_status()

    return {
        "trading_active": status["running"],
        "status": "Running" if status["running"] else "Stopped",
    }



# v49.0: AI signal endpoints removed (latest-signal, signal-history, ai-analysis).
# Mechanical scoring data is served from /api/public/mechanical/* endpoints.


# =============================================================================
# Trade Evaluation Endpoints (v5.1)
# Expose trade quality metrics from decision_memory
# =============================================================================


@router.get("/trade-evaluation/summary")
async def get_trade_evaluation_summary(days: int = 30):
    """
    Get aggregate trade evaluation statistics.

    Public endpoint - returns grade distribution, R/R stats, confidence accuracy.
    No sensitive data (prices, conditions) included.

    Parameters
    ----------
    days : int, optional
        Number of days to look back (default: 30, 0 = all time)

    Returns
    -------
    Dict
        {
            "total_evaluated": int,
            "grade_distribution": {"A+": 3, "A": 5, "B": 4, ...},
            "direction_accuracy": float,  // Win rate %
            "avg_winning_rr": float,      // Average R/R for wins
            "avg_execution_quality": float,
            "avg_grade_score": float,     // Quality score 0-5
            "exit_type_distribution": {"TAKE_PROFIT": 10, ...},
            "confidence_accuracy": {
                "HIGH": {"total": 10, "wins": 7, "accuracy": 70.0},
                ...
            },
            "avg_hold_duration_min": int,
            "last_updated": str (ISO timestamp)
        }
    """
    service = get_trade_evaluation_service()
    days_filter = None if days == 0 else days
    return service.get_evaluation_summary(days=days_filter)


@router.get("/trade-evaluation/recent")
async def get_recent_trade_evaluations(limit: int = 20):
    """
    Get recent trade evaluations (public view - sanitized).

    Excludes sensitive fields (entry/exit prices, conditions).
    Suitable for displaying trade quality on public website.

    Parameters
    ----------
    limit : int, optional
        Maximum number of trades to return (default: 20, max: 100)

    Returns
    -------
    List[Dict]
        [
            {
                "grade": "A",
                "planned_rr": 2.0,
                "actual_rr": 1.8,
                "execution_quality": 0.9,
                "exit_type": "TAKE_PROFIT",
                "confidence": "HIGH",
                "hold_duration_min": 1847,
                "direction_correct": true,
                "timestamp": "2026-02-14T02:00:00"
            },
            ...
        ]
    """
    service = get_trade_evaluation_service()
    limit = min(limit, 100)  # Cap at 100 for performance
    return service.get_recent_trades(limit=limit, include_details=False)



# v49.0: Quality analysis endpoints removed (AI auditor deleted in v46).


# =============================================================================
# Trade Evaluation - Full Access (moved from admin)
# =============================================================================


@router.get("/trade-evaluation/full")
async def get_full_trade_evaluations(limit: int = 50):
    """
    Get detailed trade evaluations with all fields.

    Includes entry/exit prices, conditions, full evaluation data.
    """
    service = get_trade_evaluation_service()
    limit = min(limit, 500)
    return service.get_recent_trades(limit=limit, include_details=True)


@router.get("/trade-evaluation/export")
async def export_trade_evaluations(format: str = "json", days: Optional[int] = None):
    """
    Export trade evaluation data.

    Parameters
    ----------
    format : str
        'json' or 'csv'
    days : int, optional
        Number of days to export (None = all time)
    """
    service = get_trade_evaluation_service()
    return service.export_data(format=format, days=days)


@router.get("/trade-evaluation/attribution")
async def get_performance_attribution(days: int = 0):
    """
    Performance attribution — PnL breakdown by exit type, confidence,
    trend/counter-trend, and grade.
    """
    service = get_trade_evaluation_service()
    days_filter = None if days == 0 else days
    return service.get_performance_attribution(days=days_filter)


# =============================================================================
# Layer Orders (v7.2+) - Per-layer SL/TP tracking (moved from admin)
# =============================================================================


@router.get("/layer-orders")
async def get_layer_orders():
    """
    Get current layer orders (per-layer SL/TP) from data/layer_orders.json.

    Each layer contains: entry_price, quantity, side, sl_price, tp_price,
    sl_order_id, tp_order_id, confidence, timestamp.
    """
    algvex_path = Path(settings.ALGVEX_PATH) if settings.ALGVEX_PATH else Path(".")
    layer_file = str(algvex_path / "data" / "layer_orders.json")

    if not os.path.exists(layer_file):
        return {"layers": {}, "count": 0, "message": "No active layers"}

    try:
        with open(layer_file, 'r') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"layers": {}, "count": 0, "message": "Invalid format"}
        sanitized = {}
        for layer_id, layer in data.items():
            sanitized[layer_id] = {
                "entry_price": layer.get("entry_price", 0),
                "quantity": layer.get("quantity", 0),
                "side": layer.get("side", ""),
                "sl_price": layer.get("sl_price"),
                "tp_price": layer.get("tp_price"),
                "confidence": layer.get("confidence", ""),
                "timestamp": layer.get("timestamp", ""),
                "highest_price": layer.get("highest_price"),
                "lowest_price": layer.get("lowest_price"),
                "has_sl": bool(layer.get("sl_order_id")),
                "has_tp": bool(layer.get("tp_order_id")),
            }
        return {"layers": sanitized, "count": len(sanitized)}
    except Exception as e:
        return {"layers": {}, "count": 0, "error": str(e)}


# =============================================================================
# Safety Events - Emergency SL / Market Close log (moved from admin)
# =============================================================================


@router.get("/safety-events")
async def get_safety_events(limit: int = 50):
    """Get safety events (emergency SL, market close) from data/safety_events.json."""
    algvex_path = Path(settings.ALGVEX_PATH) if settings.ALGVEX_PATH else Path(".")
    events_file = str(algvex_path / "data" / "safety_events.json")

    if not os.path.exists(events_file):
        return {"events": [], "count": 0}

    try:
        with open(events_file, 'r') as f:
            events = json.load(f)
        if not isinstance(events, list):
            return {"events": [], "count": 0}
        return {"events": events[:limit], "count": len(events)}
    except Exception as e:
        return {"events": [], "count": 0, "error": str(e)}


@router.get("/sltp-adjustments")
async def get_sltp_adjustments(limit: int = 50):
    """Get SL/TP adjustment history (post-fill TP adjustments, etc.)."""
    algvex_path = Path(settings.ALGVEX_PATH) if settings.ALGVEX_PATH else Path(".")
    adj_file = str(algvex_path / "data" / "sltp_adjustments.json")

    if not os.path.exists(adj_file):
        return {"adjustments": [], "count": 0}

    try:
        with open(adj_file, 'r') as f:
            adjustments = json.load(f)
        if not isinstance(adjustments, list):
            return {"adjustments": [], "count": 0}
        return {"adjustments": adjustments[:limit], "count": len(adjustments)}
    except Exception as e:
        return {"adjustments": [], "count": 0, "error": str(e)}



# v49.0: Extended reflections endpoint removed (LLM reflections deleted in v46).


# =============================================================================
# Feature Snapshots (v27.0) — Deterministic replay (moved from admin)
# =============================================================================


@router.get("/feature-snapshots")
async def list_feature_snapshots(limit: int = 20):
    """List recent feature snapshots from data/feature_snapshots/."""
    algvex_path = Path(settings.ALGVEX_PATH) if settings.ALGVEX_PATH else Path(".")
    snapshots_dir = algvex_path / "data" / "feature_snapshots"

    if not snapshots_dir.exists():
        return {"snapshots": [], "count": 0}

    try:
        files = sorted(snapshots_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
        snapshots = []
        for f in files[:limit]:
            snapshots.append({
                "filename": f.name,
                "size_bytes": f.stat().st_size,
                "modified": os.path.getmtime(str(f)),
            })
        return {"snapshots": snapshots, "count": len(files)}
    except Exception as e:
        return {"snapshots": [], "count": 0, "error": str(e)}


@router.get("/feature-snapshots/{filename}")
async def get_feature_snapshot(filename: str):
    """Get a specific feature snapshot by filename."""
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="Invalid file type")

    algvex_path = Path(settings.ALGVEX_PATH) if settings.ALGVEX_PATH else Path(".")
    filepath = algvex_path / "data" / "feature_snapshots" / filename

    snapshots_dir = algvex_path / "data" / "feature_snapshots"
    if not filepath.resolve().is_relative_to(snapshots_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")

    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Trading Memory (v5.9) — Trade history with evaluations (moved from admin)
# =============================================================================


@router.get("/trading-memory")
async def get_trading_memory(limit: int = 50):
    """Get trading memory entries from data/trading_memory.json."""
    algvex_path = Path(settings.ALGVEX_PATH) if settings.ALGVEX_PATH else Path(".")
    memory_file = str(algvex_path / "data" / "trading_memory.json")

    if not os.path.exists(memory_file):
        return {"memories": [], "count": 0}

    try:
        with open(memory_file, 'r') as f:
            data = json.load(f)
        if isinstance(data, list):
            return {"memories": data[-limit:], "count": len(data)}
        if isinstance(data, dict):
            items = data.get("trades", data.get("memories", []))
            return {"memories": items[-limit:], "count": len(items)}
        return {"memories": [], "count": 0}
    except Exception as e:
        return {"memories": [], "count": 0, "error": str(e)}


# =============================================================================
# Recent Trades (moved from admin, was duplicate of performance/trades)
# =============================================================================


@router.get("/trades/recent")
async def get_recent_trades(limit: int = 20):
    """Get recent trades for dashboard."""
    from services import trading_service

    trades = await trading_service.get_trade_history(limit=limit)
    return trades



# v49.0: Regime endpoint removed (page deleted). Regime data available via /mechanical/state.

