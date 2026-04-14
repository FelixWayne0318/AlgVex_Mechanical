# utils/order_flow_processor.py

import logging
from typing import List, Dict, Any, Union

from utils.shared_logic import calculate_cvd_trend


class OrderFlowProcessor:
    """
    订单流数据处理器

    从 Binance K线数据计算订单流指标

    v2.0 更新:
    - 支持 Binance 原始 12 列格式 (List[List])
    - 支持本地 Dict 格式 (List[Dict]) - 降级模式，无订单流数据
    """

    def __init__(self, logger: logging.Logger = None):
        self._cvd_history: List[float] = []
        self.logger = logger or logging.getLogger(__name__)

    def process_klines(
        self,
        klines: Union[List[List], List[Dict]],
    ) -> Dict[str, Any]:
        """
        处理 K线数据，计算订单流指标

        Args:
            klines: K线数据，支持两种格式:
                - List[List]: Binance 原始 12 列格式 (完整订单流数据)
                - List[Dict]: 本地 Dict 格式 (降级模式，无订单流数据)

        Returns:
            {
                "buy_ratio": 0.55,           # 买盘占比
                "avg_trade_usdt": 1250.5,    # 平均成交额
                "volume_usdt": 125000000,    # 总成交额
                "trades_count": 100000,      # 成交笔数
                "cvd_trend": "RISING",       # CVD 趋势
                "recent_10_bars": [...],     # 最近10根bar的买盘比
                "data_source": "binance_raw" | "local_dict",
            }
        """
        if not klines or len(klines) == 0:
            return self._default_result()

        # 检测数据格式
        if isinstance(klines[0], list):
            return self._process_binance_format(klines)
        elif isinstance(klines[0], dict):
            return self._process_dict_format(klines)
        else:
            self.logger.warning(f"⚠️ Unknown kline format: {type(klines[0])}")
            return self._default_result()

    def _process_binance_format(self, klines: List[List]) -> Dict[str, Any]:
        """
        处理 Binance 原始 12 列格式 (完整订单流数据)

        v2.1 更新:
        - buy_ratio 改用 10 根 K 线平均值 (更稳定，减少噪声)
        - 保留 latest_buy_ratio 供参考

        v6.5 更新:
        - 检测并剔除末尾不完整 bar (volume < 10% 均值)，避免 0.03x 伪影
        """
        # v6.5: Detect incomplete trailing bar (Layer 2 of 2 — defensive fallback)
        # Design intent (two-layer filtering):
        #   Layer 1 (AIDataAssembler.fetch_external_data): strips klines[-1] unconditionally
        #            before calling process_klines(), handling the common case.
        #   Layer 2 (here): volume-based heuristic catches edge cases where Layer 1 was
        #            bypassed (e.g., direct callers, legacy assemble() path, or future refactors).
        #            Triggers only when last bar volume < 10% of recent average — a strong
        #            signal of an in-progress bar, not a valid thin-liquidity completed bar.
        # Both layers are intentional and complementary. Do not remove either.
        if len(klines) > 10:
            # Calculate median volume of bars [:-1] to detect incomplete last bar
            prev_volumes = [float(k[5]) for k in klines[-11:-1]]
            avg_prev_vol = sum(prev_volumes) / len(prev_volumes) if prev_volumes else 0
            last_vol = float(klines[-1][5])
            if avg_prev_vol > 0 and last_vol < avg_prev_vol * 0.1:
                # Last bar volume is < 10% of average → almost certainly incomplete
                self.logger.debug(
                    f"OrderFlow: stripped incomplete bar "
                    f"(vol={last_vol:.1f}, avg={avg_prev_vol:.1f}, ratio={last_vol/avg_prev_vol:.3f})"
                )
                klines = klines[:-1]

        latest = klines[-1]

        volume = float(latest[5])
        taker_buy_volume = float(latest[9])
        quote_volume = float(latest[7])
        trades_count = int(latest[8])

        # 计算最新 K 线的买盘占比 (保留供参考)
        latest_buy_ratio = taker_buy_volume / volume if volume > 0 else 0.5

        # 计算平均成交额
        avg_trade_usdt = quote_volume / trades_count if trades_count > 0 else 0

        # 计算 CVD (累积成交量差)
        # v5.6: Bootstrap CVD history from ALL klines on first call
        # Previously only processed latest kline → needed 5+ cycles (75min) for CVD trend
        # Now: first call processes all klines → immediate CVD trend from cycle 1
        if len(self._cvd_history) == 0 and len(klines) > 1:
            # Bootstrap: process all historical klines (skip latest, added below)
            for bar in klines[:-1]:
                bar_vol = float(bar[5])
                bar_buy = float(bar[9])
                bar_sell = bar_vol - bar_buy
                self._cvd_history.append(bar_buy - bar_sell)

        sell_volume = volume - taker_buy_volume
        cvd_delta = taker_buy_volume - sell_volume
        self._cvd_history.append(cvd_delta)

        # 保留最近 50 个 CVD 值
        if len(self._cvd_history) > 50:
            self._cvd_history = self._cvd_history[-50:]

        # 判断 CVD 趋势
        cvd_trend = self._calculate_cvd_trend()

        # 计算最近 10 根 bar 的买盘比
        recent_10_bars = []
        for bar in klines[-10:]:
            bar_volume = float(bar[5])
            bar_buy = float(bar[9])
            bar_ratio = bar_buy / bar_volume if bar_volume > 0 else 0.5
            recent_10_bars.append(round(bar_ratio, 4))

        # v2.1: 使用 10 根 K 线平均值作为主 buy_ratio (更稳定)
        # 之前只用最新一根 K 线，波动太大
        avg_buy_ratio = sum(recent_10_bars) / len(recent_10_bars) if recent_10_bars else 0.5

        # v5.2: Expose CVD numerical history for AI analysis
        cvd_recent = [round(v, 2) for v in self._cvd_history[-10:]]
        cvd_cumulative = round(sum(self._cvd_history), 2) if self._cvd_history else 0.0

        return {
            "buy_ratio": round(avg_buy_ratio, 4),  # 使用 10 bar 平均值
            "latest_buy_ratio": round(latest_buy_ratio, 4),  # 保留最新 K 线值供参考
            "avg_trade_usdt": round(avg_trade_usdt, 2),
            "volume_usdt": round(quote_volume, 2),
            "trades_count": trades_count,
            "cvd_trend": cvd_trend,
            "cvd_history": cvd_recent,  # v5.2: Last 10 CVD deltas (numerical)
            "cvd_cumulative": cvd_cumulative,  # v5.2: Cumulative sum
            "recent_10_bars": recent_10_bars,
            "recent_10_bars_avg": round(avg_buy_ratio, 4),  # 明确标记这是平均值
            "data_source": "binance_raw",
            "bars_count": len(klines),  # v2.1: 添加采样窗口大小，便于诊断
        }

    def _process_dict_format(self, klines: List[Dict]) -> Dict[str, Any]:
        """
        处理本地 Dict 格式 (降级模式)

        注意: Dict 格式不包含 taker_buy_volume，无法计算真实订单流
        返回中性默认值，标记为降级数据源
        """
        self.logger.debug(
            "OrderFlowProcessor: Using Dict format (degraded mode, no order flow data)"
        )

        # 从 Dict 格式提取基础信息
        latest = klines[-1]
        volume = latest.get('volume', 0)

        return {
            "buy_ratio": 0.5,  # 中性值 (无数据)
            "avg_trade_usdt": 0,
            "volume_usdt": volume,  # 只有 volume 可用
            "trades_count": 0,
            "cvd_trend": "NEUTRAL",
            "recent_10_bars": [],
            "data_source": "local_dict",  # 标记为降级模式
            "bars_count": len(klines),  # v2.1: 添加采样窗口大小
            "_warning": "Dict format has no order flow data, using neutral values",
        }

    def _calculate_cvd_trend(self) -> str:
        """Calculate CVD trend — delegates to shared SSoT implementation."""
        return calculate_cvd_trend(self._cvd_history)

    def _default_result(self) -> Dict[str, Any]:
        """返回默认值"""
        return {
            "buy_ratio": 0.5,
            "avg_trade_usdt": 0,
            "volume_usdt": 0,
            "trades_count": 0,
            "cvd_trend": "NEUTRAL",
            "recent_10_bars": [],
            "data_source": "none",
        }

    def reset_cvd_history(self):
        """重置 CVD 历史 (用于测试或重启后)"""
        self._cvd_history = []
