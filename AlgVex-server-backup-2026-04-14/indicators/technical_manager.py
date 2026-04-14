"""
Technical Indicator Manager for NautilusTrader Strategy

Manages all technical indicators using NautilusTrader's built-in indicators.
"""

from typing import Dict, Any, List

from utils.shared_logic import classify_extension_regime, classify_volatility_regime

# Use Cython indicators (not Rust PyO3) to avoid thread safety panics
# Reference: https://github.com/Patrick-code-Bot/nautilus_AItrader
# The original repo imports directly from nautilus_trader.indicators
from nautilus_trader.indicators import (
    SimpleMovingAverage,
    ExponentialMovingAverage,
    RelativeStrengthIndex,
    MovingAverageConvergenceDivergence,
    MovingAverageType,
)
from nautilus_trader.model.data import Bar


class TechnicalIndicatorManager:
    """
    Manages technical indicators for strategy analysis.

    Uses NautilusTrader's built-in indicators for efficiency and consistency.
    """

    def __init__(
        self,
        sma_periods: List[int] = [5, 20, 50],
        ema_periods: List[int] = [12, 26],
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        bb_period: int = 20,
        bb_std: float = 2.0,
        volume_ma_period: int = 20,
        support_resistance_lookback: int = 20,
    ):
        """
        Initialize technical indicator manager.

        Parameters
        ----------
        sma_periods : List[int]
            Periods for Simple Moving Averages
        ema_periods : List[int]
            Periods for Exponential Moving Averages
        rsi_period : int
            Period for RSI
        macd_fast : int
            Fast period for MACD
        macd_slow : int
            Slow period for MACD
        macd_signal : int
            Signal period for MACD
        bb_period : int
            Period for Bollinger Bands
        bb_std : float
            Standard deviation multiplier for Bollinger Bands
        volume_ma_period : int
            Period for volume moving average
        support_resistance_lookback : int
            Lookback period for support/resistance calculation
        """
        # SMA indicators
        self.smas = {period: SimpleMovingAverage(period) for period in sma_periods}

        # EMA indicators (for MACD calculation reference)
        self.emas = {period: ExponentialMovingAverage(period) for period in ema_periods}

        # RSI — use Wilder's smoothing (RMA, alpha=1/n) to match standard RSI (Wilder 1978)
        # Default EXPONENTIAL uses EMA (alpha=2/(n+1)) which diverges ~5 RSI units
        self.rsi = RelativeStrengthIndex(rsi_period, ma_type=MovingAverageType.WILDER)

        # MACD
        self.macd = MovingAverageConvergenceDivergence(
            fast_period=macd_fast,
            slow_period=macd_slow,
        )
        self.macd_signal = ExponentialMovingAverage(macd_signal)

        # For Bollinger Bands calculation
        self.bb_sma = SimpleMovingAverage(bb_period)
        self.bb_period = bb_period
        self.bb_std = bb_std

        # Volume MA
        self.volume_sma = SimpleMovingAverage(volume_ma_period)

        # Store recent bars for calculations
        self.recent_bars: List[Bar] = []
        # v5.5: Increased max_bars to support historical context calculations
        # ADX history needs 2*period + count + 1, SMA history needs max_sma + count
        history_count = 35  # Default count for get_historical_context()
        self.max_bars = max(
            max(list(sma_periods) + [bb_period, volume_ma_period, support_resistance_lookback]) + 10,
            max(sma_periods) + history_count + 10,  # SMA history (e.g. SMA50 + 35 + 10 = 95)
            2 * 14 + history_count + 10,            # ADX history (2*14 + 35 + 10 = 73)
        )

        # Configuration
        self.support_resistance_lookback = support_resistance_lookback
        self.sma_periods = sma_periods
        self.ema_periods = ema_periods
        self.rsi_period = rsi_period
        self.macd_slow_period = macd_slow
        self.macd_fast_period = macd_fast
        self.macd_signal_period = macd_signal

        # v5.5: History buffers — store official NT indicator values on each update()
        # Eliminates RSI/MACD series vs snapshot mismatch caused by simplified recalculation
        self._rsi_history: List[float] = []
        self._macd_history: List[float] = []
        self._macd_signal_history: List[float] = []

        # v20.0: OBV (On-Balance Volume) running accumulator
        self._obv_values: List[float] = []

    def update(self, bar: Bar):
        """
        Update all indicators with new bar data.

        Parameters
        ----------
        bar : Bar
            New bar data
        """
        # Store bar for manual calculations
        self.recent_bars.append(bar)
        if len(self.recent_bars) > self.max_bars:
            self.recent_bars.pop(0)

        # Update SMA indicators
        for sma in self.smas.values():
            sma.update_raw(float(bar.close))

        # Update EMA indicators
        for ema in self.emas.values():
            ema.update_raw(float(bar.close))

        # Update RSI
        self.rsi.update_raw(float(bar.close))

        # Update MACD
        self.macd.update_raw(float(bar.close))
        self.macd_signal.update_raw(self.macd.value)

        # Update Bollinger Band SMA
        self.bb_sma.update_raw(float(bar.close))

        # Update Volume SMA
        self.volume_sma.update_raw(float(bar.volume))

        # v20.0: Update OBV (On-Balance Volume)
        self._update_obv(bar)

        # v5.5: Store official NT indicator values for history series
        # These stored values are used by get_historical_context() to build
        # time-series data for AI. This replaces the old simplified recalculation
        # which used different algorithms and produced mismatched values.
        if self.rsi.initialized:
            self._rsi_history.append(round(self.rsi.value * 100, 2))
            if len(self._rsi_history) > self.max_bars:
                self._rsi_history.pop(0)
        if self.macd.initialized:
            self._macd_history.append(round(self.macd.value, 4))
            if len(self._macd_history) > self.max_bars:
                self._macd_history.pop(0)
        if self.macd_signal.initialized:
            self._macd_signal_history.append(round(self.macd_signal.value, 4))
            if len(self._macd_signal_history) > self.max_bars:
                self._macd_signal_history.pop(0)

    def get_technical_data(self, current_price: float) -> Dict[str, Any]:
        """
        Get all technical indicator values.

        Parameters
        ----------
        current_price : float
            Current market price

        Returns
        -------
        Dict
            Dictionary containing all technical indicator values
        """
        # Basic SMA values (use current_price fallback for uninitialized SMAs to avoid 0.0 bias)
        sma_values = {
            f'sma_{period}': self.smas[period].value if self.smas[period].initialized else current_price
            for period in self.sma_periods
        }

        # EMA values (use current_price fallback for uninitialized EMAs to avoid 0.0 bias)
        ema_values = {
            f'ema_{period}': self.emas[period].value if self.emas[period].initialized else current_price
            for period in self.ema_periods
        }

        # RSI (convert from 0-1 scale to 0-100 scale)
        rsi_value = self.rsi.value * 100

        # MACD
        macd_value = self.macd.value
        macd_signal_value = self.macd_signal.value  # Signal line from MACD indicator

        # Bollinger Bands
        bb_middle = self.bb_sma.value
        bb_std_dev = self._calculate_std_dev(self.bb_period)
        bb_upper = bb_middle + (self.bb_std * bb_std_dev)
        bb_lower = bb_middle - (self.bb_std * bb_std_dev)
        bb_position = (current_price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5

        # Volume analysis
        volume_ma = self.volume_sma.value
        current_volume = float(self.recent_bars[-1].volume) if self.recent_bars else 0
        volume_ratio = current_volume / volume_ma if volume_ma > 0 else 1.0

        # Support and Resistance
        support, resistance = self._calculate_support_resistance()

        # Trend analysis
        trend_data = self._analyze_trend(
            current_price, sma_values, macd_value, macd_signal_value
        )

        # ADX (trend strength)
        adx_data = self._calculate_adx(period=14)

        # ATR (Average True Range) - v6.5: for AI agents' SL/TP distance calculation
        atr_value = self._calculate_atr(period=14)

        # v19.1: ATR Extension Ratio — price displacement from SMA normalized by ATR
        # Measures how "stretched" price is from its mean in volatility units.
        # Positive = above SMA, negative = below SMA.
        # |ratio| > 3.0 = significantly overextended (mean-reversion pressure increases)
        # |ratio| > 5.0 = extremely overextended (high probability of snapback)
        extension_ratios = self._calculate_extension_ratios(current_price, atr_value)

        # v20.0: ATR Volatility Regime — percentile-based classification
        # Orthogonal to ADX (direction strength) and Extension Ratio (displacement).
        atr_regime = self._calculate_atr_regime(atr_value, current_price)

        # Combine all data
        technical_data = {
            # SMAs
            **sma_values,
            # EMAs
            **ema_values,
            # RSI
            "rsi": rsi_value,
            # MACD
            "macd": macd_value,
            "macd_signal": macd_signal_value,
            "macd_histogram": macd_value - macd_signal_value,
            # Bollinger Bands
            "bb_upper": bb_upper,
            "bb_middle": bb_middle,
            "bb_lower": bb_lower,
            "bb_position": bb_position,
            # Volume
            "volume_ratio": volume_ratio,
            # Support/Resistance
            "support": support,
            "resistance": resistance,
            # ADX (trend strength)
            "adx": adx_data['adx'],
            "di_plus": adx_data['di_plus'],
            "di_minus": adx_data['di_minus'],
            "adx_regime": adx_data['adx_regime'],
            "adx_direction": adx_data.get('adx_direction', ''),
            # ATR (volatility measure for SL/TP)
            "atr": atr_value,
            # v19.1: ATR Extension Ratios (price displacement / ATR)
            **extension_ratios,
            # v20.0: ATR Volatility Regime (percentile-based)
            **atr_regime,
            # Trend analysis
            **trend_data,
        }

        return technical_data

    def _calculate_std_dev(self, period: int) -> float:
        """Calculate standard deviation for Bollinger Bands."""
        if len(self.recent_bars) < period:
            return 0.0

        recent_closes = [float(bar.close) for bar in self.recent_bars[-period:]]
        mean = sum(recent_closes) / len(recent_closes)
        variance = sum((x - mean) ** 2 for x in recent_closes) / len(recent_closes)
        return variance ** 0.5

    def _calculate_support_resistance(self) -> tuple:
        """Calculate support and resistance levels."""
        if len(self.recent_bars) < self.support_resistance_lookback:
            return 0.0, 0.0

        recent = self.recent_bars[-self.support_resistance_lookback:]
        support = min(float(bar.low) for bar in recent)
        resistance = max(float(bar.high) for bar in recent)

        return support, resistance

    def _calculate_adx(self, period: int = 14) -> Dict[str, Any]:
        """
        Calculate ADX (Average Directional Index) from recent bars.

        ADX measures trend strength (not direction):
        - ADX < 20: No trend / ranging market
        - ADX 20-25: Weak/emerging trend
        - ADX 25-40: Strong trend
        - ADX > 40: Very strong trend

        Also returns +DI and -DI for trend direction:
        - +DI > -DI: Bullish trend
        - -DI > +DI: Bearish trend

        Parameters
        ----------
        period : int
            ADX smoothing period (default 14, Wilder's standard)

        Returns
        -------
        Dict with adx, di_plus, di_minus, adx_regime
        """
        bars = self.recent_bars
        n = len(bars)
        # Need at least 2 * period + 1 bars for meaningful ADX
        if n < 2 * period + 1:
            return {
                'adx': 0.0, 'di_plus': 0.0, 'di_minus': 0.0,
                'adx_regime': 'INSUFFICIENT_DATA',
            }

        # Step 1: Calculate TR, +DM, -DM for each bar
        tr_list = []
        plus_dm_list = []
        minus_dm_list = []

        for i in range(1, n):
            high = float(bars[i].high)
            low = float(bars[i].low)
            prev_close = float(bars[i - 1].close)
            prev_high = float(bars[i - 1].high)
            prev_low = float(bars[i - 1].low)

            # True Range
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

            # +DM and -DM
            up_move = high - prev_high
            down_move = prev_low - low

            if up_move > down_move and up_move > 0:
                plus_dm_list.append(up_move)
            else:
                plus_dm_list.append(0.0)

            if down_move > up_move and down_move > 0:
                minus_dm_list.append(down_move)
            else:
                minus_dm_list.append(0.0)

        if len(tr_list) < period:
            return {
                'adx': 0.0, 'di_plus': 0.0, 'di_minus': 0.0,
                'adx_regime': 'INSUFFICIENT_DATA',
            }

        # Step 2: Wilder's smoothing for TR, +DM, -DM
        # First value is simple sum of first 'period' values
        smoothed_tr = sum(tr_list[:period])
        smoothed_plus_dm = sum(plus_dm_list[:period])
        smoothed_minus_dm = sum(minus_dm_list[:period])

        dx_list = []

        for i in range(period, len(tr_list)):
            # Wilder's smoothing: prev - (prev / period) + current
            smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]
            smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
            smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]

            # +DI and -DI
            if smoothed_tr > 0:
                di_plus = (smoothed_plus_dm / smoothed_tr) * 100
                di_minus = (smoothed_minus_dm / smoothed_tr) * 100
            else:
                di_plus = 0.0
                di_minus = 0.0

            # DX
            di_sum = di_plus + di_minus
            if di_sum > 0:
                dx = abs(di_plus - di_minus) / di_sum * 100
            else:
                dx = 0.0
            dx_list.append(dx)

        if len(dx_list) < period:
            return {
                'adx': 0.0, 'di_plus': di_plus, 'di_minus': di_minus,
                'adx_regime': 'INSUFFICIENT_DATA',
            }

        # Step 3: ADX = smoothed DX (Wilder's method)
        adx = sum(dx_list[:period]) / period
        for i in range(period, len(dx_list)):
            adx = (adx * (period - 1) + dx_list[i]) / period

        # Determine regime
        if adx < 20:
            regime = 'RANGING'
        elif adx < 25:
            regime = 'WEAK_TREND'
        elif adx < 40:
            regime = 'STRONG_TREND'
        else:
            regime = 'VERY_STRONG_TREND'

        # Determine direction from DI
        if di_plus > di_minus:
            direction = 'BULLISH'
        else:
            direction = 'BEARISH'

        return {
            'adx': round(adx, 1),
            'di_plus': round(di_plus, 1),
            'di_minus': round(di_minus, 1),
            'adx_regime': regime,
            'adx_direction': direction,
        }

    def _analyze_trend(
        self,
        current_price: float,
        sma_values: Dict[str, float],
        macd_value: float,
        macd_signal_value: float,
    ) -> Dict[str, Any]:
        """
        Analyze market trend using multiple indicators.

        Returns
        -------
        Dict
            Trend analysis data
        """
        sma_20 = sma_values.get('sma_20', current_price)
        sma_50 = sma_values.get('sma_50', current_price)

        # Short-term trend (price vs SMA20)
        short_term_trend = "上涨" if current_price > sma_20 else "下跌"

        # Medium-term trend (price vs SMA50)
        medium_term_trend = "上涨" if current_price > sma_50 else "下跌"

        # MACD trend
        macd_trend = "bullish" if macd_value > macd_signal_value else "bearish"

        # Overall trend
        if short_term_trend == "上涨" and medium_term_trend == "上涨":
            overall_trend = "强势上涨"
        elif short_term_trend == "下跌" and medium_term_trend == "下跌":
            overall_trend = "强势下跌"
        else:
            overall_trend = "震荡整理"

        return {
            'short_term_trend': short_term_trend,
            'medium_term_trend': medium_term_trend,
            'macd_trend': macd_trend,
            'overall_trend': overall_trend,
        }

    def _calculate_atr(self, period: int = 14) -> float:
        """
        Calculate ATR (Average True Range) from recent bars.

        v6.5: Exposes ATR to AI agents for precise SL/TP distance calculation.
        Uses the same True Range formula as sr_zone_calculator._calculate_atr_from_bars().

        Parameters
        ----------
        period : int
            ATR period (default 14, Wilder's standard)

        Returns
        -------
        float
            ATR value in absolute price units (e.g., $350 for BTC).
            Returns 0.0 if insufficient data.
        """
        bars = self.recent_bars
        if len(bars) < period + 1:
            return 0.0

        true_ranges = []
        for i in range(1, len(bars)):
            high = float(bars[i].high)
            low = float(bars[i].low)
            prev_close = float(bars[i - 1].close)

            if high <= 0 or low <= 0 or prev_close <= 0:
                continue

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        if len(true_ranges) < period:
            return 0.0

        # Wilder's Smoothed Moving Average (RMA/SMMA) — standard ATR formula
        # Seed with SMA of the first `period` true ranges, then apply Wilder smoothing.
        # This matches TradingView/MetaTrader ATR(14) output.
        # Formula: ATR_t = (ATR_{t-1} × (period - 1) + TR_t) / period
        atr = sum(true_ranges[:period]) / period  # SMA seed
        for tr in true_ranges[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    def _calculate_extension_ratios(self, current_price: float, atr_value: float) -> Dict[str, Any]:
        """
        v19.1: Calculate ATR Extension Ratios for multiple SMAs.

        Extension Ratio = (Price - SMA) / ATR

        Measures price displacement from a moving average in ATR units.
        This is a volatility-normalized measure of how far price has
        "stretched" from its mean — independent of price level or
        absolute volatility, making it comparable across instruments
        and time periods.

        Interpretation:
        - |ratio| < 1.0: Price near SMA, normal range
        - |ratio| 1.0-2.0: Moderate extension
        - |ratio| 2.0-3.0: Significant extension
        - |ratio| > 3.0: Overextended (mean-reversion pressure)
        - |ratio| > 5.0: Extreme extension (high snapback probability)

        Returns
        -------
        Dict with extension_ratio_sma_{period} for each SMA, plus
        extension_regime (NORMAL/EXTENDED/OVEREXTENDED/EXTREME).
        """
        result = {}

        if atr_value <= 0 or current_price <= 0:
            for period in self.sma_periods:
                result[f'extension_ratio_sma_{period}'] = 0.0
            result['extension_regime'] = 'NORMAL'  # Safe neutral default (in SSoT enum)
            return result

        # Calculate extension ratio for each SMA period
        max_abs_ratio = 0.0
        for period in self.sma_periods:
            sma = self.smas.get(period)
            if sma and sma.initialized and sma.value > 0:
                ratio = (current_price - sma.value) / atr_value
                result[f'extension_ratio_sma_{period}'] = round(ratio, 2)
                max_abs_ratio = max(max_abs_ratio, abs(ratio))
            else:
                result[f'extension_ratio_sma_{period}'] = 0.0

        # Classify extension regime based on the most extended ratio (SMA_20 preferred)
        primary_ratio = abs(result.get('extension_ratio_sma_20', 0.0))
        if primary_ratio == 0.0:
            # Fallback to max across all SMAs
            primary_ratio = max_abs_ratio

        result['extension_regime'] = classify_extension_regime(primary_ratio)

        return result

    def _update_obv(self, bar: Bar) -> None:
        """v20.0: Incrementally update OBV value from latest bar."""
        if len(self.recent_bars) < 2:
            self._obv_values.append(0.0)
            return

        prev_obv = self._obv_values[-1] if self._obv_values else 0.0
        curr_close = float(bar.close)
        prev_close = float(self.recent_bars[-2].close)
        volume = float(bar.volume)

        if curr_close > prev_close:
            obv = prev_obv + volume
        elif curr_close < prev_close:
            obv = prev_obv - volume
        else:
            obv = prev_obv

        self._obv_values.append(obv)
        if len(self._obv_values) > self.max_bars:
            self._obv_values.pop(0)

    def _calculate_atr_regime(self, atr_value: float, current_price: float, lookback: int = 90) -> Dict[str, Any]:
        """
        v20.0: Classify current ATR into percentile-based volatility regime.

        Uses ATR% (ATR / Price × 100) for scale-independence, then ranks
        current ATR% against a rolling historical distribution.

        Orthogonal to ADX (trend direction strength) and Extension Ratio
        (price displacement from SMA).  Answers: "Is current volatility
        historically high or low?"

        Parameters
        ----------
        atr_value : float
            Current ATR absolute value
        current_price : float
            Current price for ATR% normalisation
        lookback : int
            Window size for historical percentile ranking (default 90 bars)

        Returns
        -------
        Dict with 'volatility_regime', 'volatility_percentile', 'atr_pct'
        """
        result: Dict[str, Any] = {
            'volatility_regime': 'INSUFFICIENT_DATA',
            'volatility_percentile': 0.0,
            'atr_pct': 0.0,
        }

        if atr_value <= 0 or current_price <= 0:
            return result

        current_atr_pct = (atr_value / current_price) * 100
        result['atr_pct'] = round(current_atr_pct, 4)

        bars = self.recent_bars
        period = 14  # Standard ATR period (matches _calculate_atr)
        min_bars_needed = period + lookback
        if len(bars) < min_bars_needed:
            # Not enough history — return NORMAL as safe default
            result['volatility_regime'] = 'NORMAL'
            result['volatility_percentile'] = 50.0
            return result

        # Build ATR% history for the last `lookback` bar positions
        # Use all available bars up to end_idx for Wilder's smoothing (not just `period` bars)
        # to match the production _calculate_atr() which processes the full bar series.
        atr_pct_history: List[float] = []
        for end_idx in range(len(bars) - lookback, len(bars)):
            if end_idx < period:
                continue

            trs: List[float] = []
            for i in range(1, end_idx + 1):
                h = float(bars[i].high)
                l = float(bars[i].low)
                pc = float(bars[i - 1].close)
                if h <= 0 or l <= 0 or pc <= 0:
                    continue
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))

            if len(trs) < period:
                continue

            # Wilder's smoothing (same formula as _calculate_atr)
            atr_local = sum(trs[:period]) / period
            for tr in trs[period:]:
                atr_local = (atr_local * (period - 1) + tr) / period

            close = float(bars[end_idx].close)
            if close > 0:
                atr_pct_history.append(atr_local / close * 100)

        if len(atr_pct_history) < 10:
            result['volatility_regime'] = 'NORMAL'
            result['volatility_percentile'] = 50.0
            return result

        # Percentile rank of current ATR% within historical distribution
        rank = sum(1 for x in atr_pct_history if x <= current_atr_pct)
        percentile = (rank / len(atr_pct_history)) * 100
        result['volatility_percentile'] = round(percentile, 1)
        result['volatility_regime'] = classify_volatility_regime(percentile)

        return result

    def is_initialized(self) -> bool:
        """Check if indicators have enough data to be valid."""
        # Check if we have minimum bars for key indicators
        # Use dynamic calculation based on actual indicator periods
        min_required_bars = max(
            self.rsi_period,  # RSI period (e.g., 7 or 14)
            self.macd_slow_period,  # MACD slow period (e.g., 10 or 26)
            self.bb_period,  # Bollinger Bands period (e.g., 10 or 20)
            min(self.sma_periods) if self.sma_periods else 0  # At least shortest SMA
        )
        
        if len(self.recent_bars) < min_required_bars:
            return False

        # Check if key indicators are initialized
        if not self.rsi.initialized:
            return False

        if not self.macd.initialized:
            return False

        # Check if we have at least one SMA initialized (for trend analysis)
        if not any(sma.initialized for sma in self.smas.values()):
            return False

        return True

    def get_kline_data(self, count: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent K-line data for analysis.

        Parameters
        ----------
        count : int
            Number of recent bars to return

        Returns
        -------
        List[Dict]
            List of K-line data dictionaries
        """
        if not self.recent_bars:
            return []

        kline_data = []
        for bar in self.recent_bars[-count:]:
            kline_data.append({
                'timestamp': bar.ts_init,
                'open': float(bar.open),
                'high': float(bar.high),
                'low': float(bar.low),
                'close': float(bar.close),
                'volume': float(bar.volume),
            })

        return kline_data

    def get_historical_context(self, count: int = 20) -> Dict[str, Any]:
        """
        Get AI-friendly historical data context for enhanced decision making.

        This method provides trending data (last N values) for each indicator,
        allowing AI to see the trajectory of indicators rather than isolated snapshots.

        Implementation Plan Section 4.2.1:
        - price_trend: Last 20 closing prices
        - volume_trend: Last 20 volumes
        - rsi_trend: Last 20 RSI values
        - macd_trend: Last 20 MACD values
        - trend_direction: BULLISH/BEARISH/NEUTRAL
        - momentum_shift: INCREASING/DECREASING/STABLE

        Parameters
        ----------
        count : int
            Number of historical values to return (default 20)

        Returns
        -------
        Dict[str, Any]
            Historical context data for AI analysis
        """
        if len(self.recent_bars) < count:
            # Not enough data yet
            return {
                "price_trend": [],
                "volume_trend": [],
                "rsi_trend": [],
                "macd_trend": [],
                "trend_direction": "INSUFFICIENT_DATA",
                "momentum_shift": "INSUFFICIENT_DATA",
                "data_points": len(self.recent_bars),
                "required_points": count,
            }

        recent = self.recent_bars[-count:]

        # Extract price trend (closing prices)
        price_trend = [float(bar.close) for bar in recent]

        # Extract volume trend
        volume_trend = [float(bar.volume) for bar in recent]

        # Calculate RSI trend from stored bars
        # Note: We recalculate RSI for each bar to get the trend
        rsi_trend = self._calculate_indicator_history('rsi', count)

        # Calculate MACD trend
        macd_trend = self._calculate_indicator_history('macd', count)

        # v5.5: MACD signal line history (for histogram trajectory)
        macd_signal_trend = list(self._macd_signal_history[-count:]) if self._macd_signal_history else []

        # Determine trend direction
        trend_direction = self._determine_trend_direction(price_trend)

        # Determine momentum shift
        momentum_shift = self._determine_momentum_shift(rsi_trend, macd_trend)

        # Additional context metrics
        price_change_pct = ((price_trend[-1] - price_trend[0]) / price_trend[0] * 100) if price_trend[0] > 0 else 0
        avg_volume = sum(volume_trend) / len(volume_trend) if volume_trend else 0
        current_volume_ratio = volume_trend[-1] / avg_volume if avg_volume > 0 else 1.0

        # v5.5: Data consistency validation
        # With stored official NT values, series[-1] should always match snapshot.
        # Log warning if mismatch detected (should never happen).
        if rsi_trend and self.rsi.initialized:
            live_rsi = round(self.rsi.value * 100, 2)
            if abs(rsi_trend[-1] - live_rsi) > 0.15:
                import logging
                logging.getLogger(__name__).warning(
                    f"RSI consistency check: history[-1]={rsi_trend[-1]} vs live={live_rsi}"
                )
        if macd_trend and self.macd.initialized:
            live_macd = round(self.macd.value, 4)
            if abs(macd_trend[-1] - live_macd) > 0.01:
                import logging
                logging.getLogger(__name__).warning(
                    f"MACD consistency check: history[-1]={macd_trend[-1]} vs live={live_macd}"
                )

        # v3.24: Additional indicator history series
        adx_history = self._calculate_adx_history(count=count)
        bb_width_history = self._calculate_bb_width_history(count=count)
        sma_history = self._calculate_sma_history(count=count)

        # v17.1: Pre-compute MACD Histogram series (MACD - Signal)
        # Previously only a snapshot was provided; AI failed to compute from two separate series
        macd_histogram_trend = []
        if macd_trend and macd_signal_trend and len(macd_trend) == len(macd_signal_trend):
            macd_histogram_trend = [round(m - s, 4) for m, s in zip(macd_trend, macd_signal_trend)]

        # v20.0: OBV history for divergence detection
        obv_trend = list(self._obv_values[-count:]) if self._obv_values else []

        return {
            # Core trend data
            "price_trend": price_trend,
            "volume_trend": volume_trend,
            "rsi_trend": rsi_trend,
            "macd_trend": macd_trend,
            "macd_signal_trend": macd_signal_trend,  # v5.5
            "macd_histogram_trend": macd_histogram_trend,  # v17.1
            "obv_trend": obv_trend,  # v20.0
            # v3.24: ADX/DI trend strength history
            "adx_trend": adx_history.get("adx", []),
            "di_plus_trend": adx_history.get("di_plus", []),
            "di_minus_trend": adx_history.get("di_minus", []),
            # v3.24: Bollinger Band width history (volatility squeeze/expansion)
            "bb_width_trend": bb_width_history,
            # v3.24: SMA history for crossover detection
            "sma_history": sma_history,
            # Trend analysis
            "trend_direction": trend_direction,
            "momentum_shift": momentum_shift,
            # Summary metrics
            "price_change_pct": round(price_change_pct, 2),
            "current_volume_ratio": round(current_volume_ratio, 2),
            "data_points": count,
            # Visual indicators for AI
            "price_arrow": "↑" if price_change_pct > 1 else ("↓" if price_change_pct < -1 else "→"),
            "rsi_current": rsi_trend[-1] if rsi_trend else 0,
            "macd_current": macd_trend[-1] if macd_trend else 0,
        }

    def _calculate_indicator_history(self, indicator_name: str, count: int) -> List[float]:
        """
        Return stored official NT indicator values for history.

        v5.5: Uses values stored in update() from NautilusTrader's official
        indicators (Wilder's RSI, EMA-based MACD) instead of simplified
        recalculation. This ensures the historical series' last value always
        matches the current snapshot value from get_technical_data().

        Previous implementation used simplified math (simple average gains/losses
        for RSI, basic EMA for MACD) which produced different values than the
        official NT indicators, causing data inconsistency in AI prompts.
        """
        if indicator_name == 'rsi':
            history = self._rsi_history
        elif indicator_name == 'macd':
            history = self._macd_history
        else:
            return []

        if not history:
            return []

        return list(history[-count:])

    def _simple_ema(self, values: List[float], period: int) -> float:
        """Calculate a simple EMA for historical data."""
        if len(values) < period:
            return values[-1] if values else 0

        multiplier = 2 / (period + 1)
        ema = sum(values[:period]) / period  # Start with SMA

        for value in values[period:]:
            ema = (value - ema) * multiplier + ema

        return ema

    def _calculate_adx_history(self, count: int = 20, period: int = 14) -> Dict[str, List[float]]:
        """
        Calculate ADX/DI+/DI- time series for last N bars (v3.24).

        Uses a sliding window approach: for each output point, we use all bars
        up to that point to calculate ADX. This gives a proper time series.

        Returns
        -------
        Dict with 'adx', 'di_plus', 'di_minus' lists (same length)
        """
        bars = self.recent_bars
        min_required = 2 * period + count + 1
        if len(bars) < min_required:
            return {"adx": [], "di_plus": [], "di_minus": []}

        adx_series = []
        di_plus_series = []
        di_minus_series = []

        # For each output point, calculate ADX using all bars up to that point
        for end_idx in range(len(bars) - count, len(bars)):
            sub_bars = bars[:end_idx + 1]
            n = len(sub_bars)
            if n < 2 * period + 1:
                continue

            tr_list = []
            plus_dm_list = []
            minus_dm_list = []

            for i in range(1, n):
                high = float(sub_bars[i].high)
                low = float(sub_bars[i].low)
                prev_close = float(sub_bars[i - 1].close)
                prev_high = float(sub_bars[i - 1].high)
                prev_low = float(sub_bars[i - 1].low)

                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                tr_list.append(tr)

                up_move = high - prev_high
                down_move = prev_low - low
                plus_dm_list.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
                minus_dm_list.append(down_move if (down_move > up_move and down_move > 0) else 0.0)

            if len(tr_list) < period:
                continue

            smoothed_tr = sum(tr_list[:period])
            smoothed_plus_dm = sum(plus_dm_list[:period])
            smoothed_minus_dm = sum(minus_dm_list[:period])

            di_plus = 0.0
            di_minus = 0.0
            dx_list = []

            for i in range(period, len(tr_list)):
                smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]
                smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
                smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]

                if smoothed_tr > 0:
                    di_plus = (smoothed_plus_dm / smoothed_tr) * 100
                    di_minus = (smoothed_minus_dm / smoothed_tr) * 100

                di_sum = di_plus + di_minus
                dx = abs(di_plus - di_minus) / di_sum * 100 if di_sum > 0 else 0.0
                dx_list.append(dx)

            if len(dx_list) >= period:
                adx = sum(dx_list[:period]) / period
                for i in range(period, len(dx_list)):
                    adx = (adx * (period - 1) + dx_list[i]) / period
                adx_series.append(round(adx, 1))
                di_plus_series.append(round(di_plus, 1))
                di_minus_series.append(round(di_minus, 1))

        return {
            "adx": adx_series,
            "di_plus": di_plus_series,
            "di_minus": di_minus_series,
        }

    def _calculate_bb_width_history(self, count: int = 20) -> List[float]:
        """
        Calculate Bollinger Band width time series for last N bars (v3.24).

        BB Width = (Upper - Lower) / Middle * 100 (as percentage)
        Shows squeeze (narrowing) or expansion (widening) of volatility.
        """
        if len(self.recent_bars) < self.bb_period + count:
            return []

        bb_widths = []
        for end_idx in range(len(self.recent_bars) - count, len(self.recent_bars)):
            window = [float(b.close) for b in self.recent_bars[end_idx - self.bb_period + 1:end_idx + 1]]
            if len(window) < self.bb_period:
                continue
            middle = sum(window) / len(window)
            variance = sum((x - middle) ** 2 for x in window) / len(window)
            std_dev = variance ** 0.5
            upper = middle + self.bb_std * std_dev
            lower = middle - self.bb_std * std_dev
            width = ((upper - lower) / middle * 100) if middle > 0 else 0
            bb_widths.append(round(width, 2))

        return bb_widths

    def _calculate_sma_history(self, count: int = 20) -> Dict[str, List[float]]:
        """
        Calculate SMA time series for last N bars (v3.24).

        Returns price and SMA values so AI can see crossovers.
        """
        result = {}
        for period in self.sma_periods:
            if len(self.recent_bars) < period + count:
                continue
            sma_values = []
            for end_idx in range(len(self.recent_bars) - count, len(self.recent_bars)):
                window = [float(b.close) for b in self.recent_bars[end_idx - period + 1:end_idx + 1]]
                if len(window) >= period:
                    sma_values.append(round(sum(window) / len(window), 2))
            if sma_values:
                result[f"sma_{period}"] = sma_values

        return result

    def _determine_trend_direction(self, price_trend: List[float]) -> str:
        """
        Determine overall trend direction from price trend.

        Uses linear regression slope approach.
        """
        if len(price_trend) < 5:
            return "INSUFFICIENT_DATA"

        # Simple linear regression slope
        n = len(price_trend)
        x_mean = (n - 1) / 2
        y_mean = sum(price_trend) / n

        numerator = sum((i - x_mean) * (price_trend[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return "NEUTRAL"

        slope = numerator / denominator
        slope_pct = (slope / y_mean * 100) if y_mean > 0 else 0

        # Classify based on slope percentage
        if slope_pct > 0.5:  # >0.5% slope per bar
            return "BULLISH"
        elif slope_pct < -0.5:
            return "BEARISH"
        else:
            return "NEUTRAL"

    def _determine_momentum_shift(
        self,
        rsi_trend: List[float],
        macd_trend: List[float]
    ) -> str:
        """
        Determine if momentum is increasing, decreasing, or stable.

        Analyzes the trajectory of RSI and MACD.
        """
        if len(rsi_trend) < 5 or len(macd_trend) < 5:
            return "INSUFFICIENT_DATA"

        # Check RSI momentum (last 5 values)
        rsi_recent = rsi_trend[-5:]
        rsi_slope = (rsi_recent[-1] - rsi_recent[0]) / 5

        # Check MACD momentum (last 5 values)
        macd_recent = macd_trend[-5:]
        macd_slope = (macd_recent[-1] - macd_recent[0]) / 5

        # Normalize slopes for comparison
        rsi_momentum = "up" if rsi_slope > 2 else ("down" if rsi_slope < -2 else "stable")
        macd_momentum = "up" if macd_slope > 0 else ("down" if macd_slope < 0 else "stable")

        # Combine signals
        if rsi_momentum == "up" and macd_momentum == "up":
            return "INCREASING"
        elif rsi_momentum == "down" and macd_momentum == "down":
            return "DECREASING"
        else:
            return "STABLE"
