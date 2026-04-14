"""
Binance Account Utilities

Provides real-time account balance and position information from Binance Futures API.
"""

import os
import socket
import time
import hmac
import hashlib
import logging
import urllib.request
import urllib.error
import json
from typing import Dict, Any, Optional

from utils.http_retry import urllib_api_retry


class BinanceAccountFetcher:
    """
    Fetches real-time account information from Binance Futures API.

    This class provides methods to:
    - Get account balance (total wallet balance, available balance)
    - Get open positions with unrealized PnL
    - Get account leverage and margin info
    """

    BASE_URL = "https://fapi.binance.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        cache_ttl: float = 5.0,
        recv_window: int = 5000,
        api_timeout: float = 10.0,
    ):
        """
        Initialize Binance account fetcher.

        Parameters
        ----------
        api_key : str, optional
            Binance API key (defaults to BINANCE_API_KEY env var)
        api_secret : str, optional
            Binance API secret (defaults to BINANCE_API_SECRET env var)
        logger : logging.Logger, optional
            Logger instance
        cache_ttl : float, optional
            Cache time-to-live (seconds), default: 5.0
        recv_window : int, optional
            Binance API receive window (ms), default: 5000
        api_timeout : float, optional
            API request timeout (seconds), default: 10.0
        """
        self.api_key = api_key or os.getenv('BINANCE_API_KEY', '')
        self.api_secret = api_secret or os.getenv('BINANCE_API_SECRET', '')
        self.logger = logger or logging.getLogger(__name__)

        # Cache for rate limiting
        self._cache: Dict[str, Any] = {}
        self._cache_time: float = 0
        self._cache_ttl: float = cache_ttl

        # Binance API configuration
        self._recv_window: int = recv_window
        self._api_timeout: float = api_timeout

        # Binance server time offset (local_time + offset = binance_time)
        self._time_offset_ms: int = 0
        self._time_offset_synced: bool = False

    def _sync_server_time(self) -> bool:
        """
        Synchronize local clock with Binance server time.

        Calculates the offset between local time and Binance server time
        to prevent -1021 (Timestamp outside recvWindow) errors.

        Returns
        -------
        bool
            True if sync succeeded
        """
        try:
            url = f"{self.BASE_URL}/fapi/v1/time"
            req = urllib.request.Request(url, headers={
                "User-Agent": "AlgVex/1.0"
            })

            t_before = int(time.time() * 1000)
            response = urllib.request.urlopen(req, timeout=self._api_timeout)
            t_after = int(time.time() * 1000)
            data = json.loads(response.read())

            server_time = data.get('serverTime', 0)
            if server_time <= 0:
                self.logger.warning("Binance server time response invalid")
                return False

            # Use midpoint of request as local reference (accounts for network latency)
            local_time = (t_before + t_after) // 2
            self._time_offset_ms = server_time - local_time
            self._time_offset_synced = True
            self._time_offset_synced_at = time.time()

            if abs(self._time_offset_ms) > 1000:
                self.logger.warning(
                    f"Binance time offset: {self._time_offset_ms}ms "
                    f"(local clock is {'behind' if self._time_offset_ms > 0 else 'ahead'})"
                )
            else:
                self.logger.debug(f"Binance time offset: {self._time_offset_ms}ms")

            return True

        except Exception as e:
            self.logger.error(f"Failed to sync Binance server time: {e}")
            return False

    def _get_synced_timestamp(self) -> int:
        """Get current timestamp adjusted for Binance server time offset."""
        # Re-sync every 30 minutes (clock drift)
        if (not self._time_offset_synced or
                (time.time() - getattr(self, '_time_offset_synced_at', 0)) > 1800):
            self._sync_server_time()

        return int(time.time() * 1000) + self._time_offset_ms

    def _sign_request(self, params: Dict[str, Any]) -> str:
        """Create HMAC SHA256 signature for request."""
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        signature = hmac.new(
            self.api_secret.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature

    @urllib_api_retry
    def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Make authenticated request to Binance API with time sync.

        Decorated with @urllib_api_retry for automatic retry on transient
        network errors (URLError, socket.timeout, ConnectionError).
        HTTPError (4xx/5xx) is handled internally and not retried by the decorator.
        """
        if not self.api_key or not self.api_secret:
            self.logger.warning("Binance API credentials not configured")
            return None

        max_retries = 2  # retry once on -1021
        for attempt in range(max_retries):
            try:
                if params is None:
                    params = {}
                # Use synced timestamp instead of raw local time
                params['timestamp'] = self._get_synced_timestamp()
                params['recvWindow'] = self._recv_window

                # Sign request
                signature = self._sign_request(params)
                params['signature'] = signature

                # Build URL
                query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
                url = f"{self.BASE_URL}{endpoint}?{query_string}"

                # Make request
                req = urllib.request.Request(url, headers={
                    "X-MBX-APIKEY": self.api_key,
                    "User-Agent": "AlgVex/1.0"
                })

                response = urllib.request.urlopen(req, timeout=self._api_timeout)
                data = json.loads(response.read())
                return data

            except urllib.error.HTTPError as e:
                error_body = e.read().decode()

                # Handle -1021: Timestamp outside recvWindow
                if e.code == 400 and '-1021' in error_body:
                    if attempt < max_retries - 1:
                        self.logger.warning(
                            f"Binance -1021 timestamp error, re-syncing server time (attempt {attempt + 1})"
                        )
                        self._time_offset_synced = False
                        self._sync_server_time()
                        # Remove stale signature for retry
                        params.pop('signature', None)
                        params.pop('timestamp', None)
                        continue
                    else:
                        self.logger.error(
                            f"Binance -1021 timestamp error persists after re-sync. "
                            f"Offset: {self._time_offset_ms}ms"
                        )

                self.logger.error(f"Binance API HTTP error {e.code}: {error_body}")
                return None
            except (urllib.error.URLError, socket.timeout, ConnectionError):
                # Let transient network errors propagate for @urllib_api_retry
                raise
            except Exception as e:
                self.logger.error(f"Binance API request failed: {e}")
                return None

        return None

    @urllib_api_retry
    def _make_delete_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Make authenticated DELETE request to Binance API.

        Decorated with @urllib_api_retry for transient network error retries.
        """
        if not self.api_key or not self.api_secret:
            self.logger.warning("Binance API credentials not configured")
            return None

        max_retries = 2
        for attempt in range(max_retries):
            try:
                if params is None:
                    params = {}
                params['timestamp'] = self._get_synced_timestamp()
                params['recvWindow'] = self._recv_window

                signature = self._sign_request(params)
                params['signature'] = signature

                query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
                url = f"{self.BASE_URL}{endpoint}?{query_string}"

                req = urllib.request.Request(url, method='DELETE', headers={
                    "X-MBX-APIKEY": self.api_key,
                    "User-Agent": "AlgVex/1.0"
                })

                response = urllib.request.urlopen(req, timeout=self._api_timeout)
                data = json.loads(response.read())
                return data

            except urllib.error.HTTPError as e:
                error_body = e.read().decode()
                if e.code == 400 and '-1021' in error_body:
                    if attempt < max_retries - 1:
                        self._time_offset_synced = False
                        self._sync_server_time()
                        params.pop('signature', None)
                        params.pop('timestamp', None)
                        continue
                self.logger.error(f"Binance DELETE API HTTP error {e.code}: {error_body}")
                return None
            except (urllib.error.URLError, socket.timeout, ConnectionError):
                raise
            except Exception as e:
                self.logger.error(f"Binance DELETE API request failed: {e}")
                return None

        return None

    def cancel_all_open_orders(self, symbol: str) -> Dict[str, Any]:
        """
        取消指定交易对的所有挂单 (regular + Algo API).

        v15.4: 平仓后安全网 — 清理交易所上所有残留订单，防止条件委托单泄漏。

        Regular 订单: DELETE /fapi/v1/allOpenOrders
        Algo 订单: 逐个 DELETE /fapi/v1/algoOrder

        Parameters
        ----------
        symbol : str
            交易对 (e.g., 'BTCUSDT')

        Returns
        -------
        dict
            {'regular_cancelled': int, 'algo_cancelled': int, 'errors': list}
        """
        clean_symbol = symbol.replace('-PERP', '').replace('.BINANCE', '').upper()
        result = {'regular_cancelled': 0, 'algo_cancelled': 0, 'errors': []}

        # 1) Cancel all regular orders
        try:
            resp = self._make_delete_request("/fapi/v1/allOpenOrders", {'symbol': clean_symbol})
            if resp is not None:
                if isinstance(resp, dict) and resp.get('code') == 200:
                    result['regular_cancelled'] = -1  # API returns success but no count
                elif isinstance(resp, list):
                    result['regular_cancelled'] = len(resp)
                self.logger.info(f"Cancelled regular orders for {clean_symbol}: {resp}")
        except Exception as e:
            result['errors'].append(f"regular: {e}")
            self.logger.warning(f"Failed to cancel regular orders for {clean_symbol}: {e}")

        # 2) Cancel Algo API orders
        try:
            algo_orders = self.get_open_algo_orders(clean_symbol)
            for algo in algo_orders:
                algo_id = algo.get('algoId')
                algo_status = algo.get('algoStatus', algo.get('status', ''))
                if not algo_id or algo_status not in ('', 'WORKING', 'NEW'):
                    continue
                try:
                    cancel_resp = self._make_delete_request("/fapi/v1/algoOrder", {'algoId': algo_id})
                    if cancel_resp is not None:
                        result['algo_cancelled'] += 1
                        self.logger.info(f"Cancelled Algo order {algo_id} for {clean_symbol}")
                except Exception as e:
                    result['errors'].append(f"algo {algo_id}: {e}")
        except Exception as e:
            result['errors'].append(f"algo_list: {e}")
            self.logger.warning(f"Failed to list/cancel Algo orders for {clean_symbol}: {e}")

        return result

    def get_account_info(self, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """
        Get full account information from Binance Futures.

        Returns
        -------
        dict or None
            Account info including balances, positions, etc.
        """
        # Check cache
        if use_cache and self._cache and (time.time() - self._cache_time) < self._cache_ttl:
            return self._cache

        data = self._make_request("/fapi/v2/account")

        if data:
            self._cache = data
            self._cache_time = time.time()

        return data

    def get_balance(self) -> Dict[str, float]:
        """
        Get account balance information.

        Returns
        -------
        dict
            Balance info with keys:
            - total_balance: Total wallet balance (USDT)
            - available_balance: Available for trading (USDT)
            - unrealized_pnl: Total unrealized PnL (USDT)
            - margin_balance: Margin balance (USDT)
        """
        account = self.get_account_info()

        if not account:
            return {
                'total_balance': 0.0,
                'available_balance': 0.0,
                'unrealized_pnl': 0.0,
                'margin_balance': 0.0,
                'error': 'Failed to fetch account info'
            }

        return {
            'total_balance': float(account.get('totalWalletBalance', 0)),
            'available_balance': float(account.get('availableBalance', 0)),
            'unrealized_pnl': float(account.get('totalUnrealizedProfit', 0)),
            'margin_balance': float(account.get('totalMarginBalance', 0)),
        }

    def get_positions(self, symbol: Optional[str] = None) -> Optional[list]:
        """
        Get open positions.

        Parameters
        ----------
        symbol : str, optional
            Filter by symbol (e.g., 'BTCUSDT')

        Returns
        -------
        list or None
            List of position dicts with non-zero amounts.
            Returns None if the API call failed (caller should use fallback).
            Returns [] if the API succeeded but no positions exist.
        """
        account = self.get_account_info()

        if not account:
            return None  # API failure — distinguish from "no positions"

        positions = account.get('positions', [])

        # Filter non-zero positions
        active_positions = [
            p for p in positions
            if float(p.get('positionAmt', 0)) != 0
        ]

        # Filter by symbol if specified
        if symbol:
            # Remove -PERP suffix if present
            clean_symbol = symbol.replace('-PERP', '').replace('.BINANCE', '')
            active_positions = [
                p for p in active_positions
                if p.get('symbol', '').upper() == clean_symbol.upper()
            ]

        return active_positions

    @urllib_api_retry
    def _make_post_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Make authenticated POST request to Binance API.

        Decorated with @urllib_api_retry for transient network error retries.
        """
        if not self.api_key or not self.api_secret:
            self.logger.warning("Binance API credentials not configured")
            return None

        max_retries = 2  # retry once on -1021 (same pattern as _make_request)
        for attempt in range(max_retries):
            try:
                if params is None:
                    params = {}
                params['timestamp'] = self._get_synced_timestamp()
                params['recvWindow'] = self._recv_window

                signature = self._sign_request(params)
                params['signature'] = signature

                query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
                url = f"{self.BASE_URL}{endpoint}"

                req = urllib.request.Request(
                    url,
                    data=query_string.encode(),
                    headers={
                        "X-MBX-APIKEY": self.api_key,
                        "User-Agent": "AlgVex/1.0",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    method='POST',
                )

                response = urllib.request.urlopen(req, timeout=self._api_timeout)
                data = json.loads(response.read())
                return data

            except urllib.error.HTTPError as e:
                error_body = e.read().decode()

                # Handle -1021: Timestamp outside recvWindow
                if e.code == 400 and '-1021' in error_body:
                    if attempt < max_retries - 1:
                        self.logger.warning(
                            f"Binance -1021 timestamp error on POST, re-syncing server time (attempt {attempt + 1})"
                        )
                        self._time_offset_synced = False
                        self._sync_server_time()
                        params.pop('signature', None)
                        params.pop('timestamp', None)
                        continue
                    else:
                        self.logger.error(
                            f"Binance -1021 timestamp error persists after re-sync. "
                            f"Offset: {self._time_offset_ms}ms"
                        )

                self.logger.error(f"Binance API POST error {e.code}: {error_body}")
                return None
            except (urllib.error.URLError, socket.timeout, ConnectionError):
                raise
            except Exception as e:
                self.logger.error(f"Binance API POST request failed: {e}")
                return None

        return None

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set leverage for a symbol on Binance Futures.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g., 'BTCUSDT' or 'BTCUSDT-PERP.BINANCE')
        leverage : int
            Target leverage multiplier (1-125)

        Returns
        -------
        bool
            True if leverage was set successfully
        """
        clean_symbol = symbol.replace('-PERP', '').replace('.BINANCE', '').upper()
        result = self._make_post_request('/fapi/v1/leverage', {
            'symbol': clean_symbol,
            'leverage': leverage,
        })
        if result and result.get('leverage') == leverage:
            self.logger.info(f"✅ Binance leverage set to {leverage}x for {clean_symbol}")
            return True
        self.logger.warning(f"Failed to set leverage to {leverage}x for {clean_symbol}: {result}")
        return False

    def get_leverage(self, symbol: str) -> Optional[int]:
        """
        Get the actual leverage setting for a symbol from Binance.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g., 'BTCUSDT' or 'BTCUSDT-PERP.BINANCE')

        Returns
        -------
        Optional[int]
            Leverage multiplier (e.g., 10 for 10x leverage)
            Returns None if unable to fetch (caller must handle gracefully)
        """
        account = self.get_account_info()
        if not account:
            self.logger.warning("Cannot fetch leverage: account info unavailable")
            return None

        # Clean symbol format
        clean_symbol = symbol.replace('-PERP', '').replace('.BINANCE', '').upper()

        # Find position info for this symbol
        positions = account.get('positions', [])
        for pos in positions:
            if pos.get('symbol', '').upper() == clean_symbol:
                leverage = int(pos.get('leverage', 1))
                self.logger.debug(f"Binance leverage for {clean_symbol}: {leverage}x")
                return leverage

        self.logger.warning(f"Symbol {clean_symbol} not found in account positions")
        return None

    def get_position_summary(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Get a summary of position information.

        Returns
        -------
        dict
            Position summary with balance and position info
        """
        balance = self.get_balance()
        positions = self.get_positions(symbol) or []

        return {
            'balance': balance,
            'positions': positions,
            'positions_count': len(positions),
            'has_position': len(positions) > 0,
        }

    def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """
        获取当前挂单列表 (用于恢复 SL/TP 状态).

        Parameters
        ----------
        symbol : str, optional
            交易对 (e.g., 'BTCUSDT')，不指定则返回所有挂单

        Returns
        -------
        list
            挂单列表，每个订单包含:
            - orderId, symbol, type, side, price, stopPrice, origQty, status
        """
        params = {}
        if symbol:
            clean_symbol = symbol.replace('-PERP', '').replace('.BINANCE', '').upper()
            params['symbol'] = clean_symbol

        data = self._make_request("/fapi/v1/openOrders", params)

        if data is None:
            return []

        return data

    def get_open_algo_orders(self, symbol: Optional[str] = None) -> list:
        """
        获取 Algo API 挂单 (STOP_MARKET, TAKE_PROFIT 等通过 Algo 端点提交的订单).

        v6.6 起 TP 使用 limit_if_touched() → Binance TAKE_PROFIT (Algo API),
        这些订单不在 /fapi/v1/openOrders 中，必须查 /fapi/v1/openAlgoOrders。

        Parameters
        ----------
        symbol : str, optional
            交易对 (e.g., 'BTCUSDT')

        Returns
        -------
        list
            Algo 挂单列表，字段包含:
            - algoId, symbol, orderType, side, triggerPrice, quantity, algoStatus
        """
        params = {}
        if symbol:
            clean_symbol = symbol.replace('-PERP', '').replace('.BINANCE', '').upper()
            params['symbol'] = clean_symbol

        data = self._make_request("/fapi/v1/openAlgoOrders", params)

        if data is None:
            return []

        # Algo API 返回格式可能是 {"orders": [...]} 或直接是 list
        if isinstance(data, dict):
            return data.get('orders', [])

        return data if isinstance(data, list) else []

    def get_sl_tp_from_orders(self, symbol: str, position_side: str) -> Dict[str, Optional[float]]:
        """
        从挂单中提取止损止盈价格 (查询 regular + Algo API 两个端点).

        服务器重启后，sltp_state 会丢失，但 Binance 上的挂单还在。
        此方法用于恢复 SL/TP 状态。

        v6.6 起 TP 通过 limit_if_touched() 提交 → Binance TAKE_PROFIT (Algo API),
        必须同时查询 /fapi/v1/openAlgoOrders 才能找到。

        Parameters
        ----------
        symbol : str
            交易对 (e.g., 'BTCUSDT')
        position_side : str
            持仓方向 ('long' 或 'short')

        Returns
        -------
        dict
            {'sl_price': float or None, 'tp_price': float or None}
        """
        sl_price = None
        tp_price = None
        is_long = position_side.lower() == 'long'

        # === 1) Regular orders: /fapi/v1/openOrders ===
        orders = self.get_open_orders(symbol)
        for order in orders:
            order_type = order.get('type', '').upper()
            order_side = order.get('side', '').upper()
            stop_price = float(order.get('stopPrice', 0))
            limit_price = float(order.get('price', 0))
            reduce_only = order.get('reduceOnly', False)

            if not reduce_only:
                continue

            if order_type in ['STOP_MARKET', 'STOP']:
                if is_long and order_side == 'SELL':
                    sl_price = stop_price
                elif not is_long and order_side == 'BUY':
                    sl_price = stop_price

            elif order_type in ['TAKE_PROFIT_MARKET', 'TAKE_PROFIT']:
                if is_long and order_side == 'SELL':
                    tp_price = stop_price
                elif not is_long and order_side == 'BUY':
                    tp_price = stop_price

            elif order_type == 'LIMIT' and reduce_only:
                if is_long and order_side == 'SELL' and limit_price > 0:
                    tp_price = limit_price
                elif not is_long and order_side == 'BUY' and limit_price > 0:
                    tp_price = limit_price

        # === 2) Algo API orders: /fapi/v1/openAlgoOrders ===
        # v6.6: TP via limit_if_touched() → TAKE_PROFIT on Algo API
        if sl_price is None or tp_price is None:
            try:
                algo_orders = self.get_open_algo_orders(symbol)
                for algo in algo_orders:
                    algo_status = algo.get('algoStatus', algo.get('status', ''))
                    if algo_status not in ('', 'WORKING', 'NEW'):
                        continue

                    order_type = algo.get('orderType', algo.get('algoType', '')).upper()
                    order_side = algo.get('side', '').upper()
                    trigger_price = float(algo.get('triggerPrice', algo.get('stopPrice', 0)))

                    if trigger_price <= 0:
                        continue

                    if sl_price is None and order_type in ['STOP_MARKET', 'STOP']:
                        if is_long and order_side == 'SELL':
                            sl_price = trigger_price
                        elif not is_long and order_side == 'BUY':
                            sl_price = trigger_price

                    if tp_price is None and order_type in ['TAKE_PROFIT_MARKET', 'TAKE_PROFIT']:
                        if is_long and order_side == 'SELL':
                            tp_price = trigger_price
                        elif not is_long and order_side == 'BUY':
                            tp_price = trigger_price
            except Exception as e:
                self.logger.debug(f"Algo API 查询失败 (非关键): {e}")

        self.logger.debug(f"从 Binance 挂单恢复 SL/TP: SL=${sl_price}, TP=${tp_price}")
        return {'sl_price': sl_price, 'tp_price': tp_price}

    def get_trades(self, symbol: str, limit: int = 10) -> list:
        """
        获取最近的交易记录。

        Parameters
        ----------
        symbol : str
            交易对 (e.g., 'BTCUSDT')
        limit : int, optional
            返回记录数量, 默认 10

        Returns
        -------
        list
            交易记录列表
        """
        # 清理 symbol 格式
        clean_symbol = symbol.replace('-PERP', '').replace('.BINANCE', '').upper()

        params = {
            'symbol': clean_symbol,
            'limit': limit,
        }

        data = self._make_request("/fapi/v1/userTrades", params)

        if data is None:
            return []

        return data

    def get_realtime_price(self, symbol: str) -> Optional[float]:
        """
        Get real-time mark price from Binance Futures API.

        This is the actual current price, not a cached bar close price.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g., 'BTCUSDT' or 'BTCUSDT-PERP.BINANCE')

        Returns
        -------
        float or None
            Current mark price
        """
        clean_symbol = symbol.replace('-PERP', '').replace('.BINANCE', '').upper()
        try:
            url = f"{self.BASE_URL}/fapi/v1/ticker/price?symbol={clean_symbol}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "AlgVex/1.0"
            })
            response = urllib.request.urlopen(req, timeout=self._api_timeout)
            data = json.loads(response.read())
            price = float(data.get('price', 0))
            return price if price > 0 else None
        except Exception as e:
            self.logger.debug(f"Failed to fetch realtime price for {clean_symbol}: {e}")
            return None

    def get_income_history(self, income_type: Optional[str] = None, limit: int = 20) -> list:
        """
        获取收益历史 (包含资金费率、盈亏等)。

        Parameters
        ----------
        income_type : str, optional
            收益类型: REALIZED_PNL, FUNDING_FEE, COMMISSION, etc.
        limit : int, optional
            返回记录数量, 默认 20

        Returns
        -------
        list
            收益记录列表
        """
        params = {'limit': limit}

        if income_type:
            params['incomeType'] = income_type

        data = self._make_request("/fapi/v1/income", params)

        if data is None:
            return []

        return data


# Singleton instance for convenience
_fetcher_instance: Optional[BinanceAccountFetcher] = None


def get_binance_fetcher(
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    cache_ttl: float = 5.0,
    recv_window: int = 5000,
    api_timeout: float = 10.0,
) -> BinanceAccountFetcher:
    """Get or create a BinanceAccountFetcher instance."""
    global _fetcher_instance

    if _fetcher_instance is None:
        _fetcher_instance = BinanceAccountFetcher(
            api_key=api_key,
            api_secret=api_secret,
            logger=logger,
            cache_ttl=cache_ttl,
            recv_window=recv_window,
            api_timeout=api_timeout,
        )

    return _fetcher_instance


def fetch_real_balance() -> Dict[str, float]:
    """
    Convenience function to fetch real account balance.

    Returns
    -------
    dict
        Balance info with total_balance, available_balance, etc.
    """
    fetcher = get_binance_fetcher()
    return fetcher.get_balance()
