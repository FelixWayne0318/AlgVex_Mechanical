"""
WebSocket routes for real-time data streaming
"""
import asyncio
import json
import logging
from typing import Set, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import jwt, JWTError
from core.config import settings
from services import trading_service

logger = logging.getLogger(__name__)


def _validate_ws_token(token: Optional[str]) -> bool:
    """Validate JWT token for WebSocket connections"""
    if not token:
        return False
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email = payload.get("sub")
        if not email:
            return False
        if settings.ADMIN_EMAILS and email not in settings.ADMIN_EMAILS:
            return False
        return True
    except JWTError:
        return False

router = APIRouter(prefix="/ws", tags=["WebSocket"])


class ConnectionManager:
    """Manage WebSocket connections"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.ticker_connections: dict[str, Set[WebSocket]] = {}
        self.account_connections: Set[WebSocket] = set()
        self.positions_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        # Remove from all subscription sets
        self.account_connections.discard(websocket)
        self.positions_connections.discard(websocket)
        for symbol_set in self.ticker_connections.values():
            symbol_set.discard(websocket)

    def subscribe_ticker(self, websocket: WebSocket, symbol: str):
        if symbol not in self.ticker_connections:
            self.ticker_connections[symbol] = set()
        self.ticker_connections[symbol].add(websocket)

    def subscribe_account(self, websocket: WebSocket):
        self.account_connections.add(websocket)

    def subscribe_positions(self, websocket: WebSocket):
        self.positions_connections.add(websocket)

    async def broadcast_to_symbol(self, symbol: str, message: dict):
        if symbol in self.ticker_connections:
            disconnected = set()
            for connection in self.ticker_connections[symbol]:
                try:
                    await connection.send_json(message)
                except Exception:
                    disconnected.add(connection)
            for conn in disconnected:
                self.disconnect(conn)

    async def broadcast_account(self, message: dict):
        disconnected = set()
        for connection in self.account_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)
        for conn in disconnected:
            self.disconnect(conn)

    async def broadcast_positions(self, message: dict):
        disconnected = set()
        for connection in self.positions_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


@router.websocket("/ticker/{symbol}")
async def websocket_ticker(websocket: WebSocket, symbol: str):
    """
    Real-time ticker updates for a symbol
    Streams: lastPrice, priceChange, priceChangePercent, volume, high, low
    """
    await manager.connect(websocket)
    manager.subscribe_ticker(websocket, symbol.upper())

    try:
        while True:
            try:
                # Fetch latest ticker
                ticker = await trading_service.get_ticker(symbol.upper())
                if ticker:
                    await websocket.send_json({
                        "type": "ticker",
                        "symbol": symbol.upper(),
                        "data": {
                            "lastPrice": ticker.get("lastPrice"),
                            "priceChange": ticker.get("priceChange"),
                            "priceChangePercent": ticker.get("priceChangePercent"),
                            "highPrice": ticker.get("highPrice"),
                            "lowPrice": ticker.get("lowPrice"),
                            "volume": ticker.get("volume"),
                            "quoteVolume": ticker.get("quoteVolume"),
                        },
                        "timestamp": asyncio.get_event_loop().time()
                    })
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": str(e)
                })

            # Wait before next update
            await asyncio.sleep(1)

    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/prices")
async def websocket_prices(websocket: WebSocket):
    """
    Real-time price updates for multiple symbols
    Client sends: {"subscribe": ["BTCUSDT", "ETHUSDT"]}
    """
    await manager.connect(websocket)
    subscribed_symbols: Set[str] = set()

    async def send_prices():
        while True:
            try:
                for symbol in subscribed_symbols:
                    ticker = await trading_service.get_ticker(symbol)
                    if ticker:
                        await websocket.send_json({
                            "type": "price",
                            "symbol": symbol,
                            "price": ticker.get("lastPrice"),
                            "change": ticker.get("priceChangePercent"),
                        })
                await asyncio.sleep(1)
            except Exception:
                break

    price_task = None

    try:
        while True:
            # Receive subscription messages
            data = await websocket.receive_json()

            if "subscribe" in data:
                symbols = data["subscribe"]
                if isinstance(symbols, list):
                    subscribed_symbols.update(s.upper() for s in symbols)
                    for s in symbols:
                        manager.subscribe_ticker(websocket, s.upper())

                    # Start price streaming task
                    if price_task is None or price_task.done():
                        price_task = asyncio.create_task(send_prices())

                    await websocket.send_json({
                        "type": "subscribed",
                        "symbols": list(subscribed_symbols)
                    })

            elif "unsubscribe" in data:
                symbols = data["unsubscribe"]
                if isinstance(symbols, list):
                    for s in symbols:
                        subscribed_symbols.discard(s.upper())
                    await websocket.send_json({
                        "type": "unsubscribed",
                        "symbols": list(subscribed_symbols)
                    })

    except WebSocketDisconnect:
        if price_task:
            price_task.cancel()
        manager.disconnect(websocket)


@router.websocket("/account")
async def websocket_account(
    websocket: WebSocket,
    token: str = Query(None)
):
    """
    Real-time account updates (balance, PnL)
    Requires authentication token
    """
    if not _validate_ws_token(token):
        await websocket.close(code=4001, reason="Authentication required")
        return

    await manager.connect(websocket)
    manager.subscribe_account(websocket)

    try:
        while True:
            try:
                # Fetch account info
                account = await trading_service.get_account_info()
                if account:
                    await websocket.send_json({
                        "type": "account",
                        "data": {
                            "totalWalletBalance": account.get("totalWalletBalance"),
                            "totalUnrealizedProfit": account.get("totalUnrealizedProfit"),
                            "totalMarginBalance": account.get("totalMarginBalance"),
                            "availableBalance": account.get("availableBalance"),
                            "totalInitialMargin": account.get("totalInitialMargin"),
                            "totalMaintMargin": account.get("totalMaintMargin"),
                        },
                        "timestamp": asyncio.get_event_loop().time()
                    })
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": str(e)
                })

            # Update every 5 seconds
            await asyncio.sleep(5)

    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/positions")
async def websocket_positions(
    websocket: WebSocket,
    token: str = Query(None)
):
    """
    Real-time position updates
    Requires authentication token
    """
    if not _validate_ws_token(token):
        await websocket.close(code=4001, reason="Authentication required")
        return

    await manager.connect(websocket)
    manager.subscribe_positions(websocket)

    try:
        while True:
            try:
                # Fetch positions
                positions = await trading_service.get_positions()
                if positions:
                    # Filter active positions
                    active = [
                        p for p in positions
                        if float(p.get("positionAmt", 0)) != 0
                    ]
                    await websocket.send_json({
                        "type": "positions",
                        "data": active,
                        "count": len(active),
                        "timestamp": asyncio.get_event_loop().time()
                    })
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": str(e)
                })

            # Update every 3 seconds
            await asyncio.sleep(3)

    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/orders")
async def websocket_orders(
    websocket: WebSocket,
    token: str = Query(None)
):
    """
    Real-time open orders updates
    Requires authentication token
    """
    if not _validate_ws_token(token):
        await websocket.close(code=4001, reason="Authentication required")
        return

    await manager.connect(websocket)

    try:
        while True:
            try:
                # Fetch open orders
                orders = await trading_service.get_open_orders()
                await websocket.send_json({
                    "type": "orders",
                    "data": orders or [],
                    "count": len(orders) if orders else 0,
                    "timestamp": asyncio.get_event_loop().time()
                })
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": str(e)
                })

            # Update every 5 seconds
            await asyncio.sleep(5)

    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/stream")
async def websocket_stream(
    websocket: WebSocket,
    token: str = Query(None)
):
    """
    Combined stream for all data types
    Client sends: {"subscribe": ["ticker:BTCUSDT", "account", "positions", "orders"]}
    """
    await manager.connect(websocket)
    subscriptions: Set[str] = set()
    is_authenticated = _validate_ws_token(token)

    async def stream_data():
        while True:
            try:
                tasks = []

                # Ticker subscriptions
                ticker_symbols = [s.split(":")[1] for s in subscriptions if s.startswith("ticker:")]
                for symbol in ticker_symbols:
                    ticker = await trading_service.get_ticker(symbol)
                    if ticker:
                        await websocket.send_json({
                            "type": "ticker",
                            "symbol": symbol,
                            "data": {
                                "lastPrice": ticker.get("lastPrice"),
                                "priceChangePercent": ticker.get("priceChangePercent"),
                            }
                        })

                # Account subscription
                if "account" in subscriptions and is_authenticated:
                    account = await trading_service.get_account_info()
                    if account:
                        await websocket.send_json({
                            "type": "account",
                            "data": {
                                "totalWalletBalance": account.get("totalWalletBalance"),
                                "totalUnrealizedProfit": account.get("totalUnrealizedProfit"),
                                "availableBalance": account.get("availableBalance"),
                            }
                        })

                # Positions subscription
                if "positions" in subscriptions and is_authenticated:
                    positions = await trading_service.get_positions()
                    if positions:
                        active = [p for p in positions if float(p.get("positionAmt", 0)) != 0]
                        await websocket.send_json({
                            "type": "positions",
                            "data": active,
                            "count": len(active)
                        })

                # Orders subscription
                if "orders" in subscriptions and is_authenticated:
                    orders = await trading_service.get_open_orders()
                    await websocket.send_json({
                        "type": "orders",
                        "data": orders or [],
                        "count": len(orders) if orders else 0
                    })

                await asyncio.sleep(2)
            except Exception:
                break

    stream_task = None

    try:
        while True:
            data = await websocket.receive_json()

            if "subscribe" in data:
                items = data["subscribe"]
                if isinstance(items, list):
                    subscriptions.update(items)

                    if stream_task is None or stream_task.done():
                        stream_task = asyncio.create_task(stream_data())

                    await websocket.send_json({
                        "type": "subscribed",
                        "items": list(subscriptions)
                    })

            elif "unsubscribe" in data:
                items = data["unsubscribe"]
                if isinstance(items, list):
                    for item in items:
                        subscriptions.discard(item)
                    await websocket.send_json({
                        "type": "unsubscribed",
                        "items": list(subscriptions)
                    })

            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        if stream_task:
            stream_task.cancel()
        manager.disconnect(websocket)
