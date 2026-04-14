"""
Market Data Module

Fetches market data from Binance Futures API.
"""

from datetime import datetime, timezone
from typing import Optional

import requests

from .base import (
    DiagnosticContext,
    DiagnosticStep,
    fetch_binance_klines,
)


class MarketDataFetcher(DiagnosticStep):
    """
    Fetch market data from Binance Futures.

    Gets K-line data and current price for the configured symbol.
    """

    name = "获取市场数据 (Binance Futures)"

    def run(self) -> bool:
        try:
            cfg = self.ctx.strategy_config
            symbol = self.ctx.symbol
            interval = self.ctx.interval
            # v15.1: Fetch 250 bars (was 100) to ensure SMA_200 is properly calculated.
            # Production has 1500+ bars from NautilusTrader prefetch.
            # Binance API max is 1500; 250 is sufficient for SMA_200 + warmup.
            limit = 250

            url = f"https://fapi.binance.com/fapi/v1/klines"
            params = {"symbol": symbol, "interval": interval, "limit": limit}
            response = requests.get(url, params=params, timeout=10)
            klines_raw = response.json()

            if isinstance(klines_raw, list) and len(klines_raw) > 0:
                # v6.4: Use last bar for current price, then drop it
                # Binance klines API always returns the current (incomplete) candle
                # as the last element. Feeding it to indicators causes volume artifacts
                # (e.g., 0.03x volume ratio when the bar just started).
                latest = klines_raw[-1]
                self.ctx.current_price = float(latest[4])
                self.ctx.klines_raw = klines_raw[:-1]  # Drop incomplete bar
                print(f"  交易对: {symbol}")
                print(f"  时间周期: {interval} (从 bar_type 解析)")
                print(f"  K线数量: {len(self.ctx.klines_raw)} (已排除当前未完成K线)")
                self.ctx.snapshot_timestamp = datetime.now().strftime('%H:%M:%S')
                print(f"  最新价格: ${self.ctx.current_price:,.2f} (快照时间: {self.ctx.snapshot_timestamp})")
                print("  ✅ 市场数据获取成功")
                return True
            else:
                self.ctx.add_error(f"K线数据异常: {klines_raw}")
                return False

        except requests.RequestException as e:
            self.ctx.add_error(f"获取市场数据失败: {e}")
            return False
        except (ValueError, KeyError) as e:
            self.ctx.add_error(f"解析市场数据失败: {e}")
            return False


class SentimentDataFetcher(DiagnosticStep):
    """
    Fetch sentiment data (Long/Short ratio) from Binance.

    Uses the same SentimentDataFetcher as production.
    """

    name = "获取情绪数据"

    def run(self) -> bool:
        try:
            from utils.sentiment_client import SentimentDataFetcher as SentimentClient

            cfg = self.ctx.strategy_config

            # Use same parameters as ai_strategy.py
            sentiment_fetcher = SentimentClient(
                lookback_hours=cfg.sentiment_lookback_hours,
                timeframe=cfg.sentiment_timeframe,
            )

            if not self.ctx.summary_mode:
                print(f"  lookback_hours: {cfg.sentiment_lookback_hours}")
                print(f"  timeframe: {cfg.sentiment_timeframe}")

            sentiment_data = sentiment_fetcher.fetch()

            if sentiment_data:
                self.ctx.sentiment_data = sentiment_data
                print(f"  Long/Short Ratio: {sentiment_data.get('long_short_ratio', 0):.4f}")
                print(f"  Long Account %: {sentiment_data.get('positive_ratio', 0)*100:.2f}%")
                print(f"  Short Account %: {sentiment_data.get('negative_ratio', 0)*100:.2f}%")
                print(f"  Source: {sentiment_data.get('source', 'N/A')}")
                print("  ✅ 情绪数据获取成功")
            else:
                # Fallback to neutral values (same as on_timer)
                self.ctx.sentiment_data = self._get_default_sentiment()
                print("  ⚠️ 使用中性默认值 (与 on_timer fallback 相同)")

            return True

        except Exception as e:
            self.ctx.add_warning(f"情绪数据获取失败: {e}")
            self.ctx.sentiment_data = self._get_default_sentiment()
            return True  # Non-critical

    def _get_default_sentiment(self) -> dict:
        """Get default neutral sentiment data.

        v18.0: Must include 'degraded': True to match production
        AIDataAssembler.fetch_external_data() fallback (ai_data_assembler.py L131-142).
        """
        return {
            'long_short_ratio': 1.0,
            'long_account_pct': 50.0,
            'short_account_pct': 50.0,
            'positive_ratio': 0.5,
            'negative_ratio': 0.5,
            'net_sentiment': 0.0,
            'source': 'default_neutral',
            'degraded': True,  # v18.0: match production fallback
            'timestamp': None,
        }


class PriceDataBuilder(DiagnosticStep):
    """
    Build price data structure for AI analysis.

    Same structure as used in on_timer.
    """

    name = "构建价格数据"

    def run(self) -> bool:
        try:
            kline_data = self.ctx.indicator_manager.get_kline_data(count=10)

            # Calculate price change
            bars = self.ctx.indicator_manager.recent_bars
            if len(bars) >= 2:
                price_change = (
                    (float(bars[-1].close) - float(bars[-2].close)) /
                    float(bars[-2].close)
                ) * 100
            else:
                price_change = 0.0

            # Calculate period statistics (v3.6)
            if bars and len(bars) >= 2:
                period_high = max(float(bar.high) for bar in bars)
                period_low = min(float(bar.low) for bar in bars)
                period_start_price = float(bars[0].open)
                period_change_pct = (
                    (self.ctx.current_price - period_start_price) /
                    period_start_price * 100
                ) if period_start_price > 0 else 0
                period_hours = len(bars) * 30 / 60  # 30-minute bars (v18.2 execution layer)
            else:
                period_high = self.ctx.current_price
                period_low = self.ctx.current_price
                period_change_pct = 0
                period_hours = 0

            latest_kline = self.ctx.klines_raw[-1]
            self.ctx.price_data = {
                'price': self.ctx.current_price,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'high': float(latest_kline[2]),
                'low': float(latest_kline[3]),
                'volume': float(latest_kline[5]),
                'price_change': price_change,
                'kline_data': kline_data,
                'period_high': period_high,
                'period_low': period_low,
                'period_change_pct': period_change_pct,
                'period_hours': round(period_hours, 1),
            }

            print(f"  Current Price: ${self.ctx.price_data['price']:,.2f}")
            print(f"  High: ${self.ctx.price_data['high']:,.2f}")
            print(f"  Low: ${self.ctx.price_data['low']:,.2f}")
            print(f"  Price Change: {self.ctx.price_data['price_change']:.2f}%")
            print(f"  Period High ({period_hours:.0f}h): ${period_high:,.2f}")
            print(f"  Period Low ({period_hours:.0f}h): ${period_low:,.2f}")
            print(f"  Period Change ({period_hours:.0f}h): {period_change_pct:+.2f}%")
            print(f"  K-line Count: {len(self.ctx.price_data['kline_data'])}")
            print("  ✅ 价格数据构建成功")

            return True

        except Exception as e:
            self.ctx.add_error(f"价格数据构建失败: {e}")
            import traceback
            traceback.print_exc()
            return False
