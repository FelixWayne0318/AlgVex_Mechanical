"""
Sentiment Data Fetcher for NautilusTrader

Fetches market sentiment indicators from Binance Long/Short Ratio API.
(Replaced CryptoOracle with Binance due to invalid API key)
"""

import logging
import requests
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from utils.http_retry import api_retry


class SentimentDataFetcher:
    """
    Fetches BTC market sentiment data from Binance Futures API.

    Uses the global long/short account ratio as sentiment indicator.
    """

    # Binance Futures API (free, no API key required)
    BINANCE_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"

    def __init__(
        self,
        lookback_hours: int = 4,
        timeframe: str = "15m",
        timeout: float = 10.0,
    ):
        """
        Initialize sentiment data fetcher.

        Parameters
        ----------
        lookback_hours : int
            Not used for Binance API (kept for compatibility)
        timeframe : str
            Time interval for data: "5m", "15m", "30m", "1h", "4h", "1d"
        timeout : float, optional
            Request timeout (seconds), default: 10.0
        """
        self.lookback_hours = lookback_hours
        # Map timeframe to Binance period format
        self.timeframe = self._map_timeframe(timeframe)
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)

    def _map_timeframe(self, timeframe: str) -> str:
        """Map common timeframe formats to Binance period format."""
        mapping = {
            "1m": "5m",    # Binance minimum is 5m
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
        }
        return mapping.get(timeframe, "15m")

    def fetch(self, token: str = "BTC") -> Optional[Dict[str, Any]]:
        """
        Fetch sentiment data for specified token (with history).

        v3.24: Now fetches 10 data points for history series.

        Parameters
        ----------
        token : str
            Token symbol (default: "BTC")

        Returns
        -------
        Dict or None
            Sentiment data with structure:
            {
                'positive_ratio': float,  # Long ratio (bullish)
                'negative_ratio': float,  # Short ratio (bearish)
                'net_sentiment': float,   # Long - Short
                'data_time': str,
                'data_delay_minutes': int,
                'history': [              # v3.24: Historical data points
                    {'long': float, 'short': float, 'ratio': float, 'timestamp': int},
                    ...
                ]
            }
        """
        try:
            # Input validation: ensure token is a valid string
            if not isinstance(token, str) or not token.isalnum() or len(token) > 10:
                self.logger.warning(f"⚠️ Invalid token: {token}")
                return None

            data = self._fetch_with_retry(token)
            if data and len(data) > 0:
                # Binance returns data in ascending order (oldest first, newest last)
                result = self._parse_binance_data(data[-1])
                if result:
                    # v3.24: Build history series (oldest → newest)
                    history = []
                    for item in data:
                        try:
                            history.append({
                                'long': float(item.get('longAccount', 0.5)),
                                'short': float(item.get('shortAccount', 0.5)),
                                'ratio': float(item.get('longShortRatio', 1.0)),
                                'timestamp': item.get('timestamp', 0),
                            })
                        except (ValueError, TypeError) as e:
                            self.logger.debug(f"Using default value, original error: {e}")
                            continue
                    result['history'] = history
                return result

            self.logger.warning(f"⚠️ Binance API returned empty data for {token}")
            return None

        except Exception as e:
            self.logger.warning(f"❌ Sentiment data fetch failed after retries: {e}")
            return None

    @api_retry
    def _fetch_with_retry(self, token: str):
        params = {
            "symbol": f"{token.upper()}USDT",
            "period": self.timeframe,
            "limit": 10,
        }
        response = requests.get(self.BINANCE_URL, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _parse_binance_data(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse sentiment data from Binance API response."""
        try:
            # Extract long/short ratios with safe access
            long_account = data.get("longAccount")
            short_account = data.get("shortAccount")
            timestamp_ms = data.get("timestamp")
            long_short_ratio = data.get("longShortRatio")

            # Validate required fields
            if long_account is None or short_account is None or timestamp_ms is None:
                self.logger.warning(f"⚠️ Binance API response missing required fields: {data.keys()}")
                return None

            long_ratio = float(long_account)
            short_ratio = float(short_account)

            # v7.1: Validate ratio bounds — Binance longAccount/shortAccount
            # should be in [0, 1] and sum to ~1.0. Reject anomalous values
            # that could mislead AI decision-making.
            if not (0.0 <= long_ratio <= 1.0) or not (0.0 <= short_ratio <= 1.0):
                self.logger.warning(f"⚠️ Sentiment ratios out of bounds: long={long_ratio}, short={short_ratio}")
                return None
            if abs(long_ratio + short_ratio - 1.0) > 0.05:
                self.logger.warning(f"⚠️ Sentiment ratios don't sum to ~1.0: long={long_ratio} + short={short_ratio} = {long_ratio + short_ratio}")
                return None

            net_sentiment = long_ratio - short_ratio

            # Parse timestamp (Binance returns UTC timestamp)
            data_time_utc = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

            # Calculate delay using UTC for consistency
            now_utc = datetime.now(timezone.utc)
            data_delay = int((now_utc - data_time_utc).total_seconds() // 60)

            self.logger.debug(f"✅ Using Binance sentiment data from: {data_time_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC (delay: {data_delay} minutes)")

            return {
                'positive_ratio': long_ratio,
                'negative_ratio': short_ratio,
                'net_sentiment': net_sentiment,
                'data_time': data_time_utc.strftime('%Y-%m-%d %H:%M:%S UTC'),
                'data_delay_minutes': data_delay,
                'source': 'binance',
                'long_short_ratio': float(long_short_ratio) if long_short_ratio is not None else 0.0
            }

        except Exception as e:
            self.logger.warning(f"❌ Sentiment data parsing failed: {e}")
            return None

    def format_for_display(self, sentiment_data: Optional[Dict[str, Any]]) -> str:
        """
        Format sentiment data for logging/display.

        Parameters
        ----------
        sentiment_data : Dict or None
            Sentiment data from fetch()

        Returns
        -------
        str
            Formatted sentiment string
        """
        if not sentiment_data:
            return "Market Sentiment: Data unavailable"

        net = sentiment_data.get('net_sentiment', 0)
        sign = '+' if net >= 0 else ''
        return (
            f"Market Sentiment (Binance): "
            f"Long {sentiment_data.get('positive_ratio', 0.5):.1%} | "
            f"Short {sentiment_data.get('negative_ratio', 0.5):.1%} | "
            f"Net {sign}{net:.3f} | "
            f"Ratio {sentiment_data.get('long_short_ratio', 0):.2f}"
        )
