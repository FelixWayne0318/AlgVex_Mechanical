# utils/coinalyze_client.py

import requests
import time
import logging
from typing import Optional, Dict, Any
import os

from utils.http_retry import api_retry


class CoinalyzeClient:
    """
    Coinalyze API 客户端 (同步版本)

    获取衍生品数据: OI, 清算, 多空比

    设计原则:
    - 同步调用，兼容 on_timer() 回调
    - 参考 sentiment_client.py 的错误处理模式
    - 支持指数退避重试
    """

    BASE_URL = "https://api.coinalyze.net/v1"
    DEFAULT_SYMBOL = "BTCUSDT_PERP.A"

    def __init__(
        self,
        api_key: str = None,
        timeout: int = 10,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        logger: logging.Logger = None,
    ):
        """
        初始化 Coinalyze 客户端

        Parameters
        ----------
        api_key : str
            API Key (从 ~/.env.algvex 的 COINALYZE_API_KEY 读取)
        timeout : int
            请求超时 (秒)
        max_retries : int
            最大重试次数
        retry_delay : float
            重试基础延迟 (秒)，使用指数退避
        logger : Logger
            日志记录器
        """
        self.api_key = api_key or os.getenv("COINALYZE_API_KEY")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.logger = logger or logging.getLogger(__name__)
        self._enabled = bool(self.api_key)

        if not self._enabled:
            self.logger.warning("⚠️ COINALYZE_API_KEY not set, Coinalyze client disabled")

    def _get_headers(self) -> Dict[str, str]:
        """构建请求头"""
        return {"api_key": self.api_key} if self.api_key else {}

    @api_retry
    def _request_with_retry(
        self,
        endpoint: str,
        params: Dict[str, Any],
    ) -> Optional[Dict]:
        """
        带重试的 HTTP 请求

        Decorated with @api_retry for automatic retry on transient network
        errors (Timeout, ConnectionError). The 429 rate limit logic is handled
        internally and preserved — @api_retry does not interfere with it.

        Parameters
        ----------
        endpoint : str
            API 端点 (如 "/open-interest")
        params : Dict
            查询参数

        Returns
        -------
        Optional[Dict]
            API 响应，失败返回 None
        """
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_headers()

        for attempt in range(self.max_retries + 1):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )

                if response.status_code == 200:
                    data = response.json()
                    return data[0] if data else None

                elif response.status_code == 429:
                    self.logger.warning("⚠️ Coinalyze rate limit reached (429)")
                    # 速率限制时等待更长时间
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay * (2 ** attempt) * 2)
                        continue
                    return None

                else:
                    self.logger.warning(
                        f"⚠️ Coinalyze API error: {response.status_code}"
                    )
                    return None

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                self.logger.warning(
                    f"⚠️ Coinalyze network error (attempt {attempt + 1}/{self.max_retries + 1})"
                )
                # On last inner attempt, let the exception propagate for @api_retry
                if attempt >= self.max_retries:
                    raise
            except requests.exceptions.RequestException as e:
                self.logger.warning(
                    f"⚠️ Coinalyze request error (attempt {attempt + 1}): {e}"
                )
                return None

            # 指数退避
            if attempt < self.max_retries:
                time.sleep(self.retry_delay * (2 ** attempt))

        return None

    def get_open_interest(self, symbol: str = None) -> Optional[Dict]:
        """
        获取当前 Open Interest

        Returns:
            {
                "symbol": "BTCUSDT_PERP.A",
                "value": 102199.59,       # BTC 数量 (非 USD!)
                "update": 1769417410150   # 毫秒时间戳
            }

        注意: value 是 BTC 数量，需要乘以当前价格转换为 USD
        """
        if not self._enabled:
            return None

        symbol = symbol or self.DEFAULT_SYMBOL
        return self._request_with_retry(
            endpoint="/open-interest",
            params={"symbols": symbol},
        )

    def get_liquidations(
        self,
        symbol: str = None,
        interval: str = "1hour",
        hours: int = 24,
    ) -> Optional[Dict]:
        """
        获取清算历史

        v3.24: 从 1h 扩展到 24h，提供完整趋势

        Args:
            symbol: 交易对 (默认 BTCUSDT_PERP.A)
            interval: 1hour, 4hour, daily 等
            hours: 回溯小时数 (默认 24)

        Returns:
            {
                "symbol": "...",
                "history": [
                    {"t": 1769418000, "l": 0.002, "s": 0.028}
                ]
            }

        注意:
        - t 是秒时间戳 (10位)
        - l = long liquidations (BTC 单位，需乘以价格转换为 USD)
        - s = short liquidations (BTC 单位，需乘以价格转换为 USD)
        - 例: l=0.002, 当前价格=$88000 → Long Liq = $176
        """
        if not self._enabled:
            return None

        symbol = symbol or self.DEFAULT_SYMBOL
        return self._request_with_retry(
            endpoint="/liquidation-history",
            params={
                "symbols": symbol,
                "interval": interval,
                "from": int(time.time()) - (hours * 3600),
                "to": int(time.time()),
            },
        )

    def fetch_all(self, symbol: str = None) -> Dict[str, Any]:
        """
        一次性获取所有衍生品数据 (便捷方法)

        Returns:
            {
                "open_interest": {...} or None,
                "liquidations": {...} or None,
                "enabled": bool,
            }
        """
        if not self._enabled:
            return {
                "open_interest": None,
                "liquidations": None,
                "enabled": False,
            }

        oi = self.get_open_interest(symbol)
        liq = self.get_liquidations(symbol)

        # 🔍 Fix B8: Add data quality marker if any data is missing
        missing_count = sum([oi is None, liq is None])
        data_quality = "COMPLETE" if missing_count == 0 else "PARTIAL" if missing_count < 2 else "MISSING"

        return {
            "open_interest": oi,
            "liquidations": liq,
            "enabled": True,
            "_data_quality": data_quality,  # Fix B8: Quality marker
            "_missing_fields": [
                field for field, value in [("OI", oi), ("Liq", liq)]
                if value is None
            ],
        }

    def is_enabled(self) -> bool:
        """检查客户端是否启用"""
        return self._enabled

    # =========================================================================
    # 历史数据 API (新增)
    # =========================================================================

    def get_open_interest_history(
        self,
        symbol: str = None,
        interval: str = "1hour",
        hours: int = 4,
    ) -> Optional[Dict]:
        """
        获取 OI 历史数据 (OHLC 格式)

        Parameters
        ----------
        symbol : str
            交易对 (默认 BTCUSDT_PERP.A)
        interval : str
            时间周期 (1hour, 4hour, daily)
        hours : int
            回溯小时数

        Returns
        -------
        Dict or None
            {
                "symbol": "BTCUSDT_PERP.A",
                "history": [
                    {"t": 1769832000, "o": 101991.489, "h": 102006.154, "l": 101927.816, "c": 101936.021},
                    ...
                ]
            }

        注意:
        - t = 时间戳 (秒)
        - o/h/l/c = OI 的 开/高/低/收 (BTC 单位)
        """
        if not self._enabled:
            return None

        symbol = symbol or self.DEFAULT_SYMBOL
        now = int(time.time())

        return self._request_with_retry(
            endpoint="/open-interest-history",
            params={
                "symbols": symbol,
                "interval": interval,
                "from": now - (hours * 3600),
                "to": now,
            },
        )

    def get_long_short_ratio_history(
        self,
        symbol: str = None,
        interval: str = "1hour",
        hours: int = 4,
    ) -> Optional[Dict]:
        """
        获取多空比历史数据

        Parameters
        ----------
        symbol : str
            交易对
        interval : str
            时间周期
        hours : int
            回溯小时数

        Returns
        -------
        Dict or None
            {
                "symbol": "BTCUSDT_PERP.A",
                "history": [
                    {"t": 1769832000, "r": 2.413, "l": 70.7, "s": 29.3},
                    ...
                ]
            }

        字段说明:
        - t = 时间戳 (秒)
        - r = 多空比 (Long/Short ratio)
        - l = 多头占比 (%)
        - s = 空头占比 (%)
        """
        if not self._enabled:
            return None

        symbol = symbol or self.DEFAULT_SYMBOL
        now = int(time.time())

        return self._request_with_retry(
            endpoint="/long-short-ratio-history",
            params={
                "symbols": symbol,
                "interval": interval,
                "from": now - (hours * 3600),
                "to": now,
            },
        )

    def fetch_all_with_history(
        self,
        symbol: str = None,
        history_hours: int = 4,
    ) -> Dict[str, Any]:
        """
        获取所有数据 (包含历史数据)

        Parameters
        ----------
        symbol : str
            交易对
        history_hours : int
            历史数据回溯小时数

        Returns
        -------
        Dict
            {
                "open_interest": {...},
                "liquidations": {...},
                "open_interest_history": {...},
                "long_short_ratio_history": {...},
                "trends": {
                    "oi_trend": "RISING" / "FALLING" / "STABLE",
                    "long_short_trend": "RISING" / "FALLING" / "STABLE",
                },
                "enabled": True,
            }
        """
        if not self._enabled:
            return {
                "open_interest": None,
                "liquidations": None,
                "open_interest_history": None,
                "long_short_ratio_history": None,
                "trends": {},
                "enabled": False,
            }

        # 获取当前值
        oi = self.get_open_interest(symbol)
        liq = self.get_liquidations(symbol)

        oi_hist = self.get_open_interest_history(symbol, hours=history_hours)
        ls_hist = self.get_long_short_ratio_history(symbol, hours=history_hours)

        trends = {
            "oi_trend": self._calc_trend_from_history(oi_hist, "c"),
            "long_short_trend": self._calc_trend_from_history(ls_hist, "r"),
        }

        return {
            "open_interest": oi,
            "liquidations": liq,
            "open_interest_history": oi_hist,
            "long_short_ratio_history": ls_hist,
            "trends": trends,
            "enabled": True,
        }

    def _calc_trend_from_history(
        self,
        data: Optional[Dict],
        value_key: str,
    ) -> Optional[str]:
        """
        从历史数据计算趋势

        Parameters
        ----------
        data : Dict
            包含 history 数组的数据
        value_key : str
            要分析的字段名 (如 "c", "r")

        Returns
        -------
        str or None
            "RISING" / "FALLING" / "STABLE" / None
        """
        if not data or "history" not in data:
            return None

        history = data.get("history", [])
        if len(history) < 2:
            return None

        try:
            # history 按时间升序，[-1] 是最新的
            oldest = float(history[0].get(value_key, 0))
            newest = float(history[-1].get(value_key, 0))

            if oldest == 0:
                return None

            change_pct = (newest - oldest) / oldest * 100

            if change_pct > 3:
                return "RISING"
            elif change_pct < -3:
                return "FALLING"
            else:
                return "STABLE"
        except (ValueError, TypeError, KeyError):
            return None

    def format_for_ai(self, data: Dict[str, Any], current_price: float = 0.0) -> str:
        """
        格式化数据供 AI 分析

        Parameters
        ----------
        data : Dict
            fetch_all_with_history() 返回的数据
        current_price : float
            当前 BTC 价格 (用于 BTC → USD 转换)

        Returns
        -------
        str
            格式化的文本描述
        """
        parts = ["COINALYZE DERIVATIVES DATA:"]

        # OI
        oi = data.get("open_interest")
        if oi:
            oi_btc = float(oi.get("value", 0))
            oi_usd = oi_btc * current_price if current_price > 0 else 0
            oi_trend = data.get("trends", {}).get("oi_trend", "N/A")
            parts.append(f"- Open Interest: {oi_btc:,.0f} BTC (${oi_usd:,.0f}) [Trend: {oi_trend}]")

        # Liquidations
        liq = data.get("liquidations")
        if liq:
            history = liq.get("history", [])
            if history:
                total_long = sum(float(h.get("l", 0)) for h in history)
                total_short = sum(float(h.get("s", 0)) for h in history)
                total_usd = (total_long + total_short) * current_price if current_price > 0 else 0
                parts.append(
                    f"- Liquidations (24h): Long {total_long:.4f} BTC, Short {total_short:.4f} BTC "
                    f"(Total: ${total_usd:,.0f})"
                )

        # Long/Short Ratio (from history)
        ls_hist = data.get("long_short_ratio_history")
        if ls_hist and ls_hist.get("history"):
            latest = ls_hist["history"][-1]
            ratio = float(latest.get("r", 1))
            long_pct = float(latest.get("l", 50))
            short_pct = float(latest.get("s", 50))
            ls_trend = data.get("trends", {}).get("long_short_trend", "N/A")
            parts.append(
                f"- Long/Short Ratio: {ratio:.2f} (Long {long_pct:.1f}% / Short {short_pct:.1f}%) "
                f"[Trend: {ls_trend}]"
            )

        return "\n".join(parts) if len(parts) > 1 else "COINALYZE: No data available"
