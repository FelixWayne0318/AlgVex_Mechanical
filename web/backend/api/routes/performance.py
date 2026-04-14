"""
Performance API Routes
Endpoints for trading performance metrics and analytics
"""
from fastapi import APIRouter, HTTPException

from services.performance_service import get_performance_service
from services.notification_service import get_notification_service

router = APIRouter(prefix="/api/performance", tags=["Performance"])


# ============================================================================
# Diagnostic Endpoint
# ============================================================================

@router.get("/diagnostic")
async def check_api_connection():
    """Diagnostic: Check Binance API connectivity and credentials"""
    try:
        service = get_performance_service()
        result = await service.check_connection()
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================================
# Performance Stats Endpoints
# ============================================================================

@router.get("/stats")
async def get_performance_stats():
    """Get comprehensive trading performance statistics"""
    try:
        service = get_performance_service()
        stats = await service.get_performance_stats()
        return {"success": True, "data": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades")
async def get_recent_trades(limit: int = 20):
    """Get recent trades for timeline display"""
    try:
        service = get_performance_service()
        trades = await service.get_recent_trades_formatted(limit=limit)
        return {"success": True, "data": trades}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/equity-curve")
async def get_equity_curve():
    """Get equity curve data for charting"""
    try:
        service = get_performance_service()
        stats = await service.get_performance_stats()
        return {"success": True, "data": stats.get("pnl_curve", [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# v49.0: AI signal log endpoints removed (signal_log_service deleted).
# Mechanical signal data served from /api/public/mechanical/signal-history.

# ============================================================================
# Notification Endpoints
# ============================================================================

@router.get("/notifications")
async def get_notifications(
    limit: int = 50,
    type: str = None,
    unread_only: bool = False
):
    """Get notifications with optional filtering"""
    try:
        service = get_notification_service()
        notifications = service.get_notifications(
            limit=limit,
            notification_type=type,
            unread_only=unread_only
        )
        return {"success": True, "data": notifications}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/notifications/unread-count")
async def get_unread_count():
    """Get count of unread notifications"""
    try:
        service = get_notification_service()
        count = service.get_unread_count()
        return {"success": True, "data": {"count": count}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str):
    """Mark a notification as read"""
    try:
        service = get_notification_service()
        success = service.mark_as_read(notification_id)
        if not success:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notifications/read-all")
async def mark_all_notifications_read():
    """Mark all notifications as read"""
    try:
        service = get_notification_service()
        count = service.mark_all_as_read()
        return {"success": True, "data": {"marked_count": count}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/notifications/{notification_id}")
async def delete_notification(notification_id: str):
    """Delete a notification"""
    try:
        service = get_notification_service()
        success = service.delete_notification(notification_id)
        if not success:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


