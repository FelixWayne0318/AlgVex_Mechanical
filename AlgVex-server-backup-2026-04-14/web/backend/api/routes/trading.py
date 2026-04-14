"""
Trading API Routes - Real-time trading data from Binance
"""
from fastapi import APIRouter, Depends, Query
from typing import Optional

from services.trading_service import trading_service
from api.deps import get_current_admin

router = APIRouter(prefix="/trading", tags=["Trading"])


# ============================================================================
# Public Market Data (No Auth Required)
# ============================================================================
@router.get("/ticker/{symbol}")
async def get_ticker(symbol: str = "BTCUSDT"):
    """Get 24hr ticker for a symbol"""
    data = await trading_service.get_ticker(symbol)
    return data or {"error": "Failed to fetch ticker"}


@router.get("/mark-price/{symbol}")
async def get_mark_price(symbol: str = "BTCUSDT"):
    """Get mark price and funding rate"""
    data = await trading_service.get_mark_price(symbol)
    return data or {"error": "Failed to fetch mark price"}


@router.get("/klines/{symbol}")
async def get_klines(
    symbol: str = "BTCUSDT",
    interval: str = Query("30m", description="Kline interval (1m, 5m, 15m, 30m, 1h, 4h, 1d)"),
    limit: int = Query(100, ge=1, le=1500)
):
    """Get kline/candlestick data"""
    data = await trading_service.get_klines(symbol, interval, limit)
    return {"symbol": symbol, "interval": interval, "klines": data}


@router.get("/orderbook/{symbol}")
async def get_order_book(
    symbol: str = "BTCUSDT",
    limit: int = Query(20, ge=5, le=100)
):
    """Get order book depth"""
    data = await trading_service.get_order_book(symbol, limit)
    return data or {"error": "Failed to fetch order book"}


@router.get("/long-short-ratio/{symbol}")
async def get_long_short_ratio(
    symbol: str = "BTCUSDT",
    period: str = Query("30m", description="Period (5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d)"),
    limit: int = Query(10, ge=1, le=100)
):
    """Get long/short account ratio"""
    data = await trading_service.get_long_short_ratio(symbol, period, limit)
    return {"symbol": symbol, "period": period, "data": data}


@router.get("/open-interest/{symbol}")
async def get_open_interest(symbol: str = "BTCUSDT"):
    """Get open interest and 24h change"""
    data = await trading_service.get_open_interest(symbol)
    return data or {"error": "Failed to fetch open interest"}


# ============================================================================
# Protected Account Data (Auth Required)
# ============================================================================
@router.get("/account")
async def get_account(admin=Depends(get_current_admin)):
    """Get account information with balances"""
    data = await trading_service.get_account_info()
    if data:
        return data
    return {"error": "Failed to fetch account info", "detail": "Check API credentials"}


@router.get("/positions")
async def get_positions(
    symbol: Optional[str] = None,
    admin=Depends(get_current_admin)
):
    """Get all open positions"""
    data = await trading_service.get_positions(symbol)
    return {"positions": data, "count": len(data)}


@router.get("/orders/open")
async def get_open_orders(
    symbol: Optional[str] = None,
    admin=Depends(get_current_admin)
):
    """Get all open orders"""
    data = await trading_service.get_open_orders(symbol)
    return {"orders": data, "count": len(data)}


@router.get("/orders/history")
async def get_order_history(
    symbol: str = "BTCUSDT",
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(100, ge=1, le=500),
    admin=Depends(get_current_admin)
):
    """Get order history"""
    data = await trading_service.get_order_history(symbol, days, limit)
    return {"orders": data, "count": len(data)}


@router.get("/trades")
async def get_trade_history(
    symbol: str = "BTCUSDT",
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(100, ge=1, le=500),
    admin=Depends(get_current_admin)
):
    """Get trade/fill history"""
    data = await trading_service.get_trade_history(symbol, days, limit)
    return {"trades": data, "count": len(data)}


@router.get("/income")
async def get_income_history(
    income_type: Optional[str] = Query(None, description="REALIZED_PNL, FUNDING_FEE, COMMISSION, TRANSFER"),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(500, ge=1, le=1000),
    admin=Depends(get_current_admin)
):
    """Get income history (PnL, funding, commissions)"""
    data = await trading_service.get_income_history(income_type, days, limit)
    return {"income": data, "count": len(data)}


