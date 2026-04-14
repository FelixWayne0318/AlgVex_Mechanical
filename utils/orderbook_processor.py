# utils/orderbook_processor.py

import logging
import statistics
import time
from typing import List, Dict, Any, Optional, Tuple


class OrderBookProcessor:
    """
    订单簿数据处理器 v2.0

    职责:
    - 计算 OBI (Order Book Imbalance)
    - 计算加权 OBI (靠近盘口权重更高)
    - v2.0: 自适应加权 OBI (基于波动率)
    - 计算深度分布 (按价格带聚合)
    - v2.0: Book Pressure Gradient
    - v2.0: 动态异常检测阈值
    - 估算滑点
    - v2.0: 滑点置信度和范围
    - v2.0: 变化率计算 (Critical)

    设计原则:
    - 只做预处理，不做判断
    - 输出原始指标，让 AI 解读
    - v2.0: 明确标记数据状态

    参考:
    - Cont et al. (2014): Order book imbalance theory
    - Cartea et al. (2015): Algorithmic and High-Frequency Trading
    """

    def __init__(
        self,
        price_band_pct: float = 0.5,
        base_anomaly_threshold: float = 3.0,
        slippage_amounts: List[float] = None,
        weighted_obi_config: Dict = None,
        history_size: int = 10,
        logger: logging.Logger = None,
    ):
        """
        初始化处理器

        Parameters
        ----------
        price_band_pct : float
            价格带宽度百分比 (用于深度分布聚合)
        base_anomaly_threshold : float
            基础异常阈值 (倍数)
        slippage_amounts : List[float], optional
            滑点估算的交易量列表 (BTC)
        weighted_obi_config : Dict, optional
            加权 OBI 配置 (v2.0)
        history_size : int
            历史缓存大小 (用于变化率计算)
        logger : logging.Logger, optional
            日志记录器
        """
        self.price_band_pct = price_band_pct
        self.base_anomaly_threshold = base_anomaly_threshold
        self.slippage_amounts = slippage_amounts or [0.1, 0.5, 1.0]
        self.logger = logger or logging.getLogger(__name__)

        # v2.0: 加权 OBI 配置
        self.weighted_obi_config = weighted_obi_config or {
            "base_decay": 0.8,
            "adaptive": True,
            "volatility_factor": 0.1,
            "min_decay": 0.5,
            "max_decay": 0.95,
        }

        # v2.0: 历史数据缓存 (用于计算变化率)
        self._history: List[Dict] = []
        self._history_size = history_size

    def process(
        self,
        order_book: Dict,
        current_price: float,
        volatility: float = None,
    ) -> Dict[str, Any]:
        """
        处理订单簿数据

        Parameters
        ----------
        order_book : Dict
            Binance 订单簿原始数据
        current_price : float
            当前价格
        volatility : float, optional
            波动率 (用于自适应调整)

        Returns
        -------
        Dict
            包含所有订单簿指标的字典 (详见方案文档 lines/259-372)
        """
        try:
            bids = order_book.get("bids", [])
            asks = order_book.get("asks", [])

            if not bids or not asks:
                self.logger.error("Empty bids or asks")
                return self._no_data_result("Empty order book")

            # ========== 基础不平衡指标 ==========
            simple_obi = self._calculate_simple_obi(bids, asks)

            # 加权 OBI (固定衰减)
            base_decay = self.weighted_obi_config["base_decay"]
            weighted_obi = self._calculate_weighted_obi(bids, asks, base_decay)

            # v2.0: 自适应加权 OBI
            adaptive_decay = self._calculate_adaptive_decay(volatility)
            adaptive_weighted_obi = self._calculate_weighted_obi(bids, asks, adaptive_decay)

            # 计算总量
            bid_volume_btc = sum(float(b[1]) for b in bids)
            ask_volume_btc = sum(float(a[1]) for a in asks)
            bid_volume_usd = sum(float(b[0]) * float(b[1]) for b in bids)
            ask_volume_usd = sum(float(a[0]) * float(a[1]) for a in asks)

            obi_data = {
                "simple": round(simple_obi, 4),
                "weighted": round(weighted_obi, 4),
                "adaptive_weighted": round(adaptive_weighted_obi, 4),
                "decay_used": round(adaptive_decay, 2),
                "bid_volume_btc": round(bid_volume_btc, 2),
                "ask_volume_btc": round(ask_volume_btc, 2),
                "bid_volume_usd": round(bid_volume_usd, 2),
                "ask_volume_usd": round(ask_volume_usd, 2),
            }

            # ========== v2.0: Pressure Gradient ==========
            pressure_gradient = self._calculate_pressure_gradient(bids, asks)

            # ========== 深度分布 ==========
            depth_distribution = self._calculate_depth_distribution(bids, asks, current_price)

            # ========== v2.0: 动态异常检测 ==========
            all_volumes = [float(b[1]) for b in bids] + [float(a[1]) for a in asks]
            threshold, threshold_reason = self._calculate_dynamic_threshold(all_volumes, volatility)
            anomalies = self._detect_anomalies(bids, asks, threshold, current_price)
            anomalies["threshold_used"] = threshold
            anomalies["threshold_reason"] = threshold_reason

            # ========== v2.0: 滑点估算 (含置信度) ==========
            liquidity = self._calculate_liquidity(bids, asks)

            # ========== 组装当前数据 ==========
            current_data = {
                "obi": obi_data,
                "pressure_gradient": pressure_gradient,
                "depth_distribution": depth_distribution,
                "anomalies": anomalies,
                "liquidity": liquidity,
                "_status": {
                    "code": "OK",
                    "message": "Full data available",
                    "timestamp": int(time.time() * 1000),
                    "levels_analyzed": len(bids) + len(asks),
                    "price_used": current_price,
                    "history_samples": len(self._history),
                },
            }

            # ========== v2.0 Critical: 变化率指标 ==========
            dynamics = self._calculate_dynamics(current_data)
            current_data["dynamics"] = dynamics

            # ========== 更新历史缓存 ==========
            self._update_history(current_data)

            return current_data

        except Exception as e:
            self.logger.error(f"Order book processing error: {e}")
            return self._no_data_result(str(e))

    # =========================================================================
    # v2.0 新增方法
    # =========================================================================

    def _calculate_adaptive_decay(self, volatility: Optional[float]) -> float:
        """
        计算自适应衰减因子 (v2.0)

        高波动时: 降低衰减 (更关注盘口)
        低波动时: 提高衰减 (更多考虑远档)

        注意: 这是方案中的建议实现，实际效果需要回测验证
        如果配置为 adaptive: false，则使用固定 base_decay

        Parameters
        ----------
        volatility : float, optional
            相对波动率 (ATR / price)

        Returns
        -------
        float
            自适应衰减因子
        """
        config = self.weighted_obi_config
        if not config.get("adaptive") or volatility is None:
            return config["base_decay"]

        # Formula: decay = base_decay - volatility * volatility_factor
        decay = config["base_decay"] - volatility * config["volatility_factor"]
        return max(config["min_decay"], min(config["max_decay"], decay))

    def _calculate_dynamics(self, current_data: Dict) -> Dict:
        """
        计算变化率指标 (Critical v2.0)

        比较当前数据与历史数据，计算:
        - OBI 变化
        - 深度变化
        - 价差变化

        Parameters
        ----------
        current_data : Dict
            当前订单簿数据

        Returns
        -------
        Dict
            变化率指标字典
        """
        if len(self._history) == 0:
            return {
                "obi_change": None,
                "obi_change_pct": None,
                "bid_depth_change_pct": None,
                "ask_depth_change_pct": None,
                "spread_change_pct": None,
                "samples_count": 0,
                "trend": "INSUFFICIENT_DATA",
            }

        prev = self._history[-1]
        curr_obi = current_data["obi"]["simple"]
        prev_obi = prev["obi"]["simple"]

        obi_change = curr_obi - prev_obi
        obi_change_pct = (obi_change / abs(prev_obi) * 100) if prev_obi != 0 else None

        curr_bid = current_data["depth_distribution"]["bid_depth_usd"]
        prev_bid = prev["depth_distribution"]["bid_depth_usd"]
        bid_change = ((curr_bid - prev_bid) / prev_bid * 100) if prev_bid > 0 else None

        curr_ask = current_data["depth_distribution"]["ask_depth_usd"]
        prev_ask = prev["depth_distribution"]["ask_depth_usd"]
        ask_change = ((curr_ask - prev_ask) / prev_ask * 100) if prev_ask > 0 else None

        curr_spread = current_data["liquidity"]["spread_pct"]
        prev_spread = prev["liquidity"]["spread_pct"]
        spread_change = ((curr_spread - prev_spread) / prev_spread * 100) if prev_spread > 0 else None

        # 趋势描述 (不做判断，只描述现象)
        trend = self._describe_trend(obi_change, bid_change, ask_change)

        # v5.10: Build OBI history array for AI trend analysis
        # Includes all cached historical OBI values + current value
        obi_trend = [round(h["obi"]["simple"], 4) for h in self._history]
        obi_trend.append(round(curr_obi, 4))

        return {
            "obi_change": round(obi_change, 4) if obi_change is not None else None,
            "obi_change_pct": round(obi_change_pct, 2) if obi_change_pct is not None else None,
            "bid_depth_change_pct": round(bid_change, 2) if bid_change is not None else None,
            "ask_depth_change_pct": round(ask_change, 2) if ask_change is not None else None,
            "spread_change_pct": round(spread_change, 2) if spread_change is not None else None,
            "samples_count": len(self._history),
            "trend": trend,
            "obi_trend": obi_trend,
        }

    def _describe_trend(
        self,
        obi_change: Optional[float],
        bid_change: Optional[float],
        ask_change: Optional[float],
    ) -> str:
        """
        描述趋势 (仅描述，不做判断)

        返回值是客观描述，不是交易建议

        Parameters
        ----------
        obi_change : float, optional
            OBI 变化
        bid_change : float, optional
            买盘深度变化百分比
        ask_change : float, optional
            卖盘深度变化百分比

        Returns
        -------
        str
            趋势描述 (见方案文档 lines/1306-1315)
        """
        if obi_change is None:
            return "INSUFFICIENT_DATA"

        if obi_change > 0.05:
            return "BID_STRENGTHENING"      # 买盘相对增强
        elif obi_change < -0.05:
            return "ASK_STRENGTHENING"      # 卖盘相对增强
        elif bid_change and bid_change < -5:
            return "BID_THINNING"           # 买盘稀薄化
        elif ask_change and ask_change < -5:
            return "ASK_THINNING"           # 卖盘稀薄化
        else:
            return "STABLE"                 # 相对稳定

    def _calculate_pressure_gradient(
        self,
        bids: List,
        asks: List,
    ) -> Dict:
        """
        计算 Pressure Gradient (v2.0)

        衡量订单集中在盘口附近还是分散在远档

        Parameters
        ----------
        bids : List
            买单列表
        asks : List
            卖单列表

        Returns
        -------
        Dict
            压力梯度指标
        """
        def calc_concentration(orders: List, levels: List[int]) -> Dict:
            """计算前 N 档占比"""
            total = sum(float(o[1]) for o in orders)
            if total == 0:
                return {f"near_{l}": 0.0 for l in levels}

            result = {}
            for level in levels:
                near_vol = sum(float(orders[i][1]) for i in range(min(level, len(orders))))
                result[f"near_{level}"] = round(near_vol / total, 4)
            return result

        bid_conc = calc_concentration(bids, [5, 10, 20])
        ask_conc = calc_concentration(asks, [5, 10, 20])

        # 描述集中度 (不做判断)
        def describe_concentration(near_5: float) -> str:
            if near_5 > 0.4:
                return "HIGH"           # 订单集中在盘口
            elif near_5 > 0.25:
                return "MEDIUM"
            else:
                return "LOW"            # 订单分散

        return {
            "bid_near_5": bid_conc["near_5"],
            "bid_near_10": bid_conc["near_10"],
            "bid_near_20": bid_conc["near_20"],
            "ask_near_5": ask_conc["near_5"],
            "ask_near_10": ask_conc["near_10"],
            "ask_near_20": ask_conc["near_20"],
            "bid_concentration": describe_concentration(bid_conc["near_5"]),
            "ask_concentration": describe_concentration(ask_conc["near_5"]),
        }

    def _calculate_dynamic_threshold(
        self,
        volumes: List[float],
        volatility: Optional[float] = None,
    ) -> Tuple[float, str]:
        """
        计算动态异常阈值 (v2.0)

        基于近期订单量波动自动调整阈值

        Parameters
        ----------
        volumes : List[float]
            订单量列表
        volatility : float, optional
            波动率 (当前未使用，预留)

        Returns
        -------
        Tuple[float, str]
            (阈值, 调整原因)
        """
        if len(volumes) < 5:
            return self.base_anomaly_threshold, "insufficient_data"

        try:
            std = statistics.stdev(volumes)
            mean = statistics.mean(volumes)

            if mean == 0:
                return self.base_anomaly_threshold, "zero_mean"

            cv = std / mean  # 变异系数

            # 高变异 → 提高阈值 (减少误报)
            # 低变异 → 降低阈值 (提高敏感度)
            if cv > 1.0:
                threshold = min(5.0, self.base_anomaly_threshold * 1.5)
                reason = "high_volatility"
            elif cv < 0.3:
                threshold = max(2.0, self.base_anomaly_threshold * 0.7)
                reason = "low_volatility"
            else:
                threshold = self.base_anomaly_threshold
                reason = "normal"

            return round(threshold, 2), reason

        except Exception as e:
            self.logger.warning(f"Dynamic threshold calculation error: {e}")
            return self.base_anomaly_threshold, "error"

    def _update_history(self, data: Dict):
        """
        更新历史缓存

        只缓存必要字段以节省内存

        Parameters
        ----------
        data : Dict
            当前订单簿数据
        """
        # 只缓存必要字段
        cached = {
            "obi": {"simple": data["obi"]["simple"]},
            "depth_distribution": {
                "bid_depth_usd": data["depth_distribution"]["bid_depth_usd"],
                "ask_depth_usd": data["depth_distribution"]["ask_depth_usd"],
            },
            "liquidity": {"spread_pct": data["liquidity"]["spread_pct"]},
            "timestamp": data["_status"]["timestamp"],
        }
        self._history.append(cached)
        if len(self._history) > self._history_size:
            self._history = self._history[-self._history_size:]

    # =========================================================================
    # 原有方法 (v1.0 保留)
    # =========================================================================

    def _calculate_simple_obi(self, bids: List, asks: List) -> float:
        """
        计算简单 OBI

        OBI = (bid_volume - ask_volume) / (bid_volume + ask_volume)

        Parameters
        ----------
        bids : List
            买单列表
        asks : List
            卖单列表

        Returns
        -------
        float
            OBI 值 [-1, 1]
        """
        bid_vol = sum(float(b[1]) for b in bids)
        ask_vol = sum(float(a[1]) for a in asks)
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    def _calculate_weighted_obi(
        self,
        bids: List,
        asks: List,
        decay: float,
    ) -> float:
        """
        计算加权 OBI

        公式: 权重 = decay ^ (距离盘口的档位)
        靠近盘口的订单权重更高，远离盘口的权重递减

        Parameters
        ----------
        bids : List
            买单列表
        asks : List
            卖单列表
        decay : float
            衰减因子 [0, 1]

        Returns
        -------
        float
            加权 OBI 值 [-1, 1]
        """
        weighted_bid = sum(
            float(bid[1]) * (decay ** i)
            for i, bid in enumerate(bids)
        )
        weighted_ask = sum(
            float(ask[1]) * (decay ** i)
            for i, ask in enumerate(asks)
        )
        total = weighted_bid + weighted_ask
        if total == 0:
            return 0.0
        return (weighted_bid - weighted_ask) / total

    def _calculate_depth_distribution(
        self,
        bids: List,
        asks: List,
        current_price: float,
    ) -> Dict:
        """
        按价格带聚合深度

        将订单簿按价格带分组，便于 AI 理解深度分布

        Parameters
        ----------
        bids : List
            买单列表
        asks : List
            卖单列表
        current_price : float
            当前价格

        Returns
        -------
        Dict
            深度分布字典
        """
        # 价格带定义 (相对当前价格的百分比)
        bands = [
            (-1.5, -1.0, "bid"),
            (-1.0, -0.5, "bid"),
            (-0.5, 0, "bid"),
            (0, 0.5, "ask"),
            (0.5, 1.0, "ask"),
            (1.0, 1.5, "ask"),
        ]

        result = []
        bid_depth_usd = 0.0
        ask_depth_usd = 0.0

        for low_pct, high_pct, side in bands:
            low_price = current_price * (1 + low_pct / 100)
            high_price = current_price * (1 + high_pct / 100)

            orders = bids if side == "bid" else asks

            # 聚合该价格带的订单量
            volume_usd = 0.0
            for price_str, qty_str in orders:
                price = float(price_str)
                qty = float(qty_str)

                if side == "bid":
                    if low_price <= price < high_price:
                        volume_usd += price * qty
                else:  # ask
                    if low_price < price <= high_price:
                        volume_usd += price * qty

            if side == "bid":
                bid_depth_usd += volume_usd
            else:
                ask_depth_usd += volume_usd

            result.append({
                "range": f"{low_pct:+.1f}% ~ {high_pct:+.1f}%",
                "side": side,
                "volume_usd": round(volume_usd, 2),
            })

        return {
            "bands": result,
            "bid_depth_usd": round(bid_depth_usd, 2),
            "ask_depth_usd": round(ask_depth_usd, 2),
        }

    def _detect_anomalies(
        self,
        bids: List,
        asks: List,
        threshold: float,
        current_price: float,
    ) -> Dict:
        """
        检测异常大单

        识别远超平均水平的订单 (可能是机构大单或虚假墙)

        Parameters
        ----------
        bids : List
            买单列表
        asks : List
            卖单列表
        threshold : float
            异常阈值 (倍数)
        current_price : float
            当前价格

        Returns
        -------
        Dict
            异常检测结果
        """
        # 计算平均订单量
        all_volumes = [float(b[1]) for b in bids] + [float(a[1]) for a in asks]
        if not all_volumes:
            return {"bid_anomalies": [], "ask_anomalies": [], "has_significant": False}

        avg_volume = statistics.mean(all_volumes)

        bid_anomalies = []
        ask_anomalies = []

        # 检测买单异常
        for price_str, qty_str in bids:
            qty = float(qty_str)
            if qty > avg_volume * threshold:
                bid_anomalies.append({
                    "price": round(float(price_str), 2),
                    "volume_btc": round(qty, 2),
                    "multiplier": round(qty / avg_volume, 1),
                })

        # 检测卖单异常
        for price_str, qty_str in asks:
            qty = float(qty_str)
            if qty > avg_volume * threshold:
                ask_anomalies.append({
                    "price": round(float(price_str), 2),
                    "volume_btc": round(qty, 2),
                    "multiplier": round(qty / avg_volume, 1),
                })

        return {
            "bid_anomalies": bid_anomalies,
            "ask_anomalies": ask_anomalies,
            "has_significant": len(bid_anomalies) > 0 or len(ask_anomalies) > 0,
        }

    def _calculate_liquidity(self, bids: List, asks: List) -> Dict:
        """
        计算流动性指标

        包括:
        - 价差
        - 滑点估算 (v2.0 含置信度)

        Parameters
        ----------
        bids : List
            买单列表
        asks : List
            卖单列表

        Returns
        -------
        Dict
            流动性指标字典
        """
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])

        spread_pct = (best_ask - best_bid) / best_bid * 100
        spread_usd = best_ask - best_bid
        mid_price = (best_bid + best_ask) / 2

        # v2.0: 滑点估算 (含置信度)
        slippage = {}
        for amount in self.slippage_amounts:
            slippage[f"buy_{amount}_btc"] = self._estimate_slippage_with_confidence(
                asks, amount, "buy"
            )
            slippage[f"sell_{amount}_btc"] = self._estimate_slippage_with_confidence(
                bids, amount, "sell"
            )

        return {
            "slippage": slippage,
            "spread_pct": round(spread_pct, 4),
            "spread_usd": round(spread_usd, 2),
            "mid_price": round(mid_price, 2),
        }

    def _estimate_slippage_with_confidence(
        self,
        orders: List,
        amount: float,
        side: str,
    ) -> Dict:
        """
        估算滑点 (含置信度) v2.0

        考虑:
        - 可见流动性
        - 隐藏流动性不确定性 (冰山订单)

        Parameters
        ----------
        orders : List
            订单列表 (买单或卖单)
        amount : float
            交易量 (BTC)
        side : str
            "buy" 或 "sell"

        Returns
        -------
        Dict
            滑点估算结果 (含置信度和范围)
        """
        cumulative = 0.0
        weighted_price = 0.0

        for price_str, qty_str in orders:
            price = float(price_str)
            qty = float(qty_str)

            if cumulative + qty >= amount:
                remaining = amount - cumulative
                weighted_price += price * remaining
                cumulative = amount
                break
            else:
                weighted_price += price * qty
                cumulative += qty

        if cumulative < amount:
            # 深度不足
            return {
                "estimated": None,
                "confidence": 0.0,
                "range": [None, None],
                "reason": "insufficient_depth",
            }

        avg_price = weighted_price / amount
        best_price = float(orders[0][0])

        if side == "buy":
            slippage = (avg_price - best_price) / best_price * 100
        else:
            slippage = (best_price - avg_price) / best_price * 100

        # 置信度: 基于深度充裕程度
        depth_ratio = cumulative / amount
        confidence = min(0.95, 0.5 + depth_ratio * 0.3)

        # 范围: 考虑隐藏流动性的不确定性
        # 假设实际滑点可能在 0.5x ~ 1.5x 估算值
        range_low = max(0, slippage * 0.5)
        range_high = slippage * 1.5

        return {
            "estimated": round(slippage, 4),
            "confidence": round(confidence, 2),
            "range": [round(range_low, 4), round(range_high, 4)],
        }

    def _no_data_result(self, reason: str) -> Dict:
        """
        返回 NO_DATA 状态 (v2.0 Critical)

        避免 AI 将缺失数据误解为中性市场

        Parameters
        ----------
        reason : str
            数据不可用的原因

        Returns
        -------
        Dict
            NO_DATA 状态字典
        """
        return {
            "obi": None,
            "dynamics": None,
            "pressure_gradient": None,
            "depth_distribution": None,
            "anomalies": None,
            "liquidity": None,
            "_status": {
                "code": "NO_DATA",
                "message": f"Order book data unavailable: {reason}",
                "timestamp": int(time.time() * 1000),
            },
        }
