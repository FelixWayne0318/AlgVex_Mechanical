# utils/binance_derivatives_client.py

"""
Binance 衍生品数据客户端

获取 Binance 独有的衍生品数据:
- 大户多空账户比 (topLongShortAccountRatio)
- 大户多空持仓比 (topLongShortPositionRatio)
- Taker 买卖比 (takerlongshortRatio)
- OI 历史 (openInterestHist)
- 资金费率历史 (fundingRate)
- 24h 行情统计 (ticker/24hr)

这些数据是 Coinalyze 没有的，可提供额外的市场洞察。
"""

import requests
import logging
from typing import Optional, Dict, Any, List

from utils.http_retry import api_retry


class BinanceDerivativesClient:
    """
    Binance 衍生品数据客户端

    提供 Binance Futures API 的衍生品数据获取功能。
    无需 API Key，使用公开数据端点。
    """

    BASE_URL = "https://fapi.binance.com"

    def __init__(
        self,
        timeout: int = 10,
        logger: logging.Logger = None,
        config: dict = None,
    ):
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)
        self.config = config or {}
        # v3.7: 读取趋势计算阈值配置
        trend_config = self.config.get('binance_derivatives', {}).get('trend_calculation', {})
        self.trend_threshold_pct = trend_config.get('threshold_pct', 5.0)

    def _request(self, endpoint: str, params: dict) -> Optional[Any]:
        """通用请求方法 (with tenacity retry for transient errors)"""
        try:
            return self._request_with_retry(endpoint, params)
        except Exception as e:
            self.logger.warning(f"⚠️ Binance API request error after retries: {e}")
            return None

    @api_retry
    def _request_with_retry(self, endpoint: str, params: dict) -> Any:
        url = f"{self.BASE_URL}{endpoint}"
        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    # =========================================================================
    # 大户数据 (Top Trader Data) - Binance 独有
    # =========================================================================

    def get_top_long_short_account_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "15m",
        limit: int = 10,
    ) -> Optional[List[Dict]]:
        """
        获取大户多空账户比

        大户 = 前 20% 持仓量的账户

        Parameters
        ----------
        symbol : str
            交易对
        period : str
            周期 (5m/15m/30m/1h/2h/4h/6h/12h/1d)
        limit : int
            数量 (最大 500)

        Returns
        -------
        List[Dict]
            [
                {
                    "symbol": "BTCUSDT",
                    "longShortRatio": "1.234",   # 多/空比
                    "longAccount": "0.5525",     # 多头账户占比
                    "shortAccount": "0.4475",   # 空头账户占比
                    "timestamp": 1234567890000
                },
                ...
            ]
        """
        return self._request(
            "/futures/data/topLongShortAccountRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    def get_top_long_short_position_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "15m",
        limit: int = 10,
    ) -> Optional[List[Dict]]:
        """
        获取大户多空持仓比 (更有价值)

        大户 = 前 20% 持仓量的账户
        此指标反映大户的实际仓位分布，比账户比更有参考价值

        Returns
        -------
        List[Dict]
            [
                {
                    "symbol": "BTCUSDT",
                    "longShortRatio": "1.567",   # 多/空持仓比
                    "longAccount": "0.6100",     # 多头持仓占比
                    "shortAccount": "0.3900",   # 空头持仓占比
                    "timestamp": 1234567890000
                },
                ...
            ]
        """
        return self._request(
            "/futures/data/topLongShortPositionRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    # =========================================================================
    # Taker 买卖比 - Binance 独有
    # =========================================================================

    def get_taker_long_short_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "15m",
        limit: int = 10,
    ) -> Optional[List[Dict]]:
        """
        获取 Taker 多空比 (主动买卖力量对比)

        Taker = 主动吃单方，反映即时交易意愿

        Returns
        -------
        List[Dict]
            [
                {
                    "buySellRatio": "1.234",     # 买/卖比
                    "buyVol": "12345.67",        # 主动买入量
                    "sellVol": "10000.00",       # 主动卖出量
                    "timestamp": 1234567890000
                },
                ...
            ]
        """
        return self._request(
            "/futures/data/takerlongshortRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    # =========================================================================
    # OI 历史
    # =========================================================================

    def get_open_interest_hist(
        self,
        symbol: str = "BTCUSDT",
        period: str = "15m",
        limit: int = 10,
    ) -> Optional[List[Dict]]:
        """
        获取 OI 历史数据

        Returns
        -------
        List[Dict]
            [
                {
                    "symbol": "BTCUSDT",
                    "sumOpenInterest": "12345.67",       # OI (BTC)
                    "sumOpenInterestValue": "1234567890", # OI (USD)
                    "timestamp": 1234567890000
                },
                ...
            ]
        """
        return self._request(
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    # =========================================================================
    # 资金费率历史
    # =========================================================================

    def get_funding_rate_history(
        self,
        symbol: str = "BTCUSDT",
        limit: int = 10,
    ) -> Optional[List[Dict]]:
        """
        获取资金费率历史

        注意: 资金费率每 8 小时结算一次

        Returns
        -------
        List[Dict]
            [
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": 1234567890000,
                    "fundingRate": "0.00010000",  # 0.01%
                    "markPrice": "50000.00"
                },
                ...
            ]
        """
        return self._request(
            "/fapi/v1/fundingRate",
            {"symbol": symbol, "limit": limit},
        )

    # =========================================================================
    # 24h 行情统计
    # =========================================================================

    def get_ticker_24hr(
        self,
        symbol: str = "BTCUSDT",
    ) -> Optional[Dict]:
        """
        获取 24h 行情统计

        Returns
        -------
        Dict
            {
                "symbol": "BTCUSDT",
                "priceChange": "100.00",
                "priceChangePercent": "0.20",
                "weightedAvgPrice": "50000.00",
                "lastPrice": "50100.00",
                "volume": "12345.67",           # 24h 成交量 (BTC)
                "quoteVolume": "617283950.00",  # 24h 成交额 (USDT)
                "openPrice": "50000.00",
                "highPrice": "51000.00",
                "lowPrice": "49000.00",
                "count": 123456                 # 24h 成交笔数
            }
        """
        return self._request(
            "/fapi/v1/ticker/24hr",
            {"symbol": symbol},
        )

    # =========================================================================
    # 深度数据
    # =========================================================================

    def get_depth(
        self,
        symbol: str = "BTCUSDT",
        limit: int = 20,
    ) -> Optional[Dict]:
        """
        获取深度数据 (买卖盘口)

        Parameters
        ----------
        limit : int
            档位数 (5/10/20/50/100/500/1000)

        Returns
        -------
        Dict
            {
                "lastUpdateId": 123456789,
                "bids": [["50000.00", "1.234"], ...],  # 买盘 [价格, 数量]
                "asks": [["50001.00", "0.567"], ...]   # 卖盘 [价格, 数量]
            }
        """
        return self._request(
            "/fapi/v1/depth",
            {"symbol": symbol, "limit": limit},
        )

    # =========================================================================
    # 一次性获取所有数据
    # =========================================================================

    def fetch_all(
        self,
        symbol: str = "BTCUSDT",
        period: str = "15m",
        history_limit: int = 10,
    ) -> Dict[str, Any]:
        """
        一次性获取所有 Binance 衍生品数据

        Parameters
        ----------
        symbol : str
            交易对
        period : str
            周期
        history_limit : int
            历史数据条数

        Returns
        -------
        Dict
            完整的衍生品数据字典
        """
        # 并行请求会更快，但为了简单起见这里串行
        top_account = self.get_top_long_short_account_ratio(symbol, period, history_limit)
        top_position = self.get_top_long_short_position_ratio(symbol, period, history_limit)
        taker_ratio = self.get_taker_long_short_ratio(symbol, period, history_limit)
        oi_hist = self.get_open_interest_hist(symbol, period, history_limit)
        funding_hist = self.get_funding_rate_history(symbol, history_limit)
        ticker = self.get_ticker_24hr(symbol)

        # 计算趋势
        top_position_trend = self._calc_trend(top_position, "longShortRatio")
        taker_trend = self._calc_trend(taker_ratio, "buySellRatio")
        oi_trend = self._calc_trend(oi_hist, "sumOpenInterestValue")

        return {
            "top_long_short_account": {
                "data": top_account,
                "latest": top_account[0] if top_account else None,
            },
            "top_long_short_position": {
                "data": top_position,
                "latest": top_position[0] if top_position else None,
                "trend": top_position_trend,
            },
            "taker_long_short": {
                "data": taker_ratio,
                "latest": taker_ratio[0] if taker_ratio else None,
                "trend": taker_trend,
            },
            "open_interest_hist": {
                "data": oi_hist,
                "latest": oi_hist[0] if oi_hist else None,
                "trend": oi_trend,
            },
            "funding_rate_hist": {
                "data": funding_hist,
                "latest": funding_hist[0] if funding_hist else None,
            },
            "ticker_24hr": ticker,
            "_metadata": {
                "symbol": symbol,
                "period": period,
                "history_limit": history_limit,
            },
        }

    def _calc_trend(
        self,
        data: Optional[List[Dict]],
        key: str,
    ) -> Optional[str]:
        """
        计算趋势方向

        Returns
        -------
        str or None
            "RISING" / "FALLING" / "STABLE" / None
        """
        if not data or len(data) < 2:
            return None

        try:
            # data[0] 是最新的, data[-1] 是最旧的
            newest = float(data[0].get(key, 0))
            oldest = float(data[-1].get(key, 0))

            if oldest == 0:
                return None

            change_pct = (newest - oldest) / oldest * 100

            # v3.7: 使用配置化阈值
            threshold = self.trend_threshold_pct
            if change_pct > threshold:
                return "RISING"
            elif change_pct < -threshold:
                return "FALLING"
            else:
                return "STABLE"
        except (ValueError, TypeError) as e:
            self.logger.debug(f"Using default value, original error: {e}")
            return None

    def format_for_ai(self, data: Dict[str, Any]) -> str:
        """
        格式化数据供 AI 分析

        Parameters
        ----------
        data : Dict
            fetch_all() 返回的数据

        Returns
        -------
        str
            格式化的文本描述
        """
        parts = ["BINANCE DERIVATIVES DATA (Unique to Binance):"]

        # 大户持仓比
        top_pos = data.get("top_long_short_position", {})
        latest = top_pos.get("latest")
        if latest:
            ratio = float(latest.get("longShortRatio", 1))
            long_pct = float(latest.get("longAccount", 0.5)) * 100
            short_pct = float(latest.get("shortAccount", 0.5)) * 100
            trend = top_pos.get("trend", "N/A")
            parts.append(
                f"- Top Traders Position: Long {long_pct:.1f}% / Short {short_pct:.1f}% "
                f"(Ratio: {ratio:.2f}, Trend: {trend})"
            )

        # Taker 买卖比
        taker = data.get("taker_long_short", {})
        latest = taker.get("latest")
        if latest:
            ratio = float(latest.get("buySellRatio", 1))
            trend = taker.get("trend", "N/A")
            parts.append(f"- Taker Buy/Sell Ratio: {ratio:.3f} (Trend: {trend})")

        # OI 趋势
        oi = data.get("open_interest_hist", {})
        latest = oi.get("latest")
        if latest:
            oi_usd = float(latest.get("sumOpenInterestValue", 0))
            trend = oi.get("trend", "N/A")
            parts.append(f"- Open Interest: ${oi_usd:,.0f} (Trend: {trend})")

        # 24h 统计
        ticker = data.get("ticker_24hr")
        if ticker:
            change_pct = float(ticker.get("priceChangePercent", 0))
            volume = float(ticker.get("quoteVolume", 0))
            high = float(ticker.get("highPrice", 0))
            low = float(ticker.get("lowPrice", 0))
            parts.append(
                f"- 24h Stats: Change {change_pct:+.2f}%, "
                f"Volume ${volume:,.0f}, Range ${low:,.0f}-${high:,.0f}"
            )

        return "\n".join(parts) if len(parts) > 1 else "BINANCE DERIVATIVES: No data available"
