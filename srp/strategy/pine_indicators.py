"""
Pine Script v5 Indicator Replications — SRP v6.1

Extracted from scripts/backtest_srp_v5_exact.py (parity-verified).
These functions produce IDENTICAL output to Pine Script built-in indicators.

Verification: scripts/parity_check.py compares bar-by-bar against TradingView.

DO NOT modify these functions without re-running parity verification.
"""

from typing import List


def pine_rma(values: List[float], period: int) -> List[float]:
    """Exact Pine ta.rma: SMA seed for first `period` values, then Wilder's smoothing.

    Pine source: ta.rma(src, length)
    Used by: RSI calculation (avg_gain, avg_loss)

    Args:
        values: Input series (e.g., gains or losses)
        period: Smoothing period

    Returns:
        List of smoothed values (same length as input, first period-1 values are 0.0)
    """
    n = len(values)
    result = [0.0] * n
    if n < period:
        return result
    # SMA seed
    result[period - 1] = sum(values[:period]) / period
    # Wilder's smoothing: alpha = 1/period
    alpha = 1.0 / period
    for i in range(period, n):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def pine_rsi(closes: List[float], period: int) -> List[float]:
    """Exact Pine RSI using Wilder's RMA.

    Pine source (v6.pine L173-176):
        up   = ta.rma(math.max(ta.change(close), 0), rsi_len)
        down = ta.rma(-math.min(ta.change(close), 0), rsi_len)
        rsi  = down == 0 ? 100 : up == 0 ? 0 : 100 - (100 / (1 + up / down))

    Note: RSI always uses close (Wilder's standard), regardless of src_type.
    Range: 0-100.

    Args:
        closes: Close price series
        period: RSI period (default 7 in SRP)

    Returns:
        List of RSI values (same length as closes, first values are 50.0)
    """
    n = len(closes)
    if n < period + 1:
        return [50.0] * n

    changes = [0.0] + [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]

    avg_gain = pine_rma(gains, period)
    avg_loss = pine_rma(losses, period)

    result = [50.0] * n
    for i in range(period, n):
        if avg_loss[i] == 0:
            result[i] = 100.0
        elif avg_gain[i] == 0:
            result[i] = 0.0
        else:
            result[i] = 100.0 - (100.0 / (1.0 + avg_gain[i] / avg_loss[i]))
    return result


def pine_vwma(hlc3_list: List[float], vol_list: List[float], length: int, i: int) -> float:
    """Exact Pine ta.vwma(hlc3, length) at bar index i.

    Pine source (v6.pine L168): float core = ta.vwma(src, Length)
    Formula: sum(src[j] * volume[j]) / sum(volume[j]) for j in window

    Args:
        hlc3_list: HLC3 (typical price) series
        vol_list: Volume series
        length: VWMA period
        i: Current bar index

    Returns:
        VWMA value at bar i
    """
    if i < length - 1:
        return hlc3_list[i]
    num = sum(hlc3_list[j] * vol_list[j] for j in range(i - length + 1, i + 1))
    den = sum(vol_list[j] for j in range(i - length + 1, i + 1))
    return num / den if den > 0 else hlc3_list[i]


def pine_mfi(tp_list: List[float], vol_list: List[float], period: int, i: int) -> float:
    """Exact Pine ta.mfi(hlc3, period) at bar index i.

    Pine source (v6.pine L176): float mf = ta.mfi(src, mfi_len)
    Uses typical price (HLC3) and volume to compute Money Flow Index.
    Range: 0-100.

    Args:
        tp_list: Typical price (HLC3) series
        vol_list: Volume series
        period: MFI period (default 7 in SRP)
        i: Current bar index

    Returns:
        MFI value at bar i (50.0 during warmup)
    """
    if i < period:
        return 50.0
    pos_flow = 0.0
    neg_flow = 0.0
    for j in range(i - period + 1, i + 1):
        mf = tp_list[j] * vol_list[j]
        if j > 0 and tp_list[j] > tp_list[j - 1]:
            pos_flow += mf
        elif j > 0 and tp_list[j] < tp_list[j - 1]:
            neg_flow += mf
    if neg_flow == 0:
        return 100.0
    if pos_flow == 0:
        return 0.0
    return 100.0 - (100.0 / (1.0 + pos_flow / neg_flow))


def pine_rsi_mfi(rsi_value: float, mfi_value: float) -> float:
    """Composite RSI-MFI indicator — exact Pine formula.

    Pine source (v6.pine L181): float rsi_mfi = math.abs(rsi + mf / 2)

    [DOC-1] Operator precedence: rsi + (mf / 2), i.e., MFI weight = half of RSI.
    Actual range: 0~150 (RSI 0~100 + MFI/2 0~50). abs() is redundant but harmless.
    This formula is inherited from Felix's original design.

    Args:
        rsi_value: RSI value (0-100)
        mfi_value: MFI value (0-100)

    Returns:
        Composite RSI-MFI value (0-150)
    """
    return abs(rsi_value + mfi_value / 2.0)
