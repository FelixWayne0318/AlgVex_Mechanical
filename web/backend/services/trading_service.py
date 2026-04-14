"""
Trading Service - Fetch real-time trading data from Binance Futures
"""
import hmac
import hashlib
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import httpx
from core.config import settings, load_algvex_env


class TradingService:
    """Service for fetching real-time trading data from Binance Futures API"""

    BASE_URL = "https://fapi.binance.com"
    PUBLIC_URL = "https://api.binance.com"

    def __init__(self):
        load_algvex_env()
        self.api_key = settings.BINANCE_API_KEY
        self.api_secret = settings.BINANCE_API_SECRET
        self._client: Optional[httpx.AsyncClient] = None

    @asynccontextmanager
    async def _get_client(self):
        """Get or create a shared httpx client with connection pooling.
        Used as async context manager to match existing code pattern
        without closing the client on each request."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        yield self._client

    def _sign(self, params: dict) -> dict:
        """Sign request with HMAC SHA256"""
        if not self.api_secret:
            raise ValueError("Binance API secret not configured")

        params["timestamp"] = int(time.time() * 1000)
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        signature = hmac.new(
            self.api_secret.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    def _headers(self) -> dict:
        """Get request headers"""
        return {"X-MBX-APIKEY": self.api_key or ""}

    # =========================================================================
    # Account & Balance
    # =========================================================================
    async def get_account_info(self) -> Optional[Dict]:
        """Get futures account information with all balances"""
        try:
            params = self._sign({})
            async with self._get_client() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/fapi/v2/account",
                    params=params,
                    headers=self._headers(),
                    timeout=10.0
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "total_balance": float(data.get("totalWalletBalance", 0)),
                        "available_balance": float(data.get("availableBalance", 0)),
                        "total_unrealized_pnl": float(data.get("totalUnrealizedProfit", 0)),
                        "total_margin_balance": float(data.get("totalMarginBalance", 0)),
                        "total_position_initial_margin": float(data.get("totalPositionInitialMargin", 0)),
                        "total_open_order_initial_margin": float(data.get("totalOpenOrderInitialMargin", 0)),
                        "assets": [
                            {
                                "asset": a["asset"],
                                "wallet_balance": float(a["walletBalance"]),
                                "unrealized_pnl": float(a["unrealizedProfit"]),
                                "margin_balance": float(a["marginBalance"]),
                                "available_balance": float(a["availableBalance"]),
                            }
                            for a in data.get("assets", [])
                            if float(a.get("walletBalance", 0)) > 0
                        ],
                        "update_time": data.get("updateTime"),
                    }
        except Exception as e:
            print(f"Error fetching account info: {e}")
        return None

    # =========================================================================
    # Positions
    # =========================================================================
    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get all open positions"""
        try:
            params = self._sign({})
            async with self._get_client() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/fapi/v2/positionRisk",
                    params=params,
                    headers=self._headers(),
                    timeout=10.0
                )
                if resp.status_code == 200:
                    positions = resp.json()
                    result = []
                    for p in positions:
                        pos_amt = float(p.get("positionAmt", 0))
                        if pos_amt == 0:
                            continue
                        if symbol and p.get("symbol") != symbol:
                            continue

                        entry_price = float(p.get("entryPrice", 0))
                        mark_price = float(p.get("markPrice", 0))
                        unrealized_pnl = float(p.get("unRealizedProfit", 0))

                        # Calculate ROE
                        notional = abs(pos_amt) * entry_price
                        leverage = int(p.get("leverage", 1))
                        margin = notional / leverage if leverage > 0 else notional
                        roe = (unrealized_pnl / margin * 100) if margin > 0 else 0

                        result.append({
                            "symbol": p.get("symbol"),
                            "side": "LONG" if pos_amt > 0 else "SHORT",
                            "size": abs(pos_amt),
                            "entry_price": entry_price,
                            "mark_price": mark_price,
                            "liquidation_price": float(p.get("liquidationPrice", 0)),
                            "unrealized_pnl": unrealized_pnl,
                            "roe_percent": round(roe, 2),
                            "leverage": leverage,
                            "margin_type": p.get("marginType"),
                            "notional": round(abs(pos_amt) * mark_price, 2),
                        })
                    return result
        except Exception as e:
            print(f"Error fetching positions: {e}")
        return []

    # =========================================================================
    # Orders
    # =========================================================================
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get all open orders"""
        try:
            params = {}
            if symbol:
                params["symbol"] = symbol
            params = self._sign(params)

            async with self._get_client() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/fapi/v1/openOrders",
                    params=params,
                    headers=self._headers(),
                    timeout=10.0
                )
                if resp.status_code == 200:
                    orders = resp.json()
                    return [
                        {
                            "order_id": o.get("orderId"),
                            "client_order_id": o.get("clientOrderId"),
                            "symbol": o.get("symbol"),
                            "side": o.get("side"),
                            "type": o.get("type"),
                            "price": float(o.get("price", 0)),
                            "stop_price": float(o.get("stopPrice", 0)),
                            "quantity": float(o.get("origQty", 0)),
                            "executed_qty": float(o.get("executedQty", 0)),
                            "status": o.get("status"),
                            "time_in_force": o.get("timeInForce"),
                            "reduce_only": o.get("reduceOnly", False),
                            "close_position": o.get("closePosition", False),
                            "working_type": o.get("workingType"),
                            "created_time": datetime.fromtimestamp(
                                o.get("time", 0) / 1000
                            ).isoformat() if o.get("time") else None,
                        }
                        for o in orders
                    ]
        except Exception as e:
            print(f"Error fetching open orders: {e}")
        return []

    async def get_order_history(
        self,
        symbol: str = "BTCUSDT",
        days: int = 7,
        limit: int = 100
    ) -> List[Dict]:
        """Get order history"""
        try:
            start_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
            params = self._sign({
                "symbol": symbol,
                "startTime": start_time,
                "limit": limit,
            })

            async with self._get_client() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/fapi/v1/allOrders",
                    params=params,
                    headers=self._headers(),
                    timeout=30.0
                )
                if resp.status_code == 200:
                    orders = resp.json()
                    return [
                        {
                            "order_id": o.get("orderId"),
                            "symbol": o.get("symbol"),
                            "side": o.get("side"),
                            "type": o.get("type"),
                            "price": float(o.get("price", 0)),
                            "avg_price": float(o.get("avgPrice", 0)),
                            "quantity": float(o.get("origQty", 0)),
                            "executed_qty": float(o.get("executedQty", 0)),
                            "status": o.get("status"),
                            "time_in_force": o.get("timeInForce"),
                            "reduce_only": o.get("reduceOnly", False),
                            "created_time": datetime.fromtimestamp(
                                o.get("time", 0) / 1000
                            ).isoformat() if o.get("time") else None,
                            "updated_time": datetime.fromtimestamp(
                                o.get("updateTime", 0) / 1000
                            ).isoformat() if o.get("updateTime") else None,
                        }
                        for o in orders
                    ]
        except Exception as e:
            print(f"Error fetching order history: {e}")
        return []

    # =========================================================================
    # Trade History
    # =========================================================================
    async def get_trade_history(
        self,
        symbol: str = "BTCUSDT",
        days: int = 7,
        limit: int = 100
    ) -> List[Dict]:
        """Get trade/fill history"""
        try:
            start_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
            params = self._sign({
                "symbol": symbol,
                "startTime": start_time,
                "limit": limit,
            })

            async with self._get_client() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/fapi/v1/userTrades",
                    params=params,
                    headers=self._headers(),
                    timeout=30.0
                )
                if resp.status_code == 200:
                    trades = resp.json()
                    return [
                        {
                            "trade_id": t.get("id"),
                            "order_id": t.get("orderId"),
                            "symbol": t.get("symbol"),
                            "side": t.get("side"),
                            "price": float(t.get("price", 0)),
                            "quantity": float(t.get("qty", 0)),
                            "quote_qty": float(t.get("quoteQty", 0)),
                            "commission": float(t.get("commission", 0)),
                            "commission_asset": t.get("commissionAsset"),
                            "realized_pnl": float(t.get("realizedPnl", 0)),
                            "side_effect_type": t.get("positionSide"),
                            "maker": t.get("maker", False),
                            "buyer": t.get("buyer", False),
                            "time": datetime.fromtimestamp(
                                t.get("time", 0) / 1000
                            ).isoformat() if t.get("time") else None,
                        }
                        for t in trades
                    ]
        except Exception as e:
            print(f"Error fetching trade history: {e}")
        return []

    # =========================================================================
    # Income History (PnL, Funding, Commission)
    # =========================================================================
    async def get_income_history(
        self,
        income_type: Optional[str] = None,
        days: int = 30,
        limit: int = 1000
    ) -> List[Dict]:
        """
        Get income history

        income_type options:
        - REALIZED_PNL: Trading profit/loss
        - FUNDING_FEE: Funding fees
        - COMMISSION: Trading fees
        - TRANSFER: Transfers
        - None: All types
        """
        try:
            start_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
            params = {
                "startTime": start_time,
                "limit": limit,
            }
            if income_type:
                params["incomeType"] = income_type
            params = self._sign(params)

            async with self._get_client() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/fapi/v1/income",
                    params=params,
                    headers=self._headers(),
                    timeout=30.0
                )
                if resp.status_code == 200:
                    income = resp.json()
                    return [
                        {
                            "symbol": i.get("symbol"),
                            "income_type": i.get("incomeType"),
                            "income": float(i.get("income", 0)),
                            "asset": i.get("asset"),
                            "info": i.get("info"),
                            "time": datetime.fromtimestamp(
                                i.get("time", 0) / 1000
                            ).isoformat() if i.get("time") else None,
                            "tran_id": i.get("tranId"),
                            "trade_id": i.get("tradeId"),
                        }
                        for i in income
                    ]
        except Exception as e:
            print(f"Error fetching income history: {e}")
        return []

    # =========================================================================
    # Market Data (Public)
    # =========================================================================
    async def get_ticker(self, symbol: str = "BTCUSDT") -> Optional[Dict]:
        """Get 24hr ticker for a symbol"""
        try:
            async with self._get_client() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/fapi/v1/ticker/24hr",
                    params={"symbol": symbol},
                    timeout=10.0
                )
                if resp.status_code == 200:
                    t = resp.json()
                    return {
                        "symbol": t.get("symbol"),
                        "price": float(t.get("lastPrice", 0)),
                        "price_change": float(t.get("priceChange", 0)),
                        "price_change_percent": float(t.get("priceChangePercent", 0)),
                        "high_24h": float(t.get("highPrice", 0)),
                        "low_24h": float(t.get("lowPrice", 0)),
                        "volume_24h": float(t.get("volume", 0)),
                        "quote_volume_24h": float(t.get("quoteVolume", 0)),
                        "open_price": float(t.get("openPrice", 0)),
                        "close_time": t.get("closeTime"),
                    }
        except Exception as e:
            print(f"Error fetching ticker: {e}")
        return None

    async def get_mark_price(self, symbol: str = "BTCUSDT") -> Optional[Dict]:
        """Get mark price and funding rate"""
        try:
            async with self._get_client() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/fapi/v1/premiumIndex",
                    params={"symbol": symbol},
                    timeout=10.0
                )
                if resp.status_code == 200:
                    m = resp.json()
                    return {
                        "symbol": m.get("symbol"),
                        "mark_price": float(m.get("markPrice", 0)),
                        "index_price": float(m.get("indexPrice", 0)),
                        "funding_rate": float(m.get("lastFundingRate", 0)),
                        "next_funding_time": m.get("nextFundingTime"),
                        "interest_rate": float(m.get("interestRate", 0)),
                    }
        except Exception as e:
            print(f"Error fetching mark price: {e}")
        return None

    async def get_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "30m",
        limit: int = 100
    ) -> List[Dict]:
        """Get kline/candlestick data"""
        try:
            async with self._get_client() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/fapi/v1/klines",
                    params={
                        "symbol": symbol,
                        "interval": interval,
                        "limit": limit,
                    },
                    timeout=10.0
                )
                if resp.status_code == 200:
                    klines = resp.json()
                    # v6.5: Binance klines API always returns the current
                    # (incomplete) candle as the last element.  Strip it so
                    # the frontend only receives completed bars.
                    if isinstance(klines, list) and len(klines) > 1:
                        klines = klines[:-1]
                    return [
                        {
                            "open_time": k[0],
                            "open": float(k[1]),
                            "high": float(k[2]),
                            "low": float(k[3]),
                            "close": float(k[4]),
                            "volume": float(k[5]),
                            "close_time": k[6],
                            "quote_volume": float(k[7]),
                            "trades": int(k[8]),
                            "taker_buy_volume": float(k[9]),
                            "taker_buy_quote_volume": float(k[10]),
                        }
                        for k in klines
                    ]
        except Exception as e:
            print(f"Error fetching klines: {e}")
        return []

    async def get_order_book(
        self,
        symbol: str = "BTCUSDT",
        limit: int = 20
    ) -> Optional[Dict]:
        """Get order book depth"""
        try:
            async with self._get_client() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/fapi/v1/depth",
                    params={"symbol": symbol, "limit": limit},
                    timeout=10.0
                )
                if resp.status_code == 200:
                    d = resp.json()
                    return {
                        "last_update_id": d.get("lastUpdateId"),
                        "bids": [[float(b[0]), float(b[1])] for b in d.get("bids", [])],
                        "asks": [[float(a[0]), float(a[1])] for a in d.get("asks", [])],
                        "timestamp": d.get("T"),
                    }
        except Exception as e:
            print(f"Error fetching order book: {e}")
        return None

    async def get_open_interest(self, symbol: str = "BTCUSDT") -> Optional[Dict]:
        """Get open interest for a symbol"""
        try:
            async with self._get_client() as client:
                # Get current open interest
                resp = await client.get(
                    f"{self.BASE_URL}/fapi/v1/openInterest",
                    params={"symbol": symbol},
                    timeout=10.0
                )
                if resp.status_code != 200:
                    return None

                oi_data = resp.json()
                current_oi = float(oi_data.get("openInterest", 0))

                # Get open interest history for 24h change
                hist_resp = await client.get(
                    f"{self.BASE_URL}/futures/data/openInterestHist",
                    params={
                        "symbol": symbol,
                        "period": "1h",
                        "limit": 25,  # ~24 hours
                    },
                    timeout=10.0
                )

                change_24h = 0.0
                oi_value_usd = 0.0

                if hist_resp.status_code == 200:
                    hist_data = hist_resp.json()
                    if len(hist_data) >= 2:
                        # Get 24h ago value (or earliest available)
                        old_oi = float(hist_data[0].get("sumOpenInterest", 0))
                        new_oi = float(hist_data[-1].get("sumOpenInterest", 0))
                        oi_value_usd = float(hist_data[-1].get("sumOpenInterestValue", 0))
                        if old_oi > 0:
                            change_24h = ((new_oi - old_oi) / old_oi) * 100

                return {
                    "symbol": symbol,
                    "open_interest": current_oi,
                    "value": oi_value_usd,
                    "change_24h": round(change_24h, 2),
                    "timestamp": oi_data.get("time"),
                }
        except Exception as e:
            print(f"Error fetching open interest: {e}")
        return None

    async def get_long_short_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "30m",
        limit: int = 10
    ) -> List[Dict]:
        """Get long/short account ratio"""
        try:
            async with self._get_client() as client:
                resp = await client.get(
                    f"{self.BASE_URL}/futures/data/globalLongShortAccountRatio",
                    params={
                        "symbol": symbol,
                        "period": period,
                        "limit": limit,
                    },
                    timeout=10.0
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return [
                        {
                            "symbol": d.get("symbol"),
                            "long_account": float(d.get("longAccount", 0)),
                            "short_account": float(d.get("shortAccount", 0)),
                            "long_short_ratio": float(d.get("longShortRatio", 0)),
                            "timestamp": d.get("timestamp"),
                        }
                        for d in data
                    ]
        except Exception as e:
            print(f"Error fetching long/short ratio: {e}")
        return []



# Singleton instance
trading_service = TradingService()
