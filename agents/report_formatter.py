"""
Report Formatter Mixin for MultiAgentAnalyzer

Extracted from multi_agent_analyzer.py for code organization.
Contains all data-to-text formatting methods used to prepare
AI prompt inputs from raw market data.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from agents.prompt_constants import _get_multiplier


class ReportFormatterMixin:
    """Mixin providing report formatting methods for MultiAgentAnalyzer.

    Mechanical mode (v46.0+): Only extract_features(), compute_scores_from_features(),
    compute_anticipatory_scores(), _detect_divergences(), and _calculate_sr_zones()
    are used. Text formatting methods for AI prompts have been removed.
    """

    # _format_technical_report removed (v46.0+ mechanical mode, no AI prompts)
    # _format_direction_report removed (v46.0+ mechanical mode, no AI prompts)
    # _format_derivatives_report removed (v46.0+ mechanical mode, no AI prompts)
    # _format_30m_summary removed (v46.0+ mechanical mode, only called by _format_direction_report)
    # _format_sentiment_report removed (v46.0+ mechanical mode, no AI prompts)
    # _format_position removed (v46.0+ mechanical mode, no AI prompts)
    # _format_account removed (v46.0+ mechanical mode, no AI prompts)
    # _format_order_flow_report removed (v46.0+ mechanical mode, no AI prompts)
    # _format_orderbook_report removed (v46.0+ mechanical mode, no AI prompts)

    @staticmethod
    def compute_scores_from_features(f: Dict[str, Any]) -> Dict[str, Any]:
        """
        v28.0: Compute dimensional scores from feature dict (structured path).

        Takes the same feature_dict used by structured Bull/Bear/Judge/Risk
        and produces a compact scores dict for prompt anchoring.

        Parameters
        ----------
        f : Dict
            Feature dictionary (flat keys like rsi_30m, adx_1d, etc.)

        Returns
        -------
        Dict with keys: trend, momentum, order_flow, vol_risk, risk_env, net
        """
        def sg(key, default=0):
            val = f.get(key)
            if val is None:
                return default
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        # ── Trend Alignment ── (v39.0: rebalanced 1D↓ 4H↑)
        # v40.0 Phase 1b: Weighted voting by information density (Layer A).
        # Higher weight = higher certainty/information content.
        # Replaces equal ±1 voting that treated CVD-Price cross same as buy_ratio.
        trend_weighted = []  # (signal, weight) tuples

        # 1D SMA200 — macro filter (Trend)
        # v45.0: weight 1.5→0.8 (most lagging indicator, should not dominate direction)
        sma200 = sg('sma_200_1d')
        price = sg('price')
        if sma200 > 0 and price > 0:
            above = price > sma200
            trend_weighted.append((1 if above else -1, 0.8))

        # 1D ADX direction (Trend)
        # v45.0: weight 1.2→0.9 (ADX direction lags, ADX level more useful as regime)
        adx_dir = f.get('adx_direction_1d', '')
        if adx_dir == 'BULLISH':
            trend_weighted.append((1, 0.9))
        elif adx_dir == 'BEARISH':
            trend_weighted.append((-1, 0.9))
        else:
            trend_weighted.append((0, 0.9))

        # 1D DI spread (Trend)
        di_p_1d = sg('di_plus_1d')
        di_m_1d = sg('di_minus_1d')
        if di_p_1d > di_m_1d + 2:
            trend_weighted.append((1, 0.8))
        elif di_m_1d > di_p_1d + 2:
            trend_weighted.append((-1, 0.8))
        else:
            trend_weighted.append((0, 0.8))

        # 1D RSI: weak trend signal in trend dimension (Momentum)
        rsi_1d = sg('rsi_1d', 50)
        if rsi_1d > 55:
            trend_weighted.append((1, 0.6))
        elif rsi_1d < 45:
            trend_weighted.append((-1, 0.6))
        else:
            trend_weighted.append((0, 0.6))

        # 1D MACD direction (Trend)
        # v45.0: weight 1.0→0.7 (lagging crossover signal)
        macd_1d = sg('macd_1d')
        macd_sig_1d = sg('macd_signal_1d')
        if macd_1d > macd_sig_1d:
            trend_weighted.append((1, 0.7))
        elif macd_1d < macd_sig_1d:
            trend_weighted.append((-1, 0.7))
        else:
            trend_weighted.append((0, 0.7))

        # 1D ADX trend: rising ADX = trend gaining strength (Trend)
        # v36.2: NEUTRAL adx_dir → 0 (no directional signal when DI are equal)
        adx_1d_trend = f.get('adx_1d_trend_5bar', '')
        if adx_1d_trend == 'RISING':
            trend_weighted.append((1 if adx_dir == 'BULLISH' else (-1 if adx_dir == 'BEARISH' else 0), 0.7))
        elif adx_1d_trend == 'FALLING':
            trend_weighted.append((0, 0.7))  # Trend weakening

        # 4H RSI+MACD combined (Momentum)
        # v45.0: weight 0.5→0.7 (4H more responsive than 1D)
        rsi_4h = sg('rsi_4h', 50)
        macd_4h = sg('macd_4h')
        macd_sig_4h = sg('macd_signal_4h')
        if rsi_4h > 55 and macd_4h > macd_sig_4h:
            trend_weighted.append((1, 0.7))
        elif rsi_4h < 45 and macd_4h < macd_sig_4h:
            trend_weighted.append((-1, 0.7))
        else:
            trend_weighted.append((0, 0.7))

        # 4H SMA cross (SMA20 vs SMA50) — medium-term trend (Trend)
        sma_20_4h = sg('sma_20_4h')
        sma_50_4h = sg('sma_50_4h')
        if sma_20_4h > 0 and sma_50_4h > 0:
            if sma_20_4h > sma_50_4h:
                trend_weighted.append((1, 0.8))
            elif sma_20_4h < sma_50_4h:
                trend_weighted.append((-1, 0.8))

        # 4H EMA cross (EMA12 vs EMA26) (Trend)
        ema_12_4h = sg('ema_12_4h')
        ema_26_4h = sg('ema_26_4h')
        if ema_12_4h > 0 and ema_26_4h > 0:
            if ema_12_4h > ema_26_4h:
                trend_weighted.append((1, 0.7))
            elif ema_12_4h < ema_26_4h:
                trend_weighted.append((-1, 0.7))

        # v40.0 Phase 1: Removed 3 duplicate 4H votes (v39.0).
        # 4H DI/RSI/MACD standalone were double-counted (trend + momentum).

        # 30M RSI+MACD combined — limited trend contribution (Momentum)
        rsi_30m = sg('rsi_30m', 50)
        macd_30m = sg('macd_30m')
        macd_sig_30m = sg('macd_signal_30m')
        if rsi_30m > 55 and macd_30m > macd_sig_30m:
            trend_weighted.append((1, 0.5))
        elif rsi_30m < 45 and macd_30m < macd_sig_30m:
            trend_weighted.append((-1, 0.5))
        else:
            trend_weighted.append((0, 0.5))

        # 1D DI spread trend: WIDENING = trend strengthening, NARROWING = exhaustion
        # v36.2: NEUTRAL adx_dir → 0 (spread change meaningless without direction)
        di_spread_trend = f.get('di_spread_1d_trend_5bar', '')
        if di_spread_trend == 'WIDENING':
            trend_weighted.append((1 if adx_dir == 'BULLISH' else (-1 if adx_dir == 'BEARISH' else 0), 0.8))
        elif di_spread_trend == 'NARROWING':
            trend_weighted.append((-1 if adx_dir == 'BULLISH' else (1 if adx_dir == 'BEARISH' else 0), 0.8))

        # v40.0→v45.0: Compute weighted trend score WITH structure adjustment
        if trend_weighted:
            _tw_sum = sum(s * w for s, w in trend_weighted)
            _tw_total = sum(w for _, w in trend_weighted)
            trend_raw = _tw_sum / _tw_total if _tw_total > 0 else 0
        else:
            trend_raw = 0

        # ── v45.0: Structure Adjustment (Mean-Reversion Directional Signal) ──
        # When price is at structural extremes, lagging trend indicators are
        # unreliable. Structure signals provide ANTICIPATORY direction.
        structure_adj = 0.0   # positive = bullish structure, negative = bearish
        structure_weight = 0.0

        # A1: Extension ratio as directional (most important structure signal)
        # EXTREME negative = massively oversold = bullish mean-reversion
        # EXTREME positive = massively overbought = bearish mean-reversion
        ext_ratio_1d = sg('extension_ratio_1d', 0)
        ext_regime_1d = str(f.get('extension_regime_1d', 'NORMAL')).upper()
        if ext_regime_1d == 'EXTREME':
            if ext_ratio_1d < -4.0:
                structure_adj += 1.0 * 2.5
                structure_weight += 2.5
            elif ext_ratio_1d > 4.0:
                structure_adj += -1.0 * 2.5
                structure_weight += 2.5
        elif ext_regime_1d == 'OVEREXTENDED':
            if ext_ratio_1d < -2.5:
                structure_adj += 1.0 * 1.5
                structure_weight += 1.5
            elif ext_ratio_1d > 2.5:
                structure_adj += -1.0 * 1.5
                structure_weight += 1.5

        # A2: S/R proximity as directional (near support = bullish, near resistance = bearish)
        _avail_sr = f.get('_avail_sr_zones', True)
        sup_dist_a = sg('nearest_support_dist_atr', 99)
        res_dist_a = sg('nearest_resist_dist_atr', 99)
        sup_str = str(f.get('nearest_support_strength', 'NONE')).upper()
        res_str = str(f.get('nearest_resist_strength', 'NONE')).upper()
        _sr_w_map = {'HIGH': 1.8, 'MEDIUM': 1.2, 'LOW': 0.6}
        if _avail_sr:
            if sup_dist_a < 1.5 and sup_str in _sr_w_map:
                w = _sr_w_map[sup_str]
                structure_adj += 1.0 * w
                structure_weight += w
            if res_dist_a < 1.5 and res_str in _sr_w_map:
                w = _sr_w_map[res_str]
                structure_adj += -1.0 * w
                structure_weight += w

        # A3: Fear & Greed extreme as contrarian directional
        _fg_raw = f.get('fear_greed_index')
        fg_index = int(_fg_raw) if _fg_raw is not None else 50
        if fg_index <= 15:
            structure_adj += 1.0 * 1.2
            structure_weight += 1.2
        elif fg_index <= 25:
            structure_adj += 1.0 * 0.6
            structure_weight += 0.6
        elif fg_index >= 85:
            structure_adj += -1.0 * 1.2
            structure_weight += 1.2
        elif fg_index >= 75:
            structure_adj += -1.0 * 0.6
            structure_weight += 0.6

        # v45.0: Blend structure into trend based on ADX regime
        # Low ADX = weak trend → structure dominates (mean-reversion)
        # High ADX = strong trend → structure is noise, trend dominates
        adx_1d_blend = sg('adx_1d', 25)
        if structure_weight > 0:
            structure_raw = structure_adj / structure_weight
            if adx_1d_blend < 20:
                blended = trend_raw * 0.4 + structure_raw * 0.6
            elif adx_1d_blend < 30:
                blended = trend_raw * 0.6 + structure_raw * 0.4
            elif adx_1d_blend < 40:
                blended = trend_raw * 0.8 + structure_raw * 0.2
            else:
                blended = trend_raw * 0.95 + structure_raw * 0.05
        else:
            blended = trend_raw

        trend_score = round(abs(blended) * 10)
        trend_dir = "BULLISH" if blended > 0.15 else ("BEARISH" if blended < -0.15 else "NEUTRAL")

        # ── Momentum Quality ──
        # v40.0 Phase 1b: Weighted voting by information density (Layer A).
        mom_weighted = []  # (signal, weight) tuples
        rsi_4h_trend = f.get('rsi_4h_trend_5bar', '')
        macd_hist_trend = f.get('macd_histogram_4h_trend_5bar', '')

        # RSI 4H trend direction (Momentum)
        if rsi_4h_trend == 'RISING':
            mom_weighted.append((1, 1.0))
        elif rsi_4h_trend == 'FALLING':
            mom_weighted.append((-1, 1.0))

        # v38.2 FIX: _classify_abs_trend() outputs EXPANDING/CONTRACTING/FLAT
        # Semantics: sign = direction, EXPANDING = strengthening, CONTRACTING = weakening.
        macd_hist_4h = sg('macd_histogram_4h')
        if macd_hist_trend != 'CONTRACTING':  # EXPANDING or FLAT: valid signal
            if macd_hist_4h > 0:
                mom_weighted.append((1, 1.2))   # Momentum (MACD histogram), higher weight
            elif macd_hist_4h < 0:
                mom_weighted.append((-1, 1.2))

        # 4H ADX trend: rising ADX = strengthening trend momentum
        adx_4h_trend = f.get('adx_4h_trend_5bar', '')
        if adx_4h_trend == 'RISING':
            mom_weighted.append((1, 0.8))
        elif adx_4h_trend == 'FALLING':
            mom_weighted.append((-1, 0.8))

        # 4H DI directional pressure
        di_p_4h = sg('di_plus_4h')
        di_m_4h = sg('di_minus_4h')
        if di_p_4h > 0 and di_m_4h > 0:
            if di_p_4h > di_m_4h + 5:
                mom_weighted.append((1, 0.7))
            elif di_m_4h > di_p_4h + 5:
                mom_weighted.append((-1, 0.7))

        # Volume confirmation: high volume validates momentum
        vol_4h = sg('volume_ratio_4h', 1.0)
        if vol_4h > 1.5:
            mom_weighted.append((1, 0.9))
        elif vol_4h < 0.5:
            mom_weighted.append((-1, 0.9))

        # 30M execution layer momentum direction
        rsi_30m_trend = f.get('rsi_30m_trend_5bar', '')
        if rsi_30m_trend == 'RISING':
            mom_weighted.append((1, 0.6))
        elif rsi_30m_trend == 'FALLING':
            mom_weighted.append((-1, 0.6))

        # 30M momentum acceleration/deceleration
        mom_shift = str(f.get('momentum_shift_30m', '')).upper()
        if mom_shift == 'ACCELERATING':
            mom_weighted.append((1, 0.8))
        elif mom_shift == 'DECELERATING':
            mom_weighted.append((-1, 0.8))

        # 4H price change: price momentum confirmation
        price_4h_chg = sg('price_4h_change_5bar_pct', 0)
        if price_4h_chg > 1.0:
            mom_weighted.append((1, 0.7))
        elif price_4h_chg < -1.0:
            mom_weighted.append((-1, 0.7))

        # v29.2: 4H BB position as momentum context (weak signal)
        bb_pos_4h = sg('bb_position_4h', 0.5)
        if bb_pos_4h > 0.8:
            mom_weighted.append((1, 0.5))
        elif bb_pos_4h < 0.2:
            mom_weighted.append((-1, 0.5))

        # v29.2: 30M MACD histogram direction
        macd_hist_30m = sg('macd_histogram_30m')
        if macd_hist_30m > 0:
            mom_weighted.append((1, 0.6))
        elif macd_hist_30m < 0:
            mom_weighted.append((-1, 0.6))

        # v40.0 P0-6: Divergence signals moved OUT of momentum voting.
        # They are reversal warnings and should not be diluted by ~10 trend-following signals.
        # Applied as trend_score modifier below (mutual exclusion with v39.0 reversal detection).
        div_bull = sum(1 for d in [
            f.get('rsi_divergence_4h', 'NONE'),
            f.get('macd_divergence_4h', 'NONE'),
            f.get('obv_divergence_4h', 'NONE'),
            f.get('rsi_divergence_30m', 'NONE'),
            f.get('macd_divergence_30m', 'NONE'),
            f.get('obv_divergence_30m', 'NONE'),
        ] if d == 'BULLISH')
        div_bear = sum(1 for d in [
            f.get('rsi_divergence_4h', 'NONE'),
            f.get('macd_divergence_4h', 'NONE'),
            f.get('obv_divergence_4h', 'NONE'),
            f.get('rsi_divergence_30m', 'NONE'),
            f.get('macd_divergence_30m', 'NONE'),
            f.get('obv_divergence_30m', 'NONE'),
        ] if d == 'BEARISH')

        # v40.0: Compute weighted momentum score
        if mom_weighted:
            _mw_sum = sum(s * w for s, w in mom_weighted)
            _mw_total = sum(w for _, w in mom_weighted)
            mom_raw = _mw_sum / _mw_total if _mw_total > 0 else 0
            mom_score = round(abs(mom_raw) * 10)
            mom_dir = "BULLISH" if mom_raw > 0.15 else ("BEARISH" if mom_raw < -0.15 else "FADING")
        else:
            mom_score, mom_dir = 0, "N/A"

        # ── Order Flow ──
        # v40.0 Phase 1b: Weighted by information density (Layer A).
        # CVD-Price cross (smart money behavior) >> CVD trend >> buy_ratio (noise).
        _avail_order_flow = f.get('_avail_order_flow', True)
        flow_weighted = []  # (signal, weight) tuples

        # CVD trend 30M (Order Flow)
        cvd_30m = str(f.get('cvd_trend_30m', '')).upper()
        if cvd_30m == 'POSITIVE':
            flow_weighted.append((1, 0.8))
        elif cvd_30m == 'NEGATIVE':
            flow_weighted.append((-1, 0.8))

        # Buy ratio 30M — high noise (Order Flow)
        buy_ratio = sg('buy_ratio_30m', 0.5)
        if buy_ratio > 0.55:
            flow_weighted.append((1, 0.5))
        elif buy_ratio < 0.45:
            flow_weighted.append((-1, 0.5))

        # CVD trend 4H (Order Flow)
        cvd_4h = str(f.get('cvd_trend_4h', '')).upper()
        if cvd_4h == 'POSITIVE':
            flow_weighted.append((1, 1.0))
        elif cvd_4h == 'NEGATIVE':
            flow_weighted.append((-1, 1.0))

        # CVD-Price cross — highest information density (Order Flow)
        cvd_cross_30m = str(f.get('cvd_price_cross_30m', '')).upper()
        cvd_cross_4h = str(f.get('cvd_price_cross_4h', '')).upper()
        if cvd_cross_4h in ('ACCUMULATION', 'ABSORPTION_BUY'):
            flow_weighted.append((1, 2.0))
        elif cvd_cross_4h in ('DISTRIBUTION', 'ABSORPTION_SELL'):
            flow_weighted.append((-1, 2.0))
        if cvd_cross_30m in ('ACCUMULATION', 'ABSORPTION_BUY'):
            flow_weighted.append((1, 1.5))
        elif cvd_cross_30m in ('DISTRIBUTION', 'ABSORPTION_SELL'):
            flow_weighted.append((-1, 1.5))

        # Buy ratio 4H — high noise (Order Flow)
        buy_ratio_4h = sg('buy_ratio_4h', 0.5)
        if buy_ratio_4h > 0.55:
            flow_weighted.append((1, 0.5))
        elif buy_ratio_4h < 0.45:
            flow_weighted.append((-1, 0.5))

        # Taker buy ratio: aggressive order direction (Order Flow, but noisy)
        taker = sg('taker_buy_ratio', 0.5)
        if taker > 0.55:
            flow_weighted.append((1, 0.6))
        elif taker < 0.45:
            flow_weighted.append((-1, 0.6))

        # OBI (orderbook pressure) — can be spoofed (Order Flow)
        obi = sg('obi_weighted')
        if obi > 0.2:
            flow_weighted.append((1, 0.8))
        elif obi < -0.2:
            flow_weighted.append((-1, 0.8))

        # OBI dynamic shift — high noise
        obi_change = sg('obi_change_pct')
        if obi_change > 20:
            flow_weighted.append((1, 0.5))
        elif obi_change < -20:
            flow_weighted.append((-1, 0.5))

        # ── v45.0: Derivatives-enhanced order flow signals ──
        # Promote risk-only data to directional votes
        _avail_derivatives = f.get('_avail_derivatives', True)
        _avail_binance_deriv = f.get('_avail_binance_derivatives', True)

        # B1: OI trend + price direction = position type inference
        # OI rising + price rising = new longs (bullish conviction)
        # OI rising + price falling = new shorts (bearish conviction)
        # OI falling + price falling = long liquidation exhaustion (mildly bullish)
        # OI falling + price rising = short covering exhaustion (mildly bearish)
        if _avail_derivatives:
            oi_trend_val = str(f.get('oi_trend', '')).upper()
            _price_dir = 1 if trend_raw > 0.1 else (-1 if trend_raw < -0.1 else 0)
            if oi_trend_val == 'RISING' and _price_dir > 0:
                flow_weighted.append((1, 1.2))
            elif oi_trend_val == 'RISING' and _price_dir < 0:
                flow_weighted.append((-1, 1.2))
            elif oi_trend_val == 'FALLING' and _price_dir < 0:
                flow_weighted.append((1, 0.7))   # exhaustion = mildly bullish
            elif oi_trend_val == 'FALLING' and _price_dir > 0:
                flow_weighted.append((-1, 0.7))  # covering = mildly bearish

        # B2: Liquidation bias as directional pressure
        # Short liquidations dominant = shorts squeezed = bullish
        # Long liquidations dominant = longs flushed = bearish
        if _avail_derivatives:
            liq_bias_val = str(f.get('liquidation_bias', '')).upper()
            if liq_bias_val == 'SHORT_DOMINANT':
                flow_weighted.append((1, 1.0))
            elif liq_bias_val == 'LONG_DOMINANT':
                flow_weighted.append((-1, 1.0))

        # B3: Top traders positioning — longShortRatio centered at 1.0
        if _avail_binance_deriv:
            top_long_val = sg('top_traders_long_ratio', 1.0)
            if top_long_val > 1.05:
                flow_weighted.append((1, 1.0))
            elif top_long_val > 1.02:
                flow_weighted.append((1, 0.5))
            elif top_long_val < 0.95:
                flow_weighted.append((-1, 1.0))
            elif top_long_val < 0.98:
                flow_weighted.append((-1, 0.5))

        # B4: Funding rate extreme as contrarian directional
        # Very positive FR = overleveraged longs = bearish reversion
        # Very negative FR = overleveraged shorts = bullish reversion
        if _avail_derivatives:
            fr_val = sg('funding_rate_pct')
            if fr_val > 0.05:
                flow_weighted.append((-1, 1.2))
            elif fr_val > 0.03:
                flow_weighted.append((-1, 0.6))
            elif fr_val < -0.05:
                flow_weighted.append((1, 1.2))
            elif fr_val < -0.03:
                flow_weighted.append((1, 0.6))

        # B5: CVD cumulative 30M (extracted but previously unused)
        if _avail_order_flow:
            cvd_cum = sg('cvd_cumulative_30m', 0)
            if cvd_cum > 0:
                flow_weighted.append((1, 0.8))
            elif cvd_cum < 0:
                flow_weighted.append((-1, 0.8))

        # v40.0→v45.0: Compute weighted order flow score
        if not _avail_order_flow:
            flow_score, flow_dir = 0, "N/A"
        elif flow_weighted:
            _fw_sum = sum(s * w for s, w in flow_weighted)
            _fw_total = sum(w for _, w in flow_weighted)
            flow_raw = _fw_sum / _fw_total if _fw_total > 0 else 0
            flow_score = round(abs(flow_raw) * 10)
            flow_dir = "BULLISH" if flow_raw > 0.15 else ("BEARISH" if flow_raw < -0.15 else "MIXED")
        else:
            flow_score, flow_dir = 0, "N/A"

        # ── Vol/Extension Risk (0-10, higher = riskier) ──
        # v29.2: worst-case across all 3 timeframes
        _ext_map = {'NORMAL': 1, 'EXTENDED': 3, 'OVEREXTENDED': 6, 'EXTREME': 9}
        _vol_map = {'LOW': 1, 'NORMAL': 2, 'HIGH': 5, 'EXTREME': 8}
        ext_risks = [
            _ext_map.get(f.get('extension_regime_30m', 'NORMAL'), 2),
            _ext_map.get(f.get('extension_regime_4h', 'NORMAL'), 1),
            _ext_map.get(f.get('extension_regime_1d', 'NORMAL'), 1),
        ]
        vol_risks = [
            _vol_map.get(f.get('volatility_regime_30m', 'NORMAL'), 2),
            _vol_map.get(f.get('volatility_regime_4h', 'NORMAL'), 1),
            _vol_map.get(f.get('volatility_regime_1d', 'NORMAL'), 1),
        ]
        ext_risk = max(ext_risks)
        vol_risk = max(vol_risks)
        vol_ext_score = min(10, max(ext_risk, vol_risk))

        # v36.1: BB width squeeze amplifies vol_ext risk
        # _classify_trend() returns RISING/FALLING/FLAT for BB width series.
        # FALLING BB width = bands contracting = squeeze = impending breakout (risk +1)
        # RISING BB width already captured by volatility_regime, no double-count
        for bb_key in ('bb_width_30m_trend_5bar', 'bb_width_4h_trend_5bar'):
            bb_trend = str(f.get(bb_key, '')).upper()
            if bb_trend == 'FALLING':
                vol_ext_score = min(10, vol_ext_score + 1)
                break  # only +1 total, not per-TF

        # ── Risk Environment (0-10, higher = riskier) ──
        risk_score = 2
        _avail_derivatives = f.get('_avail_derivatives', True)
        fr = sg('funding_rate_pct')
        # v34.1: Skip FR factors when derivatives unavailable (0.0 is artifact)
        if _avail_derivatives and abs(fr) > 0.05:
            risk_score += 3
        elif _avail_derivatives and abs(fr) > 0.02:
            risk_score += 1

        # v34.3: Guard with _avail_sentiment — 0.0 default is artifact, not real data
        _avail_sentiment = f.get('_avail_sentiment', True)
        long_ratio = sg('long_ratio', 0.5)
        if _avail_sentiment and (long_ratio > 0.7 or long_ratio < 0.3):
            risk_score += 2
        elif _avail_sentiment and (long_ratio > 0.6 or long_ratio < 0.4):
            risk_score += 1

        # OI trend: rising OI = new positions opening = higher leverage in system
        oi_trend = str(f.get('oi_trend', '')).upper()
        if oi_trend == 'RISING':
            risk_score += 1

        # Liquidation cascade bias: directional liquidations = forced selling/buying
        liq_bias = str(f.get('liquidation_bias', '')).upper()
        if liq_bias in ('LONG_DOMINANT', 'SHORT_DOMINANT'):
            risk_score += 1

        # OBI imbalance: extreme orderbook skew = slippage risk
        # v34.2: Skip when orderbook data unavailable (0.0 is artifact)
        _avail_orderbook = f.get('_avail_orderbook', True)
        obi = sg('obi_weighted')
        if _avail_orderbook and abs(obi) > 0.4:
            risk_score += 1

        # Liquidation buffer: close to liquidation = critical risk
        # v34.2: Skip when account data unavailable (100.0 default is artifact)
        _avail_account = f.get('_avail_account', True)
        liq_buffer = sg('liquidation_buffer_pct', 100.0)
        # v36.0: liq_buffer=0 means at/past liquidation — most dangerous.
        # Previously `0 < liq_buffer < 5` missed the buffer=0 edge case.
        if _avail_account and 0 <= liq_buffer < 5:
            risk_score += 3
        elif _avail_account and 0 < liq_buffer < 10:
            risk_score += 1

        # FR trend: rising FR = increasing pressure on one side
        fr_trend = str(f.get('funding_rate_trend', '')).upper()
        if fr_trend in ('RISING', 'FALLING'):
            risk_score += 1

        # Premium index: extreme premium/discount = leverage risk
        premium = sg('premium_index')
        if abs(premium) > 0.001:
            risk_score += 1

        # Sentiment degraded: data quality risk
        if f.get('sentiment_degraded'):
            risk_score += 1

        # v29.2: 4H/1D volatility regime contributes to risk environment
        for vr_key in ('volatility_regime_4h', 'volatility_regime_1d'):
            vr = str(f.get(vr_key, '')).upper()
            if vr == 'EXTREME':
                risk_score += 1
            elif vr == 'HIGH':
                risk_score += 0  # Only EXTREME adds risk at higher TFs

        # FR consecutive blocks: operational risk (≥3 = stuck in loop)
        fr_blocks = sg('fr_consecutive_blocks', 0)
        if fr_blocks >= 3:
            risk_score += 1

        # Top traders extreme positioning: contrarian risk
        # v34.2: Skip when binance_derivatives data unavailable (0.5 default is artifact)
        _avail_binance_deriv = f.get('_avail_binance_derivatives', True)
        top_long = sg('top_traders_long_ratio', 1.0)
        if _avail_binance_deriv and (top_long > 1.08 or top_long < 0.92):
            risk_score += 1

        # v36.1: S/R proximity risk — price within 1 ATR of support or resistance
        # increases risk of bounce/rejection (relevant for position management)
        _avail_sr = f.get('_avail_sr_zones', True)
        sup_dist = sg('nearest_support_dist_atr', 99)
        res_dist = sg('nearest_resist_dist_atr', 99)
        if _avail_sr and min(sup_dist, res_dist) < 1.0 and min(sup_dist, res_dist) > 0:
            risk_score += 1

        # v44.0: Fear & Greed extremes — contrarian risk signal
        fg_index = int(f.get('fear_greed_index', 50))
        if fg_index < 20 or fg_index > 80:
            risk_score += 1

        risk_score = min(10, risk_score)

        # ── Trend Reversal Detection ── (v39.0)
        # Detects conditions where trend is exhausting and reversal is building.
        # When active, reduces trend_score certainty to prevent stale trend bias.
        reversal_bull_count = 0
        reversal_bear_count = 0

        # Signal 1: ADX falling from elevated level (trend exhaustion)
        adx_1d = sg('adx_1d')
        if adx_1d > 25 and adx_1d_trend == 'FALLING':
            if trend_dir == 'BEARISH':
                reversal_bull_count += 1
            elif trend_dir == 'BULLISH':
                reversal_bear_count += 1

        # Signal 2: Multiple divergences (2+ across timeframes)
        if div_bull >= 2:
            reversal_bull_count += 1
        if div_bear >= 2:
            reversal_bear_count += 1

        # Signal 3: DI convergence (directional conviction weakening)
        if di_spread_trend == 'NARROWING':
            if trend_dir == 'BEARISH':
                reversal_bull_count += 1
            elif trend_dir == 'BULLISH':
                reversal_bear_count += 1

        # Signal 4: Price near strong support/resistance
        if trend_dir == 'BEARISH' and sup_dist < 2:
            reversal_bull_count += 1
        elif trend_dir == 'BULLISH' and res_dist < 2:
            reversal_bear_count += 1

        # Signal 5: 4H momentum opposing trend direction
        if trend_dir == 'BEARISH' and mom_dir == 'BULLISH':
            reversal_bull_count += 1
        elif trend_dir == 'BULLISH' and mom_dir == 'BEARISH':
            reversal_bear_count += 1

        # Signal 6 (v45.0): EXTREME extension opposing trend = structural reversal pressure
        if ext_regime_1d == 'EXTREME':
            if ext_ratio_1d < -4.0 and trend_dir == 'BEARISH':
                reversal_bull_count += 1
            elif ext_ratio_1d > 4.0 and trend_dir == 'BULLISH':
                reversal_bear_count += 1

        # Determine reversal state (requires 3+ of 6 signals)
        reversal_active = max(reversal_bull_count, reversal_bear_count) >= 3
        reversal_dir = 'NONE'
        if reversal_bull_count >= 3:
            reversal_dir = 'BULLISH'
        elif reversal_bear_count >= 3:
            reversal_dir = 'BEARISH'

        # When reversal signal is active, reduce trend certainty
        if reversal_active:
            trend_score = max(1, trend_score - 3)

        # v40.0 P0-6: Divergence adjustment — reversal warning applied to trend_score.
        # Moved from momentum voting to prevent dilution by ~10 trend-following signals.
        # Mutual exclusion with v39.0 reversal detection: reversal_active already includes
        # divergence as one of its 5 conditions, so don't double-penalize (-2 + -3 = -5).
        if not reversal_active:
            # Both bullish and bearish divergences reduce trend certainty.
            # trend_score is unsigned (0-10 certainty), so adjustment is always negative.
            max_div = max(div_bull, div_bear)
            if max_div >= 3:
                divergence_adjustment = -3  # Strong reversal warning → weaken trend certainty
            elif max_div >= 2:
                divergence_adjustment = -2
            else:
                divergence_adjustment = 0
            if divergence_adjustment != 0:
                trend_score = max(0, trend_score + divergence_adjustment)

        # ── Regime Transition Detection ── (v40.0 Phase 2)
        # When order_flow dimension opposes trend dimension,
        # the market may be transitioning. This is informative, not "conflicting".
        _avail_mtf_1d = f.get('_avail_mtf_1d', True)
        _avail_mtf_4h = f.get('_avail_mtf_4h', True)
        _regime_transition = "NONE"

        if _avail_order_flow and flow_dir not in ("N/A", "MIXED"):
            if trend_dir == "BEARISH" and flow_dir == "BULLISH":
                _regime_transition = "TRANSITIONING_BULLISH"
            elif trend_dir == "BULLISH" and flow_dir == "BEARISH":
                _regime_transition = "TRANSITIONING_BEARISH"
        # v40.0 Phase 2c: Fallback when order_flow unavailable — use momentum as proxy
        elif not _avail_order_flow and _avail_mtf_4h:
            if trend_dir == "BEARISH" and mom_dir == "BULLISH":
                _regime_transition = "TRANSITIONING_BULLISH"
            elif trend_dir == "BULLISH" and mom_dir == "BEARISH":
                _regime_transition = "TRANSITIONING_BEARISH"

        # v40.0 Phase 2b: 2-cycle hysteresis — require consecutive detection
        # _prev_regime_transition passed via feature_dict by caller (ai_strategy.py)
        _raw_transition = _regime_transition
        if _raw_transition != "NONE":
            _prev = f.get("_prev_regime_transition", "NONE")
            if _prev == _raw_transition:
                _regime_transition = _raw_transition  # Confirmed: 2 consecutive cycles
            else:
                _regime_transition = "NONE"  # First cycle: don't act yet

        # ── Net Assessment ── (v40.0 Phase 3: Regime-dependent weighted net)
        # v40.0 P0-1: Use (direction, dim_name) tuples to prevent zip mapping errors.
        _dir_pairs = []  # (direction_label, dimension_name)
        if _avail_mtf_1d:
            _dir_pairs.append((trend_dir, "trend"))
        if _avail_mtf_4h:
            _dir_pairs.append((mom_dir, "momentum"))
        if _avail_order_flow:
            _dir_pairs.append((flow_dir, "order_flow"))

        # v40.0 Phase 3 / Layer C: Regime-dependent dimension weights
        # ADX thresholds use discrete steps (not continuous — known limitation,
        # mitigated by 2-cycle hysteresis on TRANSITIONING).
        adx_1d_val = sg('adx_1d')
        adx_4h_val = sg('adx_4h', 0)
        adx_effective = max(adx_1d_val, adx_4h_val)

        # v45.0: Structure-aware regime-dependent weights
        _is_extreme_structure = ext_regime_1d in ('EXTREME', 'OVEREXTENDED')

        if _regime_transition != "NONE":
            # TRANSITIONING: order_flow gets 2.5x (v45.0: was 2.0)
            weights = {"trend": 1.0, "momentum": 1.0, "order_flow": 2.5}
        elif adx_effective < 20 and _is_extreme_structure:
            # v45.0 NEW: Ranging + extreme extension = mean-reversion setup
            # Structure already baked into trend_dir (Change A),
            # order_flow confirms or denies the thesis
            weights = {"trend": 1.2, "momentum": 0.8, "order_flow": 2.0}
        elif adx_effective < 20:
            # Ranging (no extreme extension): order_flow still more reliable
            weights = {"trend": 0.7, "momentum": 1.0, "order_flow": 1.5}
        elif adx_effective >= 40:
            # Strong trend: trend dimension most reliable
            weights = {"trend": 1.5, "momentum": 1.0, "order_flow": 0.8}
        else:
            # Default: equal weights
            weights = {"trend": 1.0, "momentum": 1.0, "order_flow": 1.0}

        # Build weighted scores
        weighted_scores = []
        weight_list = []
        for d, dim_name in _dir_pairs:
            w = weights.get(dim_name, 1.0)
            if d == "BULLISH":
                weighted_scores.append(1 * w)
            elif d == "BEARISH":
                weighted_scores.append(-1 * w)
            else:
                # NEUTRAL, FADING, MIXED, N/A → 0 (inconclusive, still counted)
                weighted_scores.append(0)
            weight_list.append(w)

        if len(weighted_scores) < 2:
            net_label = "INSUFFICIENT"
        elif any(s != 0 for s in weighted_scores):
            net_raw = sum(weighted_scores) / sum(weight_list) if sum(weight_list) > 0 else 0
            # v40.0: TRANSITIONING regime gets its own net label prefix
            if _regime_transition != "NONE":
                net_label = _regime_transition
            elif net_raw > 0.25:  # v45.0: was 0.3 — allow structure-adjusted signals to break through
                net_label = "LEAN_BULLISH"
            elif net_raw < -0.25:  # v45.0: was -0.3
                net_label = "LEAN_BEARISH"
            else:
                net_label = "CONFLICTING"
            # Count aligned dimensions
            _majority_sign = 1 if net_raw >= 0 else -1
            aligned = sum(1 for s in weighted_scores if (s > 0 and _majority_sign > 0) or (s < 0 and _majority_sign < 0))
            net_label += f"_{aligned}of{len(weighted_scores)}"
        else:
            net_label = "INSUFFICIENT"

        return {
            "trend": {"score": trend_score, "direction": trend_dir},
            "momentum": {"score": mom_score, "direction": mom_dir},
            "order_flow": {"score": flow_score, "direction": flow_dir},
            "vol_ext_risk": {"score": vol_ext_score, "regime_30m": f"{f.get('extension_regime_30m', 'NORMAL')}/{f.get('volatility_regime_30m', 'NORMAL')}", "regime_4h": f"{f.get('extension_regime_4h', 'N/A')}/{f.get('volatility_regime_4h', 'N/A')}", "regime_1d": f"{f.get('extension_regime_1d', 'N/A')}/{f.get('volatility_regime_1d', 'N/A')}"},
            "risk_env": {"score": risk_score, "level": "HIGH" if risk_score >= 6 else ("MODERATE" if risk_score >= 4 else "LOW")},
            "net": net_label,
            # v40.0: Regime transition state
            "regime_transition": _regime_transition,
            # Store raw (pre-hysteresis) detection for next cycle
            "_raw_regime_transition": _raw_transition,
            # v39.0: Trend reversal detection
            "trend_reversal": {
                "active": reversal_active,
                "direction": reversal_dir,
                "signals": max(reversal_bull_count, reversal_bear_count),
            },
        }

    @staticmethod
    def compute_anticipatory_scores(f: Dict[str, Any], regime_config: Dict = None) -> Dict[str, Any]:
        """
        v10.0: Compute anticipatory scores from feature dict (mechanical path).

        Replaces the 11 lagging trend signals with 3 anticipatory dimensions:
        - Structure: Extension Ratio (mean-reversion) + S/R proximity
        - Divergence: RSI/MACD/OBV divergences across 4H+30M
        - Order Flow: CVD-Price cross, OI inference, FR contrarian, liquidation, Volume Climax

        Parameters
        ----------
        f : Dict
            Feature dictionary from extract_features()
        regime_config : Dict, optional
            REGIME_CONFIG from configs/base.yaml

        Returns
        -------
        Dict with keys: structure, divergence, order_flow, risk_env,
            anticipatory_raw, regime, trend_context, vol_ext_risk
        """
        import numpy as _np

        if regime_config is None:
            regime_config = {}

        def sg(key, default=0.0):
            val = f.get(key)
            if val is None:
                return default
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        # ========================================
        # Dimension 1: STRUCTURE (mean-reversion + S/R) — default weight 40%
        # Pure price-derived, zero order-flow dependency
        #
        # v47.0: Extension weights rebalanced.  1D SMA(200) EXTREME persists
        # for months in trending markets (BTC -19% below SMA200 → permanent
        # EXTREME), making Structure a constant.  Weights equalised so S/R
        # and 4H extension can counterbalance 1D.  4H EXTENDED/OVEREXTENDED
        # now vote (more responsive than 1D SMA200).
        # ========================================
        structure_votes = []  # (direction, weight)

        # Extension 1D — mean-reversion signal
        ext_ratio_1d = sg('extension_ratio_1d', 0)
        ext_regime_1d = str(f.get('extension_regime_1d', 'NORMAL')).upper()
        if ext_regime_1d == 'EXTREME':
            direction = -1 if ext_ratio_1d > 0 else 1
            structure_votes.append((direction, 1.5))
        elif ext_regime_1d == 'OVEREXTENDED':
            direction = -1 if ext_ratio_1d > 0 else 1
            structure_votes.append((direction, 1.5))

        # Extension 4H — more responsive than 1D SMA(200)
        ext_ratio_4h = sg('extension_ratio_4h', 0)
        ext_regime_4h = str(f.get('extension_regime_4h', 'NORMAL')).upper()
        if ext_regime_4h == 'EXTREME':
            direction = -1 if ext_ratio_4h > 0 else 1
            structure_votes.append((direction, 1.5))
        elif ext_regime_4h in ('OVEREXTENDED', 'EXTENDED'):
            direction = -1 if ext_ratio_4h > 0 else 1
            structure_votes.append((direction, 1.0))

        # S/R proximity — near support = bullish, near resistance = bearish
        _avail_sr = f.get('_avail_sr_zones', True)
        sup_dist_a = sg('nearest_support_dist_atr', 99)
        res_dist_a = sg('nearest_resist_dist_atr', 99)
        sup_str = str(f.get('nearest_support_strength', 'NONE')).upper()
        res_str = str(f.get('nearest_resist_strength', 'NONE')).upper()
        _sr_w = {'HIGH': 2.5, 'MEDIUM': 1.5, 'LOW': 0.8}
        if _avail_sr:
            if sup_dist_a < 1.5 and sup_str in _sr_w:
                structure_votes.append((1, _sr_w[sup_str]))
            if res_dist_a < 1.5 and res_str in _sr_w:
                structure_votes.append((-1, _sr_w[res_str]))

        # Fear & Greed extreme — contrarian mean-reversion signal
        # Migrated from compute_scores_from_features() A3 (v45.0)
        _fg_raw = f.get('fear_greed_index')
        fg_index = int(_fg_raw) if _fg_raw is not None else 50
        if fg_index <= 15:
            structure_votes.append((1, 1.2))   # Extreme Fear → bullish
        elif fg_index <= 25:
            structure_votes.append((1, 0.6))   # Fear → mildly bullish
        elif fg_index >= 85:
            structure_votes.append((-1, 1.2))  # Extreme Greed → bearish
        elif fg_index >= 75:
            structure_votes.append((-1, 0.6))  # Greed → mildly bearish

        if structure_votes:
            s_sum = sum(d * w for d, w in structure_votes)
            s_total = sum(w for _, w in structure_votes)
            structure_raw = s_sum / s_total if s_total > 0 else 0
        else:
            structure_raw = 0.0
        # Confluence factor: single signal gets 50% weight, 2+ gets full weight.
        # v47.0: Relaxed from /3 to /2 — production data shows 59% of snapshots
        # have only 1 Structure vote, old /3 damping crushed net_raw to ~0.03.
        _s_conf = min(1.0, len(structure_votes) / 2) if structure_votes else 0
        structure_score = round(abs(structure_raw) * 10 * _s_conf)
        structure_dir = "BULLISH" if structure_raw > 0.15 else ("BEARISH" if structure_raw < -0.15 else "NEUTRAL")

        # ========================================
        # Dimension 2: DIVERGENCE (multi-TF momentum exhaustion) — default weight 30%
        # ========================================
        div_votes = []

        for tf_suffix, base_w in [('_4h', 2.0), ('_30m', 1.0)]:
            for ind, ind_w in [('rsi', 1.0), ('macd', 1.0), ('obv', 0.75)]:
                key = f"{ind}_divergence{tf_suffix}"
                val = str(f.get(key, 'NONE')).upper()
                weight = base_w * ind_w
                if val == 'BULLISH':
                    div_votes.append((1, weight))
                elif val == 'BEARISH':
                    div_votes.append((-1, weight))

        if div_votes:
            d_sum = sum(d * w for d, w in div_votes)
            d_total = sum(w for _, w in div_votes)
            divergence_raw = d_sum / d_total if d_total > 0 else 0
        else:
            divergence_raw = 0.0
        # Confluence factor: relaxed /3→/2 (divergences are rare, 58% snapshots=0)
        _d_conf = min(1.0, len(div_votes) / 2) if div_votes else 0
        divergence_score = round(abs(divergence_raw) * 10 * _d_conf)
        divergence_dir = "BULLISH" if divergence_raw > 0.15 else ("BEARISH" if divergence_raw < -0.15 else "NEUTRAL")

        # ========================================
        # Dimension 3: ORDER FLOW (microstructure anticipation) — default weight 30%
        # ========================================
        _avail_order_flow = f.get('_avail_order_flow', True)
        _avail_derivatives = f.get('_avail_derivatives', True)
        _avail_binance_deriv = f.get('_avail_binance_derivatives', True)
        flow_votes = []

        # CVD-Price cross — highest information density
        for tf, weight in [('_4h', 2.5), ('_30m', 1.5)]:
            cross = str(f.get(f'cvd_price_cross{tf}', '')).upper()
            if cross in ('ACCUMULATION', 'ABSORPTION_BUY'):
                flow_votes.append((1, weight))
            elif cross in ('DISTRIBUTION', 'ABSORPTION_SELL', 'CONFIRMED_SELL'):
                flow_votes.append((-1, weight))

        # Volume Climax: vol>3× + flat price → trend exhaustion
        vol_ratio_4h = sg('volume_ratio_4h', 1.0)
        price_chg_4h = sg('price_4h_change_5bar_pct', 0)
        if vol_ratio_4h > 3.0 and abs(price_chg_4h) < 0.3:
            trend_dir_1d = str(f.get('adx_direction_1d', 'NEUTRAL')).upper()
            if trend_dir_1d == 'BULLISH':
                flow_votes.append((-1, 1.5))  # Exhaustion = counter-trend
            elif trend_dir_1d == 'BEARISH':
                flow_votes.append((1, 1.5))

        # Liquidation bias
        if _avail_derivatives:
            liq_bias = str(f.get('liquidation_bias', '')).upper()
            if liq_bias == 'SHORT_DOMINANT':
                flow_votes.append((1, 1.5))
            elif liq_bias == 'LONG_DOMINANT':
                flow_votes.append((-1, 1.5))

        # OI + Price inference
        if _avail_derivatives:
            oi_trend = str(f.get('oi_trend', '')).upper()
            if oi_trend == 'RISING' and price_chg_4h > 0.3:
                flow_votes.append((1, 1.2))
            elif oi_trend == 'RISING' and price_chg_4h < -0.3:
                flow_votes.append((-1, 1.2))
            elif oi_trend == 'FALLING' and price_chg_4h < -0.3:
                flow_votes.append((1, 0.7))
            elif oi_trend == 'FALLING' and price_chg_4h > 0.3:
                flow_votes.append((-1, 0.7))

        # FR extreme (slow filter, only |FR|>0.03%)
        if _avail_derivatives:
            fr = sg('funding_rate_pct', 0)
            if fr > 0.03:
                flow_votes.append((-1, 1.0))
            elif fr < -0.03:
                flow_votes.append((1, 1.0))

        # Top Traders positioning — Binance longShortRatio is L/S ratio
        # centered at 1.0 (not 0-1 percentage). >1.05 = longs dominant.
        if _avail_binance_deriv:
            top_long = sg('top_traders_long_ratio', 1.0)
            if top_long > 1.05:
                flow_votes.append((1, 0.8))
            elif top_long < 0.95:
                flow_votes.append((-1, 0.8))

        # Momentum shift — 30M acceleration/deceleration as timing signal
        mom_shift = str(f.get('momentum_shift_30m', '')).upper()
        if mom_shift == 'ACCELERATING':
            # Momentum accelerating in current direction → confirms flow
            if price_chg_4h > 0:
                flow_votes.append((1, 0.6))
            elif price_chg_4h < 0:
                flow_votes.append((-1, 0.6))
        elif mom_shift == 'DECELERATING':
            # Momentum fading → early reversal hint (counter-current direction)
            if price_chg_4h > 0.3:
                flow_votes.append((-1, 0.5))
            elif price_chg_4h < -0.3:
                flow_votes.append((1, 0.5))

        # Premium index — perpetual premium as demand signal
        if _avail_derivatives:
            prem = sg('premium_index', 0)
            if prem > 0.001:
                flow_votes.append((1, 0.6))   # Perpetual premium → long demand
            elif prem < -0.001:
                flow_votes.append((-1, 0.6))  # Perpetual discount → short pressure

        # CVD Absorption (sub-type of CVD-Price cross already captured above)
        # CVD cumulative 30M
        if _avail_order_flow:
            cvd_cum = sg('cvd_cumulative_30m', 0)
            if cvd_cum > 0:
                flow_votes.append((1, 0.8))
            elif cvd_cum < 0:
                flow_votes.append((-1, 0.8))

        if not _avail_order_flow:
            flow_raw, flow_score, flow_dir = 0.0, 0, "N/A"
        elif flow_votes:
            f_sum = sum(d * w for d, w in flow_votes)
            f_total = sum(w for _, w in flow_votes)
            flow_raw = f_sum / f_total if f_total > 0 else 0
            # Confluence factor: relaxed /4→/3 (many sub-signals inactive in practice)
            _f_conf = min(1.0, len(flow_votes) / 3) if flow_votes else 0
            flow_score = round(abs(flow_raw) * 10 * _f_conf)
            flow_dir = "BULLISH" if flow_raw > 0.15 else ("BEARISH" if flow_raw < -0.15 else "MIXED")
        else:
            flow_raw, flow_score, flow_dir = 0.0, 0, "N/A"

        # ========================================
        # Regime detection (rules, not HMM)
        # ========================================
        adx_1d = sg('adx_1d', 25)
        vol_regime = str(f.get('volatility_regime_1d', 'NORMAL')).upper()
        if vol_regime == 'EXTREME':
            regime = 'VOLATILE'
        elif adx_1d >= 40:
            regime = 'TRENDING'
        elif adx_1d < 20:
            if ext_regime_1d in ('EXTREME', 'OVEREXTENDED'):
                regime = 'MEAN_REVERSION'
            else:
                regime = 'RANGING'
        else:
            regime = 'DEFAULT'

        # ========================================
        # Dynamic weight boost + net_raw
        # ========================================
        cfg = regime_config.get(regime, regime_config.get('DEFAULT', {}))
        weights_list = cfg.get('weights', [0.40, 0.30, 0.30])
        ext_boost = cfg.get('extreme_boost', 0.15)

        w_s = weights_list[0] if len(weights_list) > 0 else 0.40
        w_d = weights_list[1] if len(weights_list) > 1 else 0.30
        w_f = weights_list[2] if len(weights_list) > 2 else 0.30

        # Data degradation: zero out unavailable dimensions
        if not _avail_order_flow:
            w_f = 0.0
        # Divergence dimension: extracted from klines, always available when 4H/30M data exists.
        # No weight zeroing needed (unlike order_flow which depends on external APIs).

        # Dynamic weight boost: Extension EXTREME → Structure ≥50%
        # v47.0: Skip boost in MEAN_REVERSION — regime config already sets
        # w_s=0.57 (structure-dominant).  Adding boost → 0.67 double-emphasis
        # locks Structure as permanent dominant dimension.
        if ext_regime_1d == 'EXTREME' and regime != 'MEAN_REVERSION':
            w_s = max(w_s, 0.50 + ext_boost)

        # Multi-divergence boost: ≥2 4H divergences → Divergence ≥40%
        n_4h_div = sum(
            1 for k in ('rsi_divergence_4h', 'macd_divergence_4h', 'obv_divergence_4h')
            if str(f.get(k, 'NONE')).upper() in ('BULLISH', 'BEARISH')
        )
        if n_4h_div >= 2:
            w_d = max(w_d, 0.40)

        # CVD cross boost: Accumulation/Distribution → Flow ≥40%
        cvd_4h = str(f.get('cvd_price_cross_4h', '')).upper()
        if cvd_4h in ('ACCUMULATION', 'DISTRIBUTION', 'ABSORPTION_BUY', 'ABSORPTION_SELL', 'CONFIRMED_SELL'):
            w_f = max(w_f, 0.40)

        # BB Squeeze amplification: squeeze active → Flow ×1.5
        bb_trend_4h = str(f.get('bb_width_4h_trend_5bar', '')).upper()
        squeeze_active = bb_trend_4h == 'FALLING'
        if squeeze_active and w_f > 0:
            w_f *= 1.5

        # Normalize
        total_w = w_s + w_d + w_f
        if total_w > 0:
            w_s, w_d, w_f = w_s / total_w, w_d / total_w, w_f / total_w
        else:
            w_s, w_d, w_f = 0.40, 0.30, 0.30

        # Weighted composite — damping applied per-dimension.
        # v47.0: Relaxed denominators (/2, /2, /3) + inactive weight redistribution.
        # When a dimension has 0 votes (e.g., Divergence=0 in 58% of snapshots),
        # its weight is redistributed to active dimensions so net_raw isn't capped.
        _s_damp = min(1.0, len(structure_votes) / 2) if structure_votes else 0
        _d_damp = min(1.0, len(div_votes) / 2) if div_votes else 0
        _f_damp = min(1.0, len(flow_votes) / 3) if flow_votes else 0

        # Redistribute weight of inactive dimensions to active ones
        _ew_s = w_s if _s_damp > 0 else 0
        _ew_d = w_d if _d_damp > 0 else 0
        _ew_f = w_f if _f_damp > 0 else 0
        _ew_total = _ew_s + _ew_d + _ew_f
        if _ew_total > 0 and _ew_total < 0.999:
            # Proportionally redistribute dead weight to active dimensions
            _ew_s = _ew_s / _ew_total if _ew_s > 0 else 0
            _ew_d = _ew_d / _ew_total if _ew_d > 0 else 0
            _ew_f = _ew_f / _ew_total if _ew_f > 0 else 0
        elif _ew_total > 0:
            _ew_s, _ew_d, _ew_f = w_s, w_d, w_f

        net_raw = (structure_raw * _s_damp * _ew_s
                   + divergence_raw * _d_damp * _ew_d
                   + flow_raw * _f_damp * _ew_f)
        net_raw = max(-1.0, min(1.0, net_raw))

        # ========================================
        # Trend context (non-decision, position sizing only)
        # ========================================
        adx_dir = str(f.get('adx_direction_1d', 'NEUTRAL')).upper()
        # Determine anticipatory direction from net_raw
        if net_raw > 0.1:
            ant_dir = 'BULLISH'
        elif net_raw < -0.1:
            ant_dir = 'BEARISH'
        else:
            ant_dir = 'NEUTRAL'

        if adx_dir == ant_dir and ant_dir != 'NEUTRAL':
            trend_context = 'CONFIRMING'
        elif adx_dir == 'NEUTRAL' or adx_1d < 20:
            trend_context = 'NEUTRAL'
        elif ant_dir == 'NEUTRAL':
            trend_context = 'NEUTRAL'
        else:
            trend_context = 'OPPOSING'

        # ========================================
        # Vol/Extension risk + Risk environment (reuse from existing)
        # ========================================
        _ext_map = {'NORMAL': 1, 'EXTENDED': 3, 'OVEREXTENDED': 6, 'EXTREME': 9}
        _vol_map = {'LOW': 1, 'NORMAL': 2, 'HIGH': 5, 'EXTREME': 8}
        ext_risk = max(
            _ext_map.get(f.get('extension_regime_30m', 'NORMAL'), 2),
            _ext_map.get(f.get('extension_regime_4h', 'NORMAL'), 1),
            _ext_map.get(ext_regime_1d, 1),
        )
        vol_risk = max(
            _vol_map.get(f.get('volatility_regime_30m', 'NORMAL'), 2),
            _vol_map.get(f.get('volatility_regime_4h', 'NORMAL'), 1),
            _vol_map.get(vol_regime, 1),
        )
        vol_ext_score = min(10, max(ext_risk, vol_risk))

        # Risk score
        risk_score = 2
        fr = sg('funding_rate_pct')
        if _avail_derivatives and abs(fr) > 0.05:
            risk_score += 3
        elif _avail_derivatives and abs(fr) > 0.02:
            risk_score += 1
        _avail_sentiment = f.get('_avail_sentiment', True)
        _sentiment_ok = _avail_sentiment and not f.get('sentiment_degraded', False)
        long_ratio = sg('long_ratio', 0.5)
        if _sentiment_ok and (long_ratio > 0.7 or long_ratio < 0.3):
            risk_score += 2
        oi_trend_str = str(f.get('oi_trend', '')).upper()
        if oi_trend_str == 'RISING':
            risk_score += 1
        liq_bias_str = str(f.get('liquidation_bias', '')).upper()
        if liq_bias_str in ('LONG_DOMINANT', 'SHORT_DOMINANT'):
            risk_score += 1
        # OBI extreme — orderbook imbalance = slippage risk
        _avail_orderbook = f.get('_avail_orderbook', True)
        obi = sg('obi_weighted')
        if _avail_orderbook and abs(obi) > 0.6:
            risk_score += 2
        elif _avail_orderbook and abs(obi) > 0.4:
            risk_score += 1
        _avail_account = f.get('_avail_account', True)
        liq_buffer = sg('liquidation_buffer_pct', 100.0)
        if _avail_account and 0 <= liq_buffer < 5:
            risk_score += 3
        elif _avail_account and 0 < liq_buffer < 10:
            risk_score += 1
        risk_score = min(10, risk_score)

        # Aligned count
        aligned = sum(1 for d in [structure_dir, divergence_dir, flow_dir]
                       if d in ('BULLISH', 'BEARISH') and d == (
                           'BULLISH' if net_raw > 0 else 'BEARISH'))

        return {
            "structure": {"score": structure_score, "direction": structure_dir, "raw": structure_raw},
            "divergence": {"score": divergence_score, "direction": divergence_dir, "raw": divergence_raw},
            "order_flow": {"score": flow_score, "direction": flow_dir, "raw": flow_raw},
            "vol_ext_risk": {"score": vol_ext_score},
            "risk_env": {"score": risk_score, "level": "HIGH" if risk_score >= 6 else ("MODERATE" if risk_score >= 4 else "LOW")},
            "anticipatory_raw": net_raw,
            "regime": regime,
            "trend_context": trend_context,
            "aligned": aligned,
            "weights_applied": {"structure": round(w_s, 3), "divergence": round(w_d, 3), "order_flow": round(w_f, 3)},
            "squeeze_active": squeeze_active,
        }









    def _calculate_sr_zones(
        self,
        current_price: float,
        technical_data: Optional[Dict[str, Any]],
        orderbook_data: Optional[Dict[str, Any]],
        bars_data: Optional[List[Dict[str, Any]]] = None,
        bars_data_4h: Optional[List[Dict[str, Any]]] = None,
        bars_data_1d: Optional[List[Dict[str, Any]]] = None,
        daily_bar: Optional[Dict[str, Any]] = None,
        weekly_bar: Optional[Dict[str, Any]] = None,
        atr_value: Optional[float] = None,
        order_flow_report: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Calculate S/R Zones from multiple data sources (v3.0, v4.0).

        Combines:
        - Bollinger Bands (BB Upper/Lower)
        - SMA (SMA_50, SMA_200)
        - Order Book Walls (bid/ask anomalies)
        - v3.0: Swing Points (from OHLC bars)
        - v3.0: ATR-adaptive clustering
        - v3.0: Touch Count scoring
        - v4.0: MTF swing detection (4H, 1D)
        - v4.0: Pivot Points (Daily + Weekly)
        - v4.0: Volume Profile (VPOC, VAH, VAL)

        Parameters
        ----------
        current_price : float
            Current market price
        technical_data : Dict, optional
            Technical indicator data containing BB and SMA values
        orderbook_data : Dict, optional
            Order book data containing anomalies (walls)
        bars_data : List[Dict], optional
            v3.0: OHLC bar data for swing detection and touch count
            [{'high': float, 'low': float, 'close': float}, ...]
        bars_data_4h : List[Dict], optional
            v4.0: 4H OHLCV bars for MTF swing detection
        bars_data_1d : List[Dict], optional
            v4.0: 1D OHLCV bars for MTF swing detection
        daily_bar : Dict, optional
            v4.0: Most recent completed daily bar for pivot calculation
        weekly_bar : Dict, optional
            v4.0: Aggregated weekly bar for pivot calculation
        atr_value : float, optional
            v4.0: ATR value for buffer calculation

        Returns
        -------
        Dict
            S/R zones result from SRZoneCalculator
        """
        if current_price <= 0:
            return self.sr_calculator._empty_result()

        # Extract BB data
        bb_data = None
        if technical_data:
            bb_upper = technical_data.get('bb_upper')
            bb_lower = technical_data.get('bb_lower')
            bb_middle = technical_data.get('bb_middle')
            if bb_upper and bb_lower:
                bb_data = {
                    'upper': bb_upper,
                    'lower': bb_lower,
                    'middle': bb_middle,
                }

        # Extract SMA data
        sma_data = None
        if technical_data:
            sma_50 = technical_data.get('sma_50')
            sma_200 = technical_data.get('sma_200')
            if sma_50 or sma_200:
                sma_data = {
                    'sma_50': sma_50,
                    'sma_200': sma_200,
                }

        # Extract Order Book anomalies (walls)
        orderbook_anomalies = None
        if orderbook_data:
            anomalies = orderbook_data.get('anomalies', {})
            if anomalies:
                orderbook_anomalies = {
                    'bid_anomalies': anomalies.get('bid_anomalies', []),
                    'ask_anomalies': anomalies.get('ask_anomalies', []),
                }

        # Phase 1.1: Inject taker_buy_volume into bars_data.
        # indicator_manager.get_kline_data() only returns {timestamp,open,high,low,close,volume}.
        # order_flow_report['recent_10_bars'] contains per-bar buy ratios for the last 10 bars;
        # approximate taker_buy_volume = volume × buy_ratio for those bars.
        if bars_data and order_flow_report and isinstance(order_flow_report, dict):
            per_bar_ratios = order_flow_report.get('recent_10_bars', [])
            if per_bar_ratios:
                n_inject = min(len(per_bar_ratios), len(bars_data))
                # Align: per_bar_ratios[-n_inject:] maps to bars_data[-n_inject:]
                offset = len(bars_data) - n_inject
                for _idx, _ratio in enumerate(per_bar_ratios[-n_inject:]):
                    _bar = bars_data[offset + _idx]
                    # Only inject if taker_buy_volume is missing (avoid overwriting real data)
                    if 'taker_buy_volume' not in _bar:
                        _bar = dict(_bar)  # shallow copy — do NOT mutate caller's list
                        _bar['taker_buy_volume'] = _bar.get('volume', 0) * float(_ratio)
                        bars_data = bars_data[:offset + _idx] + [_bar] + bars_data[offset + _idx + 1:]

        # Calculate S/R zones (v3.0: bars_data for swing/touch)
        # v4.0: Pass MTF bars for pivot points + volume profile
        # v8.1: Pass technical_data + orderbook_data for hold_probability real-time correction
        try:
            result = self.sr_calculator.calculate(
                current_price=current_price,
                bb_data=bb_data,
                sma_data=sma_data,
                orderbook_anomalies=orderbook_anomalies,
                bars_data=bars_data,
                bars_data_4h=bars_data_4h,
                bars_data_1d=bars_data_1d,
                daily_bar=daily_bar,
                weekly_bar=weekly_bar,
                atr_value=atr_value,
                technical_data=technical_data,
                orderbook_data=orderbook_data,
            )

            # Log S/R zone detection
            if result.get('nearest_resistance'):
                r = result['nearest_resistance']
                swing_tag = " [Swing]" if r.has_swing_point else ""
                touch_tag = f" [T:{r.touch_count}]" if r.touch_count > 0 else ""
                self.logger.debug(
                    f"S/R Zone: Nearest Resistance ${r.price_center:,.0f} "
                    f"({r.distance_pct:.1f}% away) [{r.strength}]{swing_tag}{touch_tag}"
                )
            if result.get('nearest_support'):
                s = result['nearest_support']
                swing_tag = " [Swing]" if s.has_swing_point else ""
                touch_tag = f" [T:{s.touch_count}]" if s.touch_count > 0 else ""
                self.logger.debug(
                    f"S/R Zone: Nearest Support ${s.price_center:,.0f} "
                    f"({s.distance_pct:.1f}% away) [{s.strength}]{swing_tag}{touch_tag}"
                )

            return result

        except Exception as e:
            self.logger.warning(f"S/R zone calculation failed: {e}")
            return self.sr_calculator._empty_result()

    @staticmethod
    def _ema_smooth(series: list, period: int = 20) -> list:
        """v20.0: Apply EMA smoothing to a series. Pure Python, no pandas."""
        if not series or len(series) < 2:
            return series
        multiplier = 2.0 / (period + 1)
        ema = [series[0]]
        for i in range(1, len(series)):
            ema.append(series[i] * multiplier + ema[-1] * (1 - multiplier))
        return ema

    def _detect_divergences(
        self,
        price_series: list,
        rsi_series: list = None,
        macd_hist_series: list = None,
        obv_series: list = None,
        timeframe: str = "4H",
    ) -> list:
        """
        v19.1: Pre-compute divergences between price and momentum indicators.
        v20.0: Added OBV divergence detection.
        v10.0: Replaced hand-written peak detection with scipy.signal.find_peaks.

        Detects:
        - Bullish divergence: price makes lower low, indicator makes higher low
        - Bearish divergence: price makes higher high, indicator makes lower high

        Parameters
        ----------
        price_series : list
            Price values (oldest → newest)
        rsi_series : list, optional
            RSI values (same length as price_series)
        macd_hist_series : list, optional
            MACD histogram values (same length as price_series)
        obv_series : list, optional
            EMA-smoothed OBV values (same length as price_series) (v20.0)
        timeframe : str
            Label for the timeframe (e.g., "4H", "30M")

        Returns
        -------
        list of str
            Divergence annotation strings, empty if none detected
        """
        from scipy.signal import find_peaks as _find_peaks
        import numpy as _np

        tags = []
        min_points = 5

        if not price_series or len(price_series) < min_points:
            return tags

        def find_local_extremes(series, window=2):
            """Find local highs and lows using scipy.signal.find_peaks."""
            arr = _np.array(series, dtype=float)
            # Peaks (highs): find_peaks with distance=window
            high_idx, _ = _find_peaks(arr, distance=window)
            highs = [(int(i), float(arr[i])) for i in high_idx]
            # Troughs (lows): find_peaks on inverted series
            low_idx, _ = _find_peaks(-arr, distance=window)
            lows = [(int(i), float(arr[i])) for i in low_idx]
            return highs, lows

        price_highs, price_lows = find_local_extremes(price_series)

        def check_divergence(indicator_series, indicator_name):
            """Check for divergences between price and an indicator."""
            if not indicator_series or len(indicator_series) != len(price_series):
                return
            ind_highs, ind_lows = find_local_extremes(indicator_series)
            # v20.0: OBV uses integer format and custom descriptions
            if "OBV" in indicator_name:
                ind_fmt = ",.0f"
                bearish_desc = "volume not confirming price rise, distribution likely"
                bullish_desc = "accumulation despite price decline, smart money buying"
            elif "MACD" in indicator_name:
                ind_fmt = ".4f"
                bearish_desc = "momentum weakening despite price rise"
                bullish_desc = "selling exhaustion, reversal signal"
            else:
                ind_fmt = ".1f"
                bearish_desc = "momentum weakening despite price rise"
                bullish_desc = "selling exhaustion, reversal signal"

            # Bearish divergence: price higher high + indicator lower high
            if len(price_highs) >= 2 and len(ind_highs) >= 2:
                ph1, ph2 = price_highs[-2], price_highs[-1]
                # Find indicator highs closest to these price highs
                ih_candidates_1 = [(i, v) for i, v in ind_highs if abs(i - ph1[0]) <= 2]
                ih_candidates_2 = [(i, v) for i, v in ind_highs if abs(i - ph2[0]) <= 2]
                if ih_candidates_1 and ih_candidates_2:
                    ih1 = ih_candidates_1[-1]
                    ih2 = ih_candidates_2[-1]
                    if ph2[1] > ph1[1] and ih2[1] < ih1[1]:
                        tags.append(
                            f"→ [DIVERGENCE: {timeframe} BEARISH — Price higher high "
                            f"(${ph1[1]:,.0f}→${ph2[1]:,.0f}) but {indicator_name} lower high "
                            f"({ih1[1]:{ind_fmt}}→{ih2[1]:{ind_fmt}}) — {bearish_desc}]"
                        )

            # Bullish divergence: price lower low + indicator higher low
            if len(price_lows) >= 2 and len(ind_lows) >= 2:
                pl1, pl2 = price_lows[-2], price_lows[-1]
                il_candidates_1 = [(i, v) for i, v in ind_lows if abs(i - pl1[0]) <= 2]
                il_candidates_2 = [(i, v) for i, v in ind_lows if abs(i - pl2[0]) <= 2]
                if il_candidates_1 and il_candidates_2:
                    il1 = il_candidates_1[-1]
                    il2 = il_candidates_2[-1]
                    if pl2[1] < pl1[1] and il2[1] > il1[1]:
                        tags.append(
                            f"→ [DIVERGENCE: {timeframe} BULLISH — Price lower low "
                            f"(${pl1[1]:,.0f}→${pl2[1]:,.0f}) but {indicator_name} higher low "
                            f"({il1[1]:{ind_fmt}}→{il2[1]:{ind_fmt}}) — {bullish_desc}]"
                        )

        if rsi_series:
            check_divergence(rsi_series, "RSI")
        if macd_hist_series:
            check_divergence(macd_hist_series, "MACD Hist")
        if obv_series:
            check_divergence(obv_series, "OBV")

        return tags


    def extract_features(
        self,
        technical_data: Dict[str, Any],
        sentiment_data: Optional[Dict] = None,
        order_flow_data: Optional[Dict] = None,
        order_flow_4h: Optional[Dict] = None,
        derivatives_data: Optional[Dict] = None,
        binance_derivatives: Optional[Dict] = None,
        orderbook_data: Optional[Dict] = None,
        sr_zones: Optional[Dict] = None,
        current_position: Optional[Dict] = None,
        account_context: Optional[Dict] = None,
        fear_greed_report: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Extract fixed-schema feature dict from raw data sources.

        Returns dict conforming to FEATURE_SCHEMA.
        Missing data sources -> default values (0.0 for float, "NONE" for enum).

        Deterministic: same raw data -> same feature dict (no randomness).
        Safe defaults: missing/degraded data -> neutral defaults, never raises.
        Pre-computes all derived features (divergences, CVD cross, regime, reliability).
        Does NOT call _format_*_report() — parallel path, not replacement.
        """
        from agents.prompt_constants import FEATURE_SCHEMA, _get_multiplier

        def _sf(d, key, default=0.0):
            """Safe float extraction."""
            if not d:
                return default
            v = d.get(key)
            if v is None:
                return default
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

        def _se(d, key, valid_values, default="NONE"):
            """Safe enum extraction."""
            if not d:
                return default
            v = d.get(key, default)
            if v is None:
                return default
            v_upper = str(v).upper()
            if v_upper in valid_values:
                return v_upper
            return default

        td = technical_data or {}
        mtf_dec = td.get('mtf_decision_layer') or {}
        mtf_trend = td.get('mtf_trend_layer') or {}
        hist_ctx = td.get('historical_context') or {}
        hist_ctx_4h = mtf_dec.get('historical_context') or {}  # v34.3: top-level for CVD-Price cross
        hist_ctx_1d = mtf_trend.get('historical_context') or {}  # v36.0: 1D historical context

        features = {}

        # ── Data Availability Flags (v34.1) ──
        # Distinguish "data says neutral" from "data is missing"
        features['_avail_order_flow'] = order_flow_data is not None
        features['_avail_derivatives'] = derivatives_data is not None
        features['_avail_binance_derivatives'] = binance_derivatives is not None
        features['_avail_orderbook'] = orderbook_data is not None
        features['_avail_mtf_4h'] = mtf_dec is not None and bool(mtf_dec)
        features['_avail_mtf_1d'] = mtf_trend is not None and bool(mtf_trend)
        features['_avail_account'] = account_context is not None
        features['_avail_sr_zones'] = sr_zones is not None
        features['_avail_sentiment'] = (sentiment_data is not None
                                        and not bool((sentiment_data or {}).get('degraded')))
        # technical_data, price_data: always available

        # ── 30M Execution Layer ──
        try:
            features["price"] = _sf(td, 'price')
            features["rsi_30m"] = _sf(td, 'rsi')
            features["macd_30m"] = _sf(td, 'macd')
            features["macd_signal_30m"] = _sf(td, 'macd_signal')
            features["macd_histogram_30m"] = _sf(td, 'macd_histogram')
            features["adx_30m"] = _sf(td, 'adx')
            features["di_plus_30m"] = _sf(td, 'di_plus')
            features["di_minus_30m"] = _sf(td, 'di_minus')
            features["bb_position_30m"] = _sf(td, 'bb_position')
            features["bb_upper_30m"] = _sf(td, 'bb_upper')
            features["bb_lower_30m"] = _sf(td, 'bb_lower')
            features["sma_5_30m"] = _sf(td, 'sma_5')     # v36.0 FIX: 30M has sma_periods=[5,20]
            features["sma_20_30m"] = _sf(td, 'sma_20')
            features["volume_ratio_30m"] = _sf(td, 'volume_ratio')
            # v29.2: 30M EMA + ATR%
            features["atr_pct_30m"] = _sf(td, 'atr_pct')
            # Production base indicator_manager uses ema_periods=[macd_fast, macd_slow]=[12,26]
            # (NOT the MTF execution_layer config [10]). So technical_data has ema_12/ema_26.
            features["ema_12_30m"] = _sf(td, 'ema_12')
            features["ema_26_30m"] = _sf(td, 'ema_26')
        except Exception as _30m_err:
            self.logger.warning(f"30M core feature extraction failed: {_30m_err}")

        # ── v2.0 Phase 1: log_return for HMM Regime detection ──
        try:
            import math as _math
            # 30M log_return from historical_context (last 2 closes)
            hc_series = hist_ctx.get('time_series', [])
            if len(hc_series) >= 2:
                c_cur = float(hc_series[-1].get('close', 0) or 0)
                c_prev = float(hc_series[-2].get('close', 0) or 0)
                if c_cur > 0 and c_prev > 0:
                    features["log_return_30m"] = _math.log(c_cur / c_prev)
            # 4H log_return
            hc4_series = hist_ctx_4h.get('time_series', [])
            if len(hc4_series) >= 2:
                c4_cur = float(hc4_series[-1].get('close', 0) or 0)
                c4_prev = float(hc4_series[-2].get('close', 0) or 0)
                if c4_cur > 0 and c4_prev > 0:
                    features["log_return_4h"] = _math.log(c4_cur / c4_prev)
        except Exception as e:
            logger.debug(f"log_return_4h extraction failed: {e}")

        # ── 4H Decision Layer ──
        try:
            features["rsi_4h"] = _sf(mtf_dec, 'rsi')
            features["macd_4h"] = _sf(mtf_dec, 'macd')
            features["macd_signal_4h"] = _sf(mtf_dec, 'macd_signal')
            features["macd_histogram_4h"] = _sf(mtf_dec, 'macd_histogram')
            features["adx_4h"] = _sf(mtf_dec, 'adx')
            features["di_plus_4h"] = _sf(mtf_dec, 'di_plus')
            features["di_minus_4h"] = _sf(mtf_dec, 'di_minus')
            features["bb_position_4h"] = _sf(mtf_dec, 'bb_position')
            features["bb_upper_4h"] = _sf(mtf_dec, 'bb_upper')
            features["bb_lower_4h"] = _sf(mtf_dec, 'bb_lower')
            features["sma_20_4h"] = _sf(mtf_dec, 'sma_20')
            features["sma_50_4h"] = _sf(mtf_dec, 'sma_50')
            features["volume_ratio_4h"] = _sf(mtf_dec, 'volume_ratio')
            # v29.2: 4H ATR, EMA, extension, volatility
            features["atr_4h"] = _sf(mtf_dec, 'atr')
            features["atr_pct_4h"] = _sf(mtf_dec, 'atr_pct')
            features["ema_12_4h"] = _sf(mtf_dec, 'ema_12')
            features["ema_26_4h"] = _sf(mtf_dec, 'ema_26')
            features["extension_ratio_4h"] = _sf(mtf_dec, 'extension_ratio_sma_20')
            features["extension_regime_4h"] = _se(mtf_dec, 'extension_regime',
                                                   {"NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"})
            features["volatility_regime_4h"] = _se(mtf_dec, 'volatility_regime',
                                                    {"LOW", "NORMAL", "HIGH", "EXTREME"})
            features["volatility_percentile_4h"] = _sf(mtf_dec, 'volatility_percentile')
        except Exception:
            self.logger.debug("Feature extraction: 4H decision layer partial failure")

        # ── 1D Trend Layer ──
        try:
            features["adx_1d"] = _sf(mtf_trend, 'adx')
            features["di_plus_1d"] = _sf(mtf_trend, 'di_plus')
            features["di_minus_1d"] = _sf(mtf_trend, 'di_minus')
            features["rsi_1d"] = _sf(mtf_trend, 'rsi')
            features["macd_1d"] = _sf(mtf_trend, 'macd')
            features["macd_signal_1d"] = _sf(mtf_trend, 'macd_signal')
            features["macd_histogram_1d"] = _sf(mtf_trend, 'macd_histogram')
            features["sma_200_1d"] = _sf(mtf_trend, 'sma_200')
            # v29.2: 1D BB, vol, ATR, EMA, extension, volatility
            features["bb_position_1d"] = _sf(mtf_trend, 'bb_position')
            features["volume_ratio_1d"] = _sf(mtf_trend, 'volume_ratio')
            features["atr_1d"] = _sf(mtf_trend, 'atr')
            features["atr_pct_1d"] = _sf(mtf_trend, 'atr_pct')
            features["ema_12_1d"] = _sf(mtf_trend, 'ema_12')
            features["ema_26_1d"] = _sf(mtf_trend, 'ema_26')
            features["extension_ratio_1d"] = _sf(mtf_trend, 'extension_ratio_sma_200')
            features["extension_regime_1d"] = _se(mtf_trend, 'extension_regime',
                                                   {"NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"})
            features["volatility_regime_1d"] = _se(mtf_trend, 'volatility_regime',
                                                    {"LOW", "NORMAL", "HIGH", "EXTREME"})
            features["volatility_percentile_1d"] = _sf(mtf_trend, 'volatility_percentile')
        except Exception:
            self.logger.debug("Feature extraction: 1D trend layer partial failure")

        # ── Risk Context (30M, suffixed to match 4H/1D convention) ──
        try:
            features["extension_ratio_30m"] = _sf(td, 'extension_ratio_sma_20')
            features["extension_regime_30m"] = _se(td, 'extension_regime',
                                                    {"NORMAL", "EXTENDED", "OVEREXTENDED", "EXTREME"})
            features["volatility_regime_30m"] = _se(td, 'volatility_regime',
                                                     {"LOW", "NORMAL", "HIGH", "EXTREME"})
            features["volatility_percentile_30m"] = _sf(td, 'volatility_percentile')
            features["atr_30m"] = _sf(td, 'atr')
        except Exception:
            self.logger.debug("Feature extraction: risk context partial failure")

        # ── Market Regime (pre-computed from ADX) ──
        # v39.0: Use max(1D, 4H) ADX for regime determination.
        # Rationale: 4H is the decision layer — if 4H shows strong trend
        # (ADX>40) but 1D hasn't caught up (ADX<25), the market IS trending
        # at the actionable timeframe. Using only 1D would misclassify as
        # RANGING and cause anticipatory scores to underweight trend signals.
        try:
            adx_1d = features.get("adx_1d", 0.0)  # 0.0 = data absent → RANGING (safe default)
            adx_4h = features.get("adx_4h", 0.0)
            effective_adx = max(adx_1d, adx_4h)

            # v2.0: HMM regime takes priority when available (from analyze() → hmm_regime_result)
            hmm_regime = features.get("hmm_regime")
            if hmm_regime and hmm_regime != "UNKNOWN":
                # Map HMM 4-state to existing 3-state enum for downstream compatibility
                _hmm_to_regime = {
                    "TRENDING_UP": "STRONG_TREND",
                    "TRENDING_DOWN": "STRONG_TREND",
                    "RANGING": "RANGING",
                    "HIGH_VOLATILITY": "WEAK_TREND",
                    # ADX fallback labels pass through directly
                    "STRONG_TREND": "STRONG_TREND",
                    "WEAK_TREND": "WEAK_TREND",
                }
                features["market_regime"] = _hmm_to_regime.get(hmm_regime, "RANGING")
                hmm_conf = features.get("hmm_regime_confidence", 0)
                hmm_src = features.get("hmm_regime_source", "unknown")
                self.logger.info(
                    f"Market regime: HMM={hmm_regime} → {features['market_regime']} "
                    f"(source={hmm_src}, conf={hmm_conf:.2f}, ADX={effective_adx:.1f})"
                )
            else:
                # Fallback: ADX threshold (pre-HMM behavior)
                if effective_adx >= 40:
                    features["market_regime"] = "STRONG_TREND"
                elif effective_adx >= 25:
                    features["market_regime"] = "WEAK_TREND"
                else:
                    features["market_regime"] = "RANGING"
                adx_source = "4H" if adx_4h >= adx_1d else "1D"
                self.logger.info(
                    f"Market regime: ADX fallback max(1D={adx_1d:.1f}, 4H={adx_4h:.1f}) "
                    f"= {effective_adx:.1f} (source={adx_source}) -> {features['market_regime']}"
                )

            di_p_1d = features.get("di_plus_1d", 0)
            di_m_1d = features.get("di_minus_1d", 0)
            # v36.2: Three-state direction — equal DI (including both 0 when
            # 1D data unavailable) must produce NEUTRAL, not spurious BEARISH.
            # tag_validator.py already maps NEUTRAL → TREND_1D_NEUTRAL (line 178).
            if di_p_1d > di_m_1d:
                features["adx_direction_1d"] = "BULLISH"
            elif di_m_1d > di_p_1d:
                features["adx_direction_1d"] = "BEARISH"
            else:
                features["adx_direction_1d"] = "NEUTRAL"
        except Exception:
            features.setdefault("market_regime", "RANGING")
            features.setdefault("adx_direction_1d", "NEUTRAL")

        # ── Pre-computed Categorical (v31.0) ──
        # MACD crossover: avoid LLM comparing negative floats
        try:
            for suffix, macd_key, sig_key in [
                ("30m", "macd_30m", "macd_signal_30m"),
                ("4h",  "macd_4h",  "macd_signal_4h"),
                ("1d",  "macd_1d",  "macd_signal_1d"),
            ]:
                macd_val = features.get(macd_key, 0.0)
                macd_sig = features.get(sig_key, 0.0)
                diff = macd_val - macd_sig
                # Use ATR-relative threshold for NEUTRAL zone
                atr_ref = features.get(f"atr_pct_{suffix}", 0.0)
                threshold = atr_ref * 0.01 if atr_ref > 0 else 0.0
                if diff > threshold:
                    features[f"macd_cross_{suffix}"] = "BULLISH"
                elif diff < -threshold:
                    features[f"macd_cross_{suffix}"] = "BEARISH"
                else:
                    features[f"macd_cross_{suffix}"] = "NEUTRAL"
        except Exception:
            for s in ("30m", "4h", "1d"):
                features.setdefault(f"macd_cross_{s}", "NEUTRAL")

        # DI direction: pre-compare DI+/DI- for 30M and 4H
        try:
            for suffix, dp_key, dm_key in [
                ("30m", "di_plus_30m", "di_minus_30m"),
                ("4h",  "di_plus_4h",  "di_minus_4h"),
            ]:
                di_p = features.get(dp_key, 0.0)
                di_m = features.get(dm_key, 0.0)
                if di_p == 0.0 and di_m == 0.0:
                    features[f"di_direction_{suffix}"] = "NEUTRAL"
                elif di_p > di_m:
                    features[f"di_direction_{suffix}"] = "BULLISH"
                else:
                    features[f"di_direction_{suffix}"] = "BEARISH"
        except Exception:
            for s in ("30m", "4h"):
                features.setdefault(f"di_direction_{s}", "NEUTRAL")

        # RSI zone: categorical from raw RSI value
        try:
            for suffix, rsi_key in [("30m", "rsi_30m"), ("4h", "rsi_4h"), ("1d", "rsi_1d")]:
                rsi_val = features.get(rsi_key, 50.0)
                if rsi_val < 30:
                    features[f"rsi_zone_{suffix}"] = "OVERSOLD"
                elif rsi_val > 70:
                    features[f"rsi_zone_{suffix}"] = "OVERBOUGHT"
                else:
                    features[f"rsi_zone_{suffix}"] = "NEUTRAL"
        except Exception:
            for s in ("30m", "4h", "1d"):
                features.setdefault(f"rsi_zone_{s}", "NEUTRAL")

        # ── Divergences (pre-computed) ──
        try:
            # 4H divergences use mtf_decision_layer's historical_context (not 30M hist_ctx)
            # hist_ctx_4h already defined at top scope (v34.3)
            price_4h = hist_ctx_4h.get('price_trend', [])
            rsi_4h = hist_ctx_4h.get('rsi_trend', [])
            macd_4h = hist_ctx_4h.get('macd_histogram_trend', [])
            obv_4h = hist_ctx_4h.get('obv_trend', [])

            div_4h = self._detect_divergences(
                price_series=price_4h, rsi_series=rsi_4h,
                macd_hist_series=macd_4h,
                obv_series=obv_4h if len(obv_4h) >= 5 else None,
                timeframe="4H"
            ) if price_4h and len(price_4h) >= 5 else []

            features["rsi_divergence_4h"] = "NONE"
            features["macd_divergence_4h"] = "NONE"
            features["obv_divergence_4h"] = "NONE"
            for tag in div_4h:
                tag_upper = tag.upper()
                if "RSI" in tag_upper:
                    features["rsi_divergence_4h"] = "BEARISH" if "BEARISH" in tag_upper else "BULLISH"
                elif "MACD" in tag_upper:
                    features["macd_divergence_4h"] = "BEARISH" if "BEARISH" in tag_upper else "BULLISH"
                elif "OBV" in tag_upper:
                    features["obv_divergence_4h"] = "BEARISH" if "BEARISH" in tag_upper else "BULLISH"

            price_30m = hist_ctx.get('price_trend', [])
            rsi_30m = hist_ctx.get('rsi_trend', [])
            macd_30m = hist_ctx.get('macd_histogram_trend', [])
            obv_30m = hist_ctx.get('obv_trend', [])  # v36.0 FIX: was 'obv_ema_trend' (non-existent key)

            div_30m = self._detect_divergences(
                price_series=price_30m, rsi_series=rsi_30m,
                macd_hist_series=macd_30m,
                obv_series=obv_30m if len(obv_30m) >= 5 else None,
                timeframe="30M"
            ) if price_30m and len(price_30m) >= 5 else []

            features["rsi_divergence_30m"] = "NONE"
            features["macd_divergence_30m"] = "NONE"
            features["obv_divergence_30m"] = "NONE"
            for tag in div_30m:
                tag_upper = tag.upper()
                if "RSI" in tag_upper:
                    features["rsi_divergence_30m"] = "BEARISH" if "BEARISH" in tag_upper else "BULLISH"
                elif "OBV" in tag_upper:
                    features["obv_divergence_30m"] = "BEARISH" if "BEARISH" in tag_upper else "BULLISH"
                elif "MACD" in tag_upper:
                    features["macd_divergence_30m"] = "BEARISH" if "BEARISH" in tag_upper else "BULLISH"
        except Exception:
            for k in ("rsi_divergence_4h", "macd_divergence_4h", "obv_divergence_4h",
                      "rsi_divergence_30m", "macd_divergence_30m", "obv_divergence_30m"):
                features.setdefault(k, "NONE")

        # ── Order Flow ──
        try:
            of = order_flow_data or {}
            features["cvd_trend_30m"] = _se(of, 'cvd_trend',
                                            {"POSITIVE", "NEGATIVE", "NEUTRAL"}, "NEUTRAL")
            features["buy_ratio_30m"] = _sf(of, 'buy_ratio')
            features["cvd_cumulative_30m"] = _sf(of, 'cvd_cumulative')
            # v34.3: Compute CVD-Price cross inline (OrderFlowProcessor doesn't produce this field)
            # Mirrors _format_order_flow_report() logic: CVD net (5-bar) vs price change (5-bar)
            features["cvd_price_cross_30m"] = self._compute_cvd_price_cross(
                of.get('cvd_history', []),
                hist_ctx.get('price_trend', []),
            )
        except Exception:
            self.logger.debug("Feature extraction: order flow partial failure")

        # ── 4H CVD ──
        try:
            of4 = order_flow_4h or {}
            features["cvd_trend_4h"] = _se(of4, 'cvd_trend',
                                           {"POSITIVE", "NEGATIVE", "NEUTRAL"}, "NEUTRAL")
            features["buy_ratio_4h"] = _sf(of4, 'buy_ratio')
            # v34.3: Compute CVD-Price cross inline (same fix as 30M above)
            features["cvd_price_cross_4h"] = self._compute_cvd_price_cross(
                of4.get('cvd_history', []),
                hist_ctx_4h.get('price_trend', []) if hist_ctx_4h else [],
            )
        except Exception:
            self.logger.debug("Feature extraction: 4H CVD partial failure")

        # ── Derivatives (Coinalyze + Binance FR merged) ──
        try:
            dd = derivatives_data or {}
            # funding_rate is injected by ai_data_assembler into derivatives_report
            fr_data = dd.get('funding_rate') or {}
            features["funding_rate_pct"] = _sf(fr_data, 'current_pct')
            features["funding_rate_trend"] = _se(fr_data, 'trend',
                                                  {"RISING", "FALLING", "STABLE"}, "STABLE")
            # OI trend lives in dd['trends']['oi_trend'] (from fetch_all_with_history)
            _trends = dd.get('trends') or {}
            _oi_trend_val = str(_trends.get('oi_trend', 'STABLE')).upper()
            features["oi_trend"] = _oi_trend_val if _oi_trend_val in {"RISING", "FALLING", "STABLE"} else "STABLE"
            # Liquidation bias: compute from raw history {l: long_btc, s: short_btc}
            _liq = dd.get('liquidations') or {}
            _liq_history = _liq.get('history', []) if isinstance(_liq, dict) else []
            _total_long = sum(float(h.get('l', 0)) for h in _liq_history) if _liq_history else 0
            _total_short = sum(float(h.get('s', 0)) for h in _liq_history) if _liq_history else 0
            _total_liq = _total_long + _total_short
            if _total_liq > 0:
                _long_pct = _total_long / _total_liq
                if _long_pct > 0.6:
                    features["liquidation_bias"] = "LONG_DOMINANT"
                elif _long_pct < 0.4:
                    features["liquidation_bias"] = "SHORT_DOMINANT"
                else:
                    features["liquidation_bias"] = "BALANCED"
            else:
                features["liquidation_bias"] = "NONE"
            features["premium_index"] = _sf(fr_data, 'premium_index')
        except Exception:
            self.logger.debug("Feature extraction: derivatives partial failure")

        # ── FR Direction (depends on funding_rate_pct from derivatives above) ──
        try:
            fr_val = features.get("funding_rate_pct", 0.0)
            if fr_val > 0.005:
                features["fr_direction"] = "POSITIVE"
            elif fr_val < -0.005:
                features["fr_direction"] = "NEGATIVE"
            else:
                features["fr_direction"] = "NEUTRAL"
        except Exception:
            features.setdefault("fr_direction", "NEUTRAL")

        # ── Orderbook ──
        try:
            ob = orderbook_data or {}
            obi = ob.get('obi') or {}
            dynamics = ob.get('dynamics') or {}
            features["obi_weighted"] = _sf(obi, 'weighted')
            features["obi_change_pct"] = _sf(dynamics, 'obi_change_pct')
            features["bid_volume_usd"] = _sf(obi, 'bid_volume_usd')
            features["ask_volume_usd"] = _sf(obi, 'ask_volume_usd')
        except Exception:
            self.logger.debug("Feature extraction: orderbook partial failure")

        # ── Sentiment ──
        try:
            sd = sentiment_data or {}
            features["long_ratio"] = _sf(sd, 'positive_ratio')
            features["short_ratio"] = _sf(sd, 'negative_ratio')
            features["sentiment_degraded"] = bool(sd.get('degraded', False))
        except Exception:
            features.setdefault("sentiment_degraded", False)

        # ── Fear & Greed Index (v44.0) ──
        try:
            fg = fear_greed_report or {}
            features["fear_greed_index"] = int(fg.get('value', 50)) if fg else 50
        except Exception:
            features["fear_greed_index"] = 50  # neutral default

        # ── Top Traders (Binance Derivatives) ──
        try:
            bd = binance_derivatives or {}
            # fetch_all() returns nested: top_long_short_position.latest.longShortRatio
            _top_pos = bd.get('top_long_short_position') or {}
            _top_latest = _top_pos.get('latest') or {} if isinstance(_top_pos, dict) else {}
            features["top_traders_long_ratio"] = _sf(_top_latest, 'longShortRatio')
            # fetch_all() returns nested: taker_long_short.latest.buySellRatio
            _taker = bd.get('taker_long_short') or {}
            _taker_latest = _taker.get('latest') or {} if isinstance(_taker, dict) else {}
            features["taker_buy_ratio"] = _sf(_taker_latest, 'buySellRatio')
        except Exception:
            self.logger.debug("Feature extraction: top traders partial failure")

        # ── S/R Zones ──
        # nearest_support / nearest_resistance are SRZone dataclass objects (not dicts),
        # so use getattr() instead of .get().  Field is price_center (not 'price').
        try:
            sz = sr_zones or {}
            ns = sz.get('nearest_support')  # SRZone dataclass or None
            nr = sz.get('nearest_resistance')
            atr_val = features.get('atr_30m', 1.0) or 1.0
            price = features.get('price', 0.0)

            sp = float(getattr(ns, 'price_center', 0.0)) if ns else 0.0
            features["nearest_support_price"] = sp
            features["nearest_support_strength"] = str(getattr(ns, 'strength', 'NONE')).upper() if ns else "NONE"
            features["nearest_support_dist_atr"] = abs(price - sp) / atr_val if sp > 0 and atr_val > 0 else 0.0

            rp = float(getattr(nr, 'price_center', 0.0)) if nr else 0.0
            features["nearest_resist_price"] = rp
            features["nearest_resist_strength"] = str(getattr(nr, 'strength', 'NONE')).upper() if nr else "NONE"
            features["nearest_resist_dist_atr"] = abs(price - rp) / atr_val if rp > 0 and atr_val > 0 else 0.0
        except Exception:
            self.logger.debug("Feature extraction: S/R zones partial failure")

        # ── Position Context ──
        try:
            cp = current_position or {}
            ac = account_context or {}
            ps = cp.get('side', 'FLAT')
            if ps and str(ps).upper() in ("LONG", "SHORT"):
                features["position_side"] = str(ps).upper()
            else:
                features["position_side"] = "FLAT"
            # v31.4: Fix field name mapping to match production data structures:
            # - _get_current_position_data() returns 'pnl_percentage' (not 'pnl_pct')
            # - _get_current_position_data() returns 'margin_used_pct' (not 'size_pct')
            # - _get_account_context() returns 'liquidation_buffer_portfolio_min_pct' (not 'liquidation_buffer_pct')
            features["position_pnl_pct"] = _sf(cp, 'pnl_percentage')
            features["position_size_pct"] = _sf(cp, 'margin_used_pct')
            features["account_equity_usdt"] = _sf(ac, 'equity')
            features["liquidation_buffer_pct"] = _sf(ac, 'liquidation_buffer_portfolio_min_pct')
            features["leverage"] = int(_sf(ac, 'leverage', 1))
        except Exception:
            features.setdefault("position_side", "FLAT")
            features.setdefault("leverage", 1)

        # ── FR Block Context ──
        try:
            fr_ctx = td.get('fr_block_context') or {}
            features["fr_consecutive_blocks"] = int(_sf(fr_ctx, 'consecutive_blocks', 0))
            features["fr_blocked_direction"] = _se(fr_ctx, 'blocked_direction',
                                                    {"LONG", "SHORT", "NONE"})
        except Exception:
            self.logger.debug("Feature extraction: FR block context partial failure")

        # ── Trend Time Series (1D, 5-bar summary) ──
        # v36.0 FIX: was using hist_ctx (30M) — adx_trend/di_plus_trend were 30M data
        # labeled as 1D, and rsi_trend_1d/price_trend_1d keys didn't exist → always empty.
        # Correct source is hist_ctx_1d (1D historical_context) with plain keys.
        try:
            adx_trend = hist_ctx_1d.get('adx_trend', [])
            rsi_1d_trend = hist_ctx_1d.get('rsi_trend', [])
            di_plus_trend = hist_ctx_1d.get('di_plus_trend', [])
            di_minus_trend = hist_ctx_1d.get('di_minus_trend', [])
            price_1d_trend = hist_ctx_1d.get('price_trend', [])

            features["adx_1d_trend_5bar"] = self._classify_trend(adx_trend[-5:]) if len(adx_trend) >= 5 else "FLAT"
            features["rsi_1d_trend_5bar"] = self._classify_trend(rsi_1d_trend[-5:]) if len(rsi_1d_trend) >= 5 else "FLAT"
            features["price_1d_change_5bar_pct"] = (
                ((price_1d_trend[-1] - price_1d_trend[-5]) / price_1d_trend[-5] * 100)
                if len(price_1d_trend) >= 5 and price_1d_trend[-5] > 0 else 0.0
            )

            if len(di_plus_trend) >= 5 and len(di_minus_trend) >= 5:
                spreads = [di_plus_trend[-5+i] - di_minus_trend[-5+i] for i in range(5)]
                features["di_spread_1d_trend_5bar"] = self._classify_spread_trend(spreads)
            else:
                features["di_spread_1d_trend_5bar"] = "FLAT"
        except Exception:
            for k in ("adx_1d_trend_5bar", "di_spread_1d_trend_5bar", "rsi_1d_trend_5bar"):
                features.setdefault(k, "FLAT")
            features.setdefault("price_1d_change_5bar_pct", 0.0)

        # ── 4H Time Series (5-bar summary) ──
        # v36.0 FIX: was using hist_ctx (30M) with '_4h' suffix keys that don't exist.
        # Correct source is hist_ctx_4h (4H historical_context) with plain keys.
        try:
            rsi_4h_hist = hist_ctx_4h.get('rsi_trend', [])
            macd_hist_4h_hist = hist_ctx_4h.get('macd_histogram_trend', [])
            adx_4h_hist = hist_ctx_4h.get('adx_trend', [])
            price_4h_hist = hist_ctx_4h.get('price_trend', [])

            features["rsi_4h_trend_5bar"] = self._classify_trend(rsi_4h_hist[-5:]) if len(rsi_4h_hist) >= 5 else "FLAT"
            features["adx_4h_trend_5bar"] = self._classify_trend(adx_4h_hist[-5:]) if len(adx_4h_hist) >= 5 else "FLAT"
            features["price_4h_change_5bar_pct"] = (
                ((price_4h_hist[-1] - price_4h_hist[-5]) / price_4h_hist[-5] * 100)
                if len(price_4h_hist) >= 5 and price_4h_hist[-5] > 0 else 0.0
            )

            if len(macd_hist_4h_hist) >= 5:
                abs_vals = [abs(v) for v in macd_hist_4h_hist[-5:]]
                features["macd_histogram_4h_trend_5bar"] = self._classify_abs_trend(abs_vals)
            else:
                features["macd_histogram_4h_trend_5bar"] = "FLAT"

            # v36.0: BB width trend for squeeze/expansion detection
            bb_width_4h = hist_ctx_4h.get('bb_width_trend', [])
            if len(bb_width_4h) >= 5:
                features["bb_width_4h_trend_5bar"] = self._classify_trend(bb_width_4h[-5:])
            else:
                features["bb_width_4h_trend_5bar"] = "FLAT"
        except Exception:
            for k in ("rsi_4h_trend_5bar", "macd_histogram_4h_trend_5bar", "adx_4h_trend_5bar",
                       "bb_width_4h_trend_5bar"):
                features.setdefault(k, "FLAT")
            features.setdefault("price_4h_change_5bar_pct", 0.0)

        # ── 30M Time Series (5-bar summary) ──
        try:
            price_30m_hist = hist_ctx.get('price_trend', [])
            rsi_30m_hist = hist_ctx.get('rsi_trend', [])

            features["rsi_30m_trend_5bar"] = self._classify_trend(rsi_30m_hist[-5:]) if len(rsi_30m_hist) >= 5 else "FLAT"
            features["price_30m_change_5bar_pct"] = (
                ((price_30m_hist[-1] - price_30m_hist[-5]) / price_30m_hist[-5] * 100)
                if len(price_30m_hist) >= 5 and price_30m_hist[-5] > 0 else 0.0
            )

            if len(rsi_30m_hist) >= 5:
                recent_slope = rsi_30m_hist[-1] - rsi_30m_hist[-3]
                older_slope = rsi_30m_hist[-3] - rsi_30m_hist[-5]
                if abs(recent_slope) > abs(older_slope) * 1.3:
                    features["momentum_shift_30m"] = "ACCELERATING"
                elif abs(recent_slope) < abs(older_slope) * 0.7:
                    features["momentum_shift_30m"] = "DECELERATING"
                else:
                    features["momentum_shift_30m"] = "STABLE"
            else:
                features["momentum_shift_30m"] = "STABLE"

            # v36.0: BB width trend for squeeze/expansion detection
            bb_width_30m = hist_ctx.get('bb_width_trend', [])
            if len(bb_width_30m) >= 5:
                features["bb_width_30m_trend_5bar"] = self._classify_trend(bb_width_30m[-5:])
            else:
                features["bb_width_30m_trend_5bar"] = "FLAT"
        except Exception:
            features.setdefault("momentum_shift_30m", "STABLE")
            features.setdefault("rsi_30m_trend_5bar", "FLAT")
            features.setdefault("bb_width_30m_trend_5bar", "FLAT")
            features.setdefault("price_30m_change_5bar_pct", 0.0)

        # ── Reliability annotations ──
        try:
            adx_1d = features.get("adx_1d", 0.0)
            reliability = {}
            indicator_keys = {
                'rsi_30m': '30m_rsi', 'macd_30m': '30m_macd', 'adx_30m': '30m_adx',
                'bb_position_30m': '30m_bb', 'volume_ratio_30m': '30m_volume',
                'rsi_4h': '4h_rsi', 'macd_4h': '4h_macd', 'adx_4h': '4h_adx',
                'bb_position_4h': '4h_bb', 'volume_ratio_4h': '4h_vol_ratio',
                'adx_1d': '1d_adx', 'rsi_1d': '1d_rsi', 'macd_1d': '1d_macd',
                'bb_position_1d': '1d_bb', 'atr_1d': '1d_atr', 'atr_4h': '4h_atr',
            }
            for feat_key, annot_key in indicator_keys.items():
                _, _, tier = _get_multiplier(annot_key, adx_1d)
                reliability[feat_key] = tier.upper()
            features["_reliability"] = reliability
        except Exception:
            features["_reliability"] = {}

        # Fill defaults for any missing keys
        for key, schema in FEATURE_SCHEMA.items():
            if key.startswith("_"):
                continue
            if key not in features:
                ftype = schema.get("type", "float")
                if ftype == "float":
                    features[key] = 0.0
                elif ftype == "int":
                    features[key] = 0
                elif ftype == "bool":
                    features[key] = False
                elif ftype == "enum":
                    vals = schema.get("values", ["NONE"])
                    features[key] = "NONE" if "NONE" in vals else vals[0]

        # --- Runtime cross-validation against FEATURE_SCHEMA ---
        _validation_warnings: list = []
        for key, spec in FEATURE_SCHEMA.items():
            if key.startswith("_"):
                continue
            val = features.get(key)
            if val is None:
                continue
            expected_type = spec.get("type", "float")
            if expected_type == "float" and not isinstance(val, (int, float)):
                _validation_warnings.append(f"{key}: expected float, got {type(val).__name__}")
                features[key] = 0.0
            elif expected_type == "int" and not isinstance(val, int):
                _validation_warnings.append(f"{key}: expected int, got {type(val).__name__}")
                features[key] = int(val) if isinstance(val, (float, int)) else 0
            elif expected_type == "enum" and isinstance(val, str):
                valid_vals = spec.get("values", [])
                if valid_vals and val not in valid_vals:
                    _validation_warnings.append(f"{key}: '{val}' not in {valid_vals}")
                    features[key] = "NONE" if "NONE" in valid_vals else valid_vals[0]
            elif expected_type == "bool" and not isinstance(val, bool):
                features[key] = bool(val)

        # Drift detection: keys in features but NOT in FEATURE_SCHEMA
        extra_keys = set(k for k in features if not k.startswith("_")) - set(FEATURE_SCHEMA.keys())
        if extra_keys:
            _validation_warnings.append(f"Extra keys not in FEATURE_SCHEMA: {extra_keys}")

        if _validation_warnings:
            self.logger.warning(
                f"Feature validation: {len(_validation_warnings)} warning(s): "
                + "; ".join(_validation_warnings[:5])
            )

        # ── Data Quality Metadata (v28.0) ──
        # Tracks which data sources were unavailable, so AI can distinguish
        # "data says neutral" from "data is missing".
        unavailable = []
        if not order_flow_data:
            unavailable.append("order_flow_30m")
        if not order_flow_4h:
            unavailable.append("order_flow_4h")
        if not derivatives_data:
            unavailable.append("derivatives")
        if not binance_derivatives:
            unavailable.append("top_traders")
        if not orderbook_data:
            unavailable.append("orderbook")
        if features.get("sentiment_degraded"):
            unavailable.append("sentiment")
        if not sr_zones or (not sr_zones.get('nearest_support') and not sr_zones.get('nearest_resistance')):
            unavailable.append("sr_zones")
        features["_unavailable"] = unavailable

        # v2.0 Phase 1: Pandera Tier 2 — validate extracted features
        try:
            from utils.data_validator import validate_features
            feat_warnings = validate_features(features)
            if feat_warnings:
                features["_validation_warnings"] = feat_warnings
        except Exception:
            pass  # Validation failure must never block trading

        return features

    @staticmethod
    def _classify_trend(series: List[float]) -> str:
        """Classify a short numeric series as RISING/FALLING/FLAT.

        Uses last-vs-first comparison instead of half-average.
        Half-average is structurally flawed for 5-bar series with
        mountain/valley shapes: a peak at index 2 gets assigned to
        second_half, masking the actual trend direction.
        """
        if not series or len(series) < 2:
            return "FLAT"
        diff_pct = (series[-1] - series[0]) / max(abs(series[0]), 1e-9) * 100
        if diff_pct > 5:
            return "RISING"
        elif diff_pct < -5:
            return "FALLING"
        return "FLAT"

    @staticmethod
    def _classify_abs_trend(abs_series: List[float]) -> str:
        """Classify absolute-value trend as EXPANDING/CONTRACTING/FLAT.

        Uses last-vs-first ratio instead of half-average ratio.
        Half-average masks momentum collapse when peak is in the middle
        (e.g. [100, 107, 130, 106, 35] → half-avg says FLAT, but
        momentum clearly collapsed to 27% of peak).
        """
        if not abs_series or len(abs_series) < 2:
            return "FLAT"
        first_val = abs(abs_series[0])
        last_val = abs(abs_series[-1])
        if first_val < 1e-9:
            return "FLAT"
        ratio = last_val / first_val
        if ratio > 1.15:
            return "EXPANDING"
        elif ratio < 0.85:
            return "CONTRACTING"
        return "FLAT"

    @staticmethod
    def _classify_spread_trend(spreads: List[float]) -> str:
        """Classify DI spread trend as WIDENING/NARROWING/FLAT.

        Uses last-vs-first abs-spread ratio instead of half-average.
        """
        if not spreads or len(spreads) < 2:
            return "FLAT"
        first_abs = abs(spreads[0])
        last_abs = abs(spreads[-1])
        if first_abs < 1e-9:
            return "FLAT"
        ratio = last_abs / first_abs
        if ratio > 1.1:
            return "WIDENING"
        elif ratio < 0.9:
            return "NARROWING"
        return "FLAT"

    @staticmethod
    def _compute_cvd_price_cross(
        cvd_history: List[float],
        price_series: List[float],
    ) -> str:
        """
        v34.3: Compute CVD-Price cross classification from raw data.

        Mirrors _format_order_flow_report() logic (v19.2):
        - CVD net = sum of last 5 bars (or all if < 5)
        - Price change = 5-bar percentage change
        - Thresholds: ±0.3% for price flat/rising/falling

        Returns one of: ACCUMULATION, DISTRIBUTION, CONFIRMED_SELL,
                        ABSORPTION_BUY, ABSORPTION_SELL, NONE
        """
        if len(cvd_history) < 3 or len(price_series) < 2:
            return "NONE"

        cvd_net = sum(cvd_history[-5:]) if len(cvd_history) >= 5 else sum(cvd_history)

        # 5-bar price change (matching CVD window)
        if len(price_series) >= 5 and price_series[-5] > 0:
            price_change_pct = (price_series[-1] - price_series[-5]) / price_series[-5] * 100
        elif price_series[0] > 0:
            price_change_pct = (price_series[-1] - price_series[0]) / price_series[0] * 100
        else:
            return "NONE"

        price_flat = abs(price_change_pct) <= 0.3
        price_falling = price_change_pct < -0.3
        price_rising = price_change_pct > 0.3
        cvd_positive = cvd_net > 0
        cvd_negative = cvd_net < 0

        if price_falling and cvd_positive:
            return "ACCUMULATION"
        elif price_rising and cvd_negative:
            return "DISTRIBUTION"
        elif price_falling and cvd_negative:
            return "CONFIRMED_SELL"
        elif price_flat and cvd_positive:
            return "ABSORPTION_BUY"
        elif price_flat and cvd_negative:
            return "ABSORPTION_SELL"
        return "NONE"
