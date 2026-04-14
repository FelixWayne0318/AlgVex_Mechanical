# utils/ai_data_assembler.py

import logging
from typing import Dict, Any, List, Optional
from utils.order_flow_processor import OrderFlowProcessor


class AIDataAssembler:
    """
    AI 数据组装器 — 外部 API 数据获取的单一来源 (Single Source of Truth)

    v7.0 重构:
    - fetch_external_data(): 统一的外部 API 数据获取方法
      生产 on_timer() 和诊断 ai_decision.py 共用此方法，
      消除 ~350 行重复代码。返回 analyze() 兼容的 key names。
    - assemble() / format_complete_report(): DEPRECATED，仅供旧测试脚本使用

    职责:
    1. 获取所有外部 API 数据 (Sentiment, Order Flow, Coinalyze, Orderbook, Binance Derivatives)
    2. Binance Funding Rate 注入 (合并到 derivatives 数据)
    3. 返回与 MultiAgentAnalyzer.analyze() 参数名一致的 dict

    v2.0: 同步实现，兼容 on_timer()
    v3.0: 整合 BinanceDerivativesClient + Coinalyze
    v3.0.1: historical_context 支持
    v7.0: fetch_external_data() 统一接口
    """

    def __init__(
        self,
        binance_kline_client,
        order_flow_processor,
        coinalyze_client,
        sentiment_client,
        binance_derivatives_client=None,
        binance_orderbook_client=None,
        orderbook_processor=None,
        config: Dict = None,
        logger: logging.Logger = None,
    ):
        """
        初始化数据组装器

        Parameters
        ----------
        binance_kline_client : BinanceKlineClient
            Binance K线客户端 (获取完整 12 列数据)
        order_flow_processor : OrderFlowProcessor
            订单流处理器
        coinalyze_client : CoinalyzeClient
            Coinalyze 衍生品客户端
        sentiment_client : SentimentDataFetcher
            情绪数据客户端
        binance_derivatives_client : BinanceDerivativesClient, optional
            Binance 衍生品客户端 (大户数据等) - v3.0 新增
        binance_orderbook_client : BinanceOrderBookClient, optional
            Binance 订单簿客户端 - v3.7 新增
        orderbook_processor : OrderBookProcessor, optional
            订单簿处理器 - v3.7 新增
        config : Dict, optional
            配置字典 (用于获取配置参数)
        """
        self.binance_klines = binance_kline_client
        self.order_flow = order_flow_processor
        self.coinalyze = coinalyze_client
        self.sentiment = sentiment_client
        self.binance_derivatives = binance_derivatives_client
        self.binance_orderbook = binance_orderbook_client
        self.orderbook_processor = orderbook_processor
        self.config = config or {}
        self.logger = logger or logging.getLogger(__name__)

        # v18 Item 16: Separate OrderFlowProcessor for 4H CVD (stateful _cvd_history)
        self._order_flow_4h = OrderFlowProcessor(logger=self.logger)

        # OI 变化率计算缓存
        self._last_oi_usd: float = 0.0

    # =========================================================================
    # v7.0: 统一外部 API 数据获取 (生产 + 诊断共用)
    # =========================================================================

    def fetch_external_data(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "30m",
        current_price: float = 0,
        volatility: float = 0.02,
    ) -> Dict[str, Any]:
        """
        获取所有外部 API 数据，返回 analyze() 兼容的 key names。

        此方法是生产 on_timer() 和诊断 ai_decision.py 的共用入口，
        消除了两处 ~150-200 行的重复内联代码。

        逻辑 1:1 搬自 ai_strategy.py on_timer()。

        Parameters
        ----------
        symbol : str
            交易对 (default: "BTCUSDT")
        interval : str
            K线周期 (default: "30m")
        current_price : float
            当前价格 (用于 orderbook 处理)
        volatility : float
            波动率 (用于 orderbook 自适应参数, default: bb_bandwidth 0.02)

        Returns
        -------
        Dict with keys matching MultiAgentAnalyzer.analyze() parameters:
            - sentiment_report: Dict (always non-None, falls back to neutral)
            - order_flow_report: Dict | None
            - derivatives_report: Dict | None (with Binance FR injected)
            - orderbook_report: Dict | None
            - binance_derivatives_report: Dict | None
        """
        result = {
            'sentiment_report': None,
            'order_flow_report': None,
            'order_flow_report_4h': None,  # v18 Item 16: 4H CVD order flow
            'derivatives_report': None,
            'orderbook_report': None,
            'binance_derivatives_report': None,
        }

        # ========== 1. Sentiment (matches on_timer L1726-1746) ==========
        if self.sentiment:
            try:
                result['sentiment_report'] = self.sentiment.fetch()
            except Exception as e:
                self.logger.warning(f"⚠️ Sentiment fetch failed: {e}")

        # Default neutral fallback (prevents None being passed to AI)
        # v18.0: Added 'degraded' marker so AI can distinguish real neutral from API failure
        if result['sentiment_report'] is None:
            result['sentiment_report'] = {
                'long_short_ratio': 1.0,
                'long_account_pct': 50.0,
                'short_account_pct': 50.0,
                'positive_ratio': 0.5,
                'negative_ratio': 0.5,
                'net_sentiment': 0.0,
                'source': 'default_neutral',
                'degraded': True,
                'timestamp': None,
            }

        # ========== 2. Order flow (matches on_timer L1884-1907) ==========
        if self.binance_klines and self.order_flow:
            try:
                raw_klines = self.binance_klines.get_klines(
                    symbol=symbol,
                    interval=interval,
                    limit=50,
                )
                if raw_klines:
                    # v6.5: Strip last (incomplete) bar from REST API klines
                    completed_klines = raw_klines[:-1] if len(raw_klines) > 1 else raw_klines
                    result['order_flow_report'] = self.order_flow.process_klines(completed_klines)
                else:
                    self.logger.warning("⚠️ Failed to get Binance klines for order flow")
            except Exception as e:
                self.logger.warning(f"⚠️ Order flow processing failed: {e}")

        # ========== 2b. 4H Order flow (v18 Item 16: 4H CVD) ==========
        if self.binance_klines:
            try:
                raw_klines_4h = self.binance_klines.get_klines(
                    symbol=symbol,
                    interval='4h',
                    limit=20,
                )
                if raw_klines_4h:
                    completed_4h = raw_klines_4h[:-1] if len(raw_klines_4h) > 1 else raw_klines_4h
                    result['order_flow_report_4h'] = self._order_flow_4h.process_klines(completed_4h)
                else:
                    self.logger.debug("4H klines not available for CVD")
            except Exception as e:
                self.logger.warning(f"⚠️ 4H order flow processing failed: {e}")

        # ========== 3. Coinalyze derivatives (matches on_timer L1911-1925) ==========
        # v20.0: Use fetch_all_with_history() to get OI hourly history + L/S ratio history + trends.
        # fetch_all_with_history() adds 2 extra API calls (OI history + LS ratio) per 20-min cycle,
        # returning: open_interest_history, long_short_ratio_history, trends.oi_trend, trends.long_short_trend
        if self.coinalyze and self.coinalyze.is_enabled():
            try:
                result['derivatives_report'] = self.coinalyze.fetch_all_with_history()
            except Exception as e:
                self.logger.warning(f"⚠️ Derivatives fetch failed: {e}")

        # ========== 4. Binance Funding Rate injection (matches on_timer L1930-1975) ==========
        # Coinalyze fetch_all() returns OI + Liquidations only, NOT Funding Rate.
        # Binance FR must be fetched separately and injected.
        if self.binance_klines:
            try:
                binance_fr = self.binance_klines.get_funding_rate()
                if binance_fr:
                    if result['derivatives_report'] is None:
                        result['derivatives_report'] = {'enabled': True}
                    fr_dict = {
                        'current_pct': binance_fr.get('funding_rate_pct', 0),
                        'settled_pct': binance_fr.get('funding_rate_pct', 0),
                        'predicted_rate_pct': binance_fr.get('predicted_rate_pct'),
                        'premium_index': binance_fr.get('premium_index'),
                        'mark_price': binance_fr.get('mark_price', 0),
                        'index_price': binance_fr.get('index_price', 0),
                        'next_funding_countdown_min': binance_fr.get('next_funding_countdown_min'),
                        'source': 'binance_direct',
                    }
                    # Fetch history for trend analysis
                    try:
                        fr_history = self.binance_klines.get_funding_rate_history(limit=10)
                        if fr_history and len(fr_history) >= 2:
                            history_list = []
                            for h in fr_history:
                                rate = float(h.get('fundingRate', 0))
                                history_list.append({
                                    'rate_pct': round(rate * 100, 6),
                                    'time': h.get('fundingTime'),
                                })
                            fr_dict['history'] = history_list
                            # Calculate trend using directional change / |first|
                            # Old approach (rates[-1] > rates[0]*1.1) was wrong
                            # for negative rates: multiplying a negative by 1.1
                            # makes the threshold more negative, causing tiny
                            # increases in negativity to be misclassified as RISING.
                            rates = [entry['rate_pct'] for entry in history_list]
                            first_rate = rates[0]
                            last_rate = rates[-1]
                            abs_first = abs(first_rate)
                            if abs_first < 0.0001:
                                # Near-zero: use absolute change threshold
                                change = last_rate - first_rate
                                if change > 0.001:
                                    fr_dict['trend'] = 'RISING'
                                elif change < -0.001:
                                    fr_dict['trend'] = 'FALLING'
                                else:
                                    fr_dict['trend'] = 'STABLE'
                            else:
                                pct_change = (last_rate - first_rate) / abs_first
                                if pct_change > 0.1:
                                    fr_dict['trend'] = 'RISING'
                                elif pct_change < -0.1:
                                    fr_dict['trend'] = 'FALLING'
                                else:
                                    fr_dict['trend'] = 'STABLE'
                    except Exception as e:
                        self.logger.debug(f"History is best-effort: {e}")
                        pass  # History is best-effort
                    result['derivatives_report']['funding_rate'] = fr_dict
            except Exception as e:
                self.logger.warning(f"⚠️ Binance funding rate merge failed: {e}")

        # ========== 5. Order book (matches on_timer L1979-2010) ==========
        if self.binance_orderbook and self.orderbook_processor:
            if current_price <= 0:
                # current_price=0 makes OBI, slippage, and anomaly threshold calculations invalid.
                # Skip orderbook entirely rather than returning garbage data to AI.
                self.logger.warning(
                    "⚠️ Skipping orderbook processing: current_price=0 "
                    "(OBI/slippage calculations require a valid reference price)"
                )
            else:
                try:
                    raw_orderbook = self.binance_orderbook.get_order_book(
                        symbol=symbol,
                        limit=100,
                    )
                    if raw_orderbook:
                        result['orderbook_report'] = self.orderbook_processor.process(
                            order_book=raw_orderbook,
                            current_price=current_price,
                            volatility=volatility,
                        )
                    else:
                        self.logger.warning("⚠️ Failed to get order book data")
                except Exception as e:
                    self.logger.warning(f"⚠️ Order book processing failed: {e}")

        # ========== 6. Binance derivatives (matches on_timer L2014-2026) ==========
        if self.binance_derivatives:
            try:
                result['binance_derivatives_report'] = self.binance_derivatives.fetch_all()
            except Exception as e:
                self.logger.warning(f"⚠️ Binance derivatives fetch failed: {e}")

        # ========== 7. Fear & Greed Index (v2.0 Phase 1) ==========
        try:
            from utils.fear_greed_client import FearGreedClient
            fg_client = FearGreedClient()
            fg_data = fg_client.fetch()
            if fg_data:
                result['fear_greed_report'] = fg_data
        except Exception as e:
            self.logger.debug(f"Fear & Greed fetch skipped: {e}")

        return result

    # =========================================================================
    # DEPRECATED: 以下方法仅供旧测试脚本使用，生产代码请用 fetch_external_data()
    # =========================================================================

    def assemble(
        self,
        technical_data: Dict[str, Any],
        position_data: Optional[Dict[str, Any]] = None,
        symbol: str = "BTCUSDT",
        interval: str = "30m",
        indicator_manager=None,
    ) -> Dict[str, Any]:
        """
        DEPRECATED: 仅供旧测试脚本使用。生产代码请用 fetch_external_data()。

        此方法的输出格式与 MultiAgentAnalyzer.analyze() 参数不兼容，
        生产 on_timer() 从未使用此方法。

        Parameters
        ----------
        technical_data : Dict
            技术指标数据 (来自 indicator_manager.get_technical_data())
        position_data : Dict, optional
            当前持仓信息
        symbol : str
            交易对
        interval : str
            K线周期
        indicator_manager : TechnicalIndicatorManager, optional
            技术指标管理器 (用于获取 historical_context) - v3.0.1 新增

        Returns
        -------
        Dict
            完整的 AI 输入数据字典
        """
        # Step 1: 获取 Binance 完整 K线 (12 列)
        raw_klines = self.binance_klines.get_klines(
            symbol=symbol,
            interval=interval,
            limit=50,
        )

        # Step 2: 处理订单流数据
        # v6.5: Binance klines API always returns the current (incomplete) bar as
        # the last element. Feeding it to order flow processor causes volume artifacts
        # (e.g., 0.03x volume ratio when the bar just started). Strip the incomplete
        # bar for order flow calculation, but keep it for current price extraction.
        if raw_klines:
            current_price = float(raw_klines[-1][4])  # Latest close = most recent price
            # Use only completed bars for order flow analysis
            completed_klines = raw_klines[:-1] if len(raw_klines) > 1 else raw_klines
            order_flow_data = self.order_flow.process_klines(completed_klines)
        else:
            self.logger.warning("⚠️ Failed to get Binance klines, using degraded mode")
            order_flow_data = self.order_flow._default_result()
            current_price = technical_data.get('price', 0)

        # Step 3: 获取 Coinalyze 衍生品数据
        # v35.1-fix: Use fetch_all_with_history() to match production fetch_external_data()
        # (v20.0+). Previous fetch_all() missed trends/history data.
        coinalyze_data = self.coinalyze.fetch_all_with_history()

        # Step 4: 转换衍生品数据格式
        derivatives = self._convert_derivatives(
            oi_raw=coinalyze_data.get('open_interest'),
            liq_raw=coinalyze_data.get('liquidations'),
            current_price=current_price,
        )
        # Pass through trends computed by fetch_all_with_history()
        if coinalyze_data.get('trends'):
            derivatives['trends'] = coinalyze_data['trends']

        # Step 5: 获取情绪数据
        sentiment_data = self.sentiment.fetch()
        if sentiment_data is None:
            sentiment_data = self._default_sentiment()

        # Step 6: 获取 Binance 衍生品数据 (大户数据等) - v3.0 新增
        binance_derivatives_data = None
        if self.binance_derivatives:
            try:
                binance_derivatives_data = self.binance_derivatives.fetch_all(
                    symbol=symbol,
                    period=interval,
                    history_limit=10,
                )
            except Exception as e:
                self.logger.warning(f"⚠️ Binance derivatives fetch error: {e}")

        # Step 7: 获取订单簿数据 - v3.7 新增
        orderbook_data = None
        if self.binance_orderbook and self.orderbook_processor:
            try:
                raw_orderbook = self.binance_orderbook.get_order_book(symbol=symbol)
                if raw_orderbook:
                    # 获取波动率用于自适应调整
                    volatility = self._get_recent_volatility(technical_data)
                    orderbook_data = self.orderbook_processor.process(
                        order_book=raw_orderbook,
                        current_price=current_price,
                        volatility=volatility,
                    )
                else:
                    orderbook_data = self._no_data_orderbook("API returned None")
            except Exception as e:
                self.logger.warning(f"⚠️ Order book fetch error: {e}")
                orderbook_data = self._no_data_orderbook(str(e))

        # Step 8: 获取历史上下文
        # count=35 确保 MACD 历史计算有足够数据 (slow_period=26 + 5 + buffer)
        historical_context = None
        if indicator_manager is not None:
            try:
                historical_context = indicator_manager.get_historical_context(count=35)
                self.logger.debug(
                    f"Historical context: trend={historical_context.get('trend_direction')}, "
                    f"momentum={historical_context.get('momentum_shift')}"
                )
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to get historical context: {e}")
                historical_context = {
                    "error": str(e),
                    "trend_direction": "ERROR",
                    "momentum_shift": "ERROR",
                }

        # Step 9: 组装最终数据
        return {
            "price": {
                "current": current_price,
                "change_pct": self._calc_change(raw_klines) if raw_klines else 0,
            },
            "technical": technical_data,
            "historical_context": historical_context,  # v3.0.1 新增
            "order_flow": order_flow_data,
            "derivatives": derivatives,
            "sentiment": sentiment_data,
            "binance_derivatives": binance_derivatives_data,  # v3.0 新增
            "order_book": orderbook_data,  # v3.7 新增
            "current_position": position_data or {},
            "_metadata": {
                "kline_source": "binance_raw" if raw_klines else "none",
                "coinalyze_enabled": self.coinalyze.is_enabled(),
                "binance_derivatives_enabled": self.binance_derivatives is not None,
                "orderbook_enabled": self.binance_orderbook is not None,
                "orderbook_status": orderbook_data.get("_status", {}).get("code") if orderbook_data else "DISABLED",
                "historical_context_enabled": historical_context is not None,  # v3.0.1 新增
            },
        }

    def _convert_derivatives(
        self,
        oi_raw: Optional[Dict],
        liq_raw: Optional[Dict],
        current_price: float,
    ) -> Dict[str, Any]:
        """
        Coinalyze API (OI + Liquidations) + Binance API (Funding Rate) → 统一格式转换
        """
        result = {
            "open_interest": None,
            "liquidations": None,
            "funding_rate": None,
            "enabled": True,
        }

        # OI 转换 (BTC → USD)
        if oi_raw:
            try:
                oi_btc = float(oi_raw.get('value', 0))
                oi_usd = oi_btc * current_price if current_price > 0 else 0

                # 计算变化率 (首次为 None)
                change_pct = None
                if self._last_oi_usd > 0 and oi_usd > 0:
                    change_pct = round(
                        (oi_usd - self._last_oi_usd) / self._last_oi_usd * 100, 2
                    )
                self._last_oi_usd = oi_usd

                result["open_interest"] = {
                    "value": round(oi_btc, 2),
                    "total_usd": round(oi_usd, 0),
                    "total_btc": round(oi_btc, 2),
                    "change_pct": change_pct,
                }
            except Exception as e:
                self.logger.warning(f"⚠️ OI parse error: {e}")

        # Funding 转换 (v3.22→v5.1: 完全以 Binance 为准)
        # premiumIndex.lastFundingRate = 预期费率, /fundingRate = 已结算费率
        binance_funding = None
        try:
            binance_funding = self.binance_klines.get_funding_rate()
        except Exception as e:
            self.logger.debug(f"⚠️ Binance funding rate fetch error: {e}")

        # 获取 Binance 资金费率结算历史 (最近 10 次)
        binance_funding_history = None
        try:
            binance_funding_history = self.binance_klines.get_funding_rate_history(limit=10)
        except Exception as e:
            self.logger.debug(f"⚠️ Binance funding rate history fetch error: {e}")

        if binance_funding:
            # v5.1: funding_rate = 已结算费率, predicted_rate = 预期费率 (from lastFundingRate)
            funding_rate = binance_funding['funding_rate']        # 已结算
            funding_pct = binance_funding['funding_rate_pct']     # 已结算 (%)

            # 构建历史趋势
            history_rates = []
            if binance_funding_history:
                for record in binance_funding_history:
                    try:
                        rate = float(record.get('fundingRate', 0))
                        history_rates.append({
                            "time": record.get('fundingTime'),
                            "rate": rate,
                            "rate_pct": round(rate * 100, 6),
                            "mark_price": record.get('markPrice'),
                        })
                    except (ValueError, TypeError) as e:
                        self.logger.debug(f"Using default value, original error: {e}")
                        continue

            # 计算历史趋势方向
            funding_trend = "N/A"
            if len(history_rates) >= 3:
                recent_3 = [r['rate'] for r in history_rates[-3:]]
                if recent_3[-1] > recent_3[0] * 1.1:
                    funding_trend = "RISING"
                elif recent_3[-1] < recent_3[0] * 0.9:
                    funding_trend = "FALLING"
                else:
                    funding_trend = "STABLE"

            result["funding_rate"] = {
                "value": funding_rate,              # 已结算费率 (向后兼容)
                "current": funding_rate,
                "current_pct": funding_pct,          # 消费者兼容 (format_complete_report + multi_agent_analyzer 读此字段)
                "settled": funding_rate,             # 已结算费率 (明确语义)
                "settled_pct": funding_pct,          # 已结算费率 (%)
                "interpretation": self._interpret_funding(funding_rate),
                "source": "binance_8h",
                "period": "8h",
                # v5.1: 预期费率直接来自 premiumIndex.lastFundingRate (非自算)
                "predicted_rate": binance_funding.get('predicted_rate'),
                "predicted_rate_pct": binance_funding.get('predicted_rate_pct'),
                # v3.22: 下次结算时间
                "next_funding_time": binance_funding.get('next_funding_time'),
                "next_funding_countdown_min": binance_funding.get('next_funding_countdown_min'),
                # v3.22: 结算历史 (最近 10 次)
                "history": history_rates,
                "trend": funding_trend,
                # v3.22: 溢价指数
                "premium_index": binance_funding.get('premium_index'),
                "mark_price": binance_funding.get('mark_price'),
                "index_price": binance_funding.get('index_price'),
                }
        # If Binance is unavailable, funding_rate stays None (neutral for AI).

        # Liquidation 转换 (嵌套结构)
        # v2.1: 即使 history 为空也返回结构 (区分"无爆仓"和"数据缺失")
        if liq_raw:
            try:
                history = liq_raw.get('history', [])

                # 计算总爆仓量 (BTC → USD)
                long_liq_btc = 0.0
                short_liq_btc = 0.0
                if history:
                    for item in history:
                        long_liq_btc += float(item.get('l', 0))
                        short_liq_btc += float(item.get('s', 0))

                long_liq_usd = long_liq_btc * current_price if current_price > 0 else 0
                short_liq_usd = short_liq_btc * current_price if current_price > 0 else 0

                result["liquidations"] = {
                    "history": history,
                    "has_data": len(history) > 0,  # 明确标记数据状态
                    "long_btc": round(long_liq_btc, 4),
                    "short_btc": round(short_liq_btc, 4),
                    "long_usd": round(long_liq_usd, 2),
                    "short_usd": round(short_liq_usd, 2),
                    "total_usd": round(long_liq_usd + short_liq_usd, 2),
                }
            except Exception as e:
                self.logger.warning(f"⚠️ Liquidation parse error: {e}")

        return result

    def _interpret_funding(self, funding_rate: float) -> str:
        """解读资金费率"""
        if funding_rate > 0.001:  # > 0.1%
            return "VERY_BULLISH"
        elif funding_rate > 0.0005:  # > 0.05%
            return "BULLISH"
        elif funding_rate < -0.001:  # < -0.1%
            return "VERY_BEARISH"
        elif funding_rate < -0.0005:  # < -0.05%
            return "BEARISH"
        else:
            return "NEUTRAL"

    def _calc_change(self, klines: List) -> float:
        """计算涨跌幅 (基于 K线数据)"""
        if not klines or len(klines) < 2:
            return 0.0
        old_close = float(klines[0][4])
        new_close = float(klines[-1][4])
        return round((new_close - old_close) / old_close * 100, 2) if old_close > 0 else 0.0

    def _default_sentiment(self) -> Dict[str, Any]:
        """默认情绪数据 (中性)"""
        return {
            'positive_ratio': 0.5,
            'negative_ratio': 0.5,
            'net_sentiment': 0.0,
            'long_short_ratio': 1.0,
            'source': 'default_neutral',
            'degraded': True,  # v18.0: consistent with fetch_external_data fallback
        }

    def format_complete_report(self, data: Dict[str, Any]) -> str:
        """
        DEPRECATED: 生产代码从未使用此方法。v46.0 AI format methods 已删除。

        仅供测试脚本 (validate_data_pipeline.py) 使用。

        Parameters
        ----------
        data : Dict
            assemble() 返回的完整数据

        Returns
        -------
        str
            格式化的完整市场数据报告
        """
        current_price = data.get("price", {}).get("current", 0)
        parts = []

        # =========================================================================
        # 1. 价格和技术指标
        # =========================================================================
        parts.append("=" * 50)
        parts.append("MARKET DATA REPORT")
        parts.append("=" * 50)

        price_data = data.get("price", {})
        parts.append(f"\nPRICE: ${current_price:,.2f} ({price_data.get('change_pct', 0):+.2f}%)")

        # =========================================================================
        # 2. 订单流数据
        # =========================================================================
        order_flow = data.get("order_flow", {})
        if order_flow:
            parts.append("\nORDER FLOW (from Binance Klines):")
            parts.append(f"  - Buy Ratio: {order_flow.get('buy_ratio', 0.5):.1%}")
            parts.append(f"  - CVD Trend: {order_flow.get('cvd_trend', 'N/A')}")
            parts.append(f"  - Avg Trade Size: ${order_flow.get('avg_trade_size', 0):,.0f}")

        # =========================================================================
        # 3. Coinalyze 衍生品数据 (含趋势)
        # =========================================================================
        derivatives = data.get("derivatives", {})
        if derivatives:
            parts.append("\nCOINALYZE DERIVATIVES:")
            trends = derivatives.get("trends", {})

            # OI
            oi = derivatives.get("open_interest")
            if oi:
                oi_trend = trends.get("oi_trend", "N/A")
                parts.append(
                    f"  - Open Interest: {oi.get('total_btc', 0):,.0f} BTC "
                    f"(${oi.get('total_usd', 0):,.0f}) [Trend: {oi_trend}]"
                )

            # Funding Rate (v3.22: 增强版 — 当前 + 预期 + 历史趋势)
            fr = derivatives.get("funding_rate")
            if fr:
                fr_trend = fr.get("trend", "N/A")
                parts.append(
                    f"  - Funding Rate (last settled): {fr.get('current_pct', 0):.5f}% "
                    f"({fr.get('interpretation', 'N/A')}) [Trend: {fr_trend}]"
                )
                # 溢价指数 + 预期费率
                premium_index = fr.get('premium_index')
                if premium_index is not None:
                    pi_pct = premium_index * 100
                    mark = fr.get('mark_price', 0)
                    index = fr.get('index_price', 0)
                    parts.append(
                        f"  - Premium Index: {pi_pct:+.4f}% "
                        f"(Mark: ${mark:,.2f}, Index: ${index:,.2f})"
                    )
                predicted_pct = fr.get('predicted_rate_pct')
                if predicted_pct is not None:
                    parts.append(
                        f"  - Predicted Next Funding Rate: {predicted_pct:.5f}%"
                    )
                # 下次结算倒计时
                countdown = fr.get('next_funding_countdown_min')
                if countdown is not None:
                    hours = countdown // 60
                    mins = countdown % 60
                    parts.append(
                        f"  - Next Settlement: {hours}h {mins}m"
                    )
                # 历史 (最近 10 次结算)
                history = fr.get('history', [])
                if history and len(history) >= 2:
                    rates_str = " → ".join(
                        [f"{r['rate_pct']:.5f}%" for r in history]
                    )
                    parts.append(f"  - Settlement History (last {len(history)}): {rates_str}")

            # Liquidations (v3.24: 24h)
            liq = derivatives.get("liquidations")
            if liq:
                parts.append(
                    f"  - Liquidations (24h): Long ${liq.get('long_usd', 0):,.0f} / "
                    f"Short ${liq.get('short_usd', 0):,.0f}"
                )

            # Long/Short Ratio (from Coinalyze)
            ls_hist = derivatives.get("long_short_ratio_history")
            if ls_hist and ls_hist.get("history"):
                latest = ls_hist["history"][-1]
                ls_trend = trends.get("long_short_trend", "N/A")
                parts.append(
                    f"  - Long/Short Ratio (Coinalyze): {latest.get('r', 1):.2f} "
                    f"(Long {latest.get('l', 50):.1f}% / Short {latest.get('s', 50):.1f}%) "
                    f"[Trend: {ls_trend}]"
                )

        # =========================================================================
        # 4. Binance 衍生品数据 (大户数据、Taker 比)
        # =========================================================================
        binance_deriv = data.get("binance_derivatives")
        if binance_deriv:
            parts.append("\nBINANCE DERIVATIVES (Unique Data):")

            # 大户持仓比
            top_pos = binance_deriv.get("top_long_short_position", {})
            latest = top_pos.get("latest")
            if latest:
                ratio = float(latest.get("longShortRatio", 1))
                long_pct = float(latest.get("longAccount", 0.5)) * 100
                trend = top_pos.get("trend", "N/A")
                parts.append(
                    f"  - Top Traders Position: Long {long_pct:.1f}% "
                    f"(Ratio: {ratio:.2f}) [Trend: {trend}]"
                )

            # Taker 买卖比
            taker = binance_deriv.get("taker_long_short", {})
            latest = taker.get("latest")
            if latest:
                ratio = float(latest.get("buySellRatio", 1))
                trend = taker.get("trend", "N/A")
                parts.append(f"  - Taker Buy/Sell Ratio: {ratio:.3f} [Trend: {trend}]")

            # OI 趋势 (Binance)
            oi_hist = binance_deriv.get("open_interest_hist", {})
            latest = oi_hist.get("latest")
            if latest:
                oi_usd = float(latest.get("sumOpenInterestValue", 0))
                trend = oi_hist.get("trend", "N/A")
                parts.append(f"  - OI (Binance): ${oi_usd:,.0f} [Trend: {trend}]")

            # 24h 统计
            ticker = binance_deriv.get("ticker_24hr")
            if ticker:
                change_pct = float(ticker.get("priceChangePercent", 0))
                volume = float(ticker.get("quoteVolume", 0))
                parts.append(
                    f"  - 24h Stats: Change {change_pct:+.2f}%, Volume ${volume:,.0f}"
                )

        # =========================================================================
        # 5. 市场情绪 (Binance 多空比)
        # =========================================================================
        sentiment = data.get("sentiment", {})
        if sentiment:
            parts.append("\nMARKET SENTIMENT (Binance Global L/S Ratio):")
            parts.append(
                f"  - Long: {sentiment.get('positive_ratio', 0.5):.1%} / "
                f"Short: {sentiment.get('negative_ratio', 0.5):.1%}"
            )
            parts.append(f"  - Net Sentiment: {sentiment.get('net_sentiment', 0):+.3f}")
            parts.append(f"  - L/S Ratio: {sentiment.get('long_short_ratio', 1):.2f}")
            # v3.24: Show history series
            history = sentiment.get('history', [])
            if history and len(history) >= 2:
                long_series = [f"{h['long']*100:.1f}%" for h in history]
                parts.append(f"  - Long% History: {' → '.join(long_series)}")

        # =========================================================================
        # 6. 数据源状态
        # =========================================================================
        metadata = data.get("_metadata", {})
        parts.append("\nDATA SOURCES:")
        parts.append(f"  - Klines: {metadata.get('kline_source', 'unknown')}")
        parts.append(f"  - Coinalyze: {'enabled' if metadata.get('coinalyze_enabled') else 'disabled'}")
        parts.append(f"  - Binance Derivatives: {'enabled' if metadata.get('binance_derivatives_enabled') else 'disabled'}")

        # =========================================================================
        # 7. 订单簿深度数据 (v3.7 新增)
        # =========================================================================
        order_book = data.get("order_book")
        if order_book:
            status = order_book.get("_status", {})
            status_code = status.get("code", "UNKNOWN")

            parts.append("\nORDER BOOK DEPTH (Binance, 100 levels):")
            parts.append(f"  Status: {status_code}")

            # v2.0: 处理 NO_DATA 状态
            if status_code == "NO_DATA":
                parts.append(f"  Reason: {status.get('message', 'Unknown')}")
                parts.append("  [All metrics unavailable - do not assume neutral market]")
            else:
                # OBI
                obi = order_book.get("obi", {})
                if obi:
                    parts.append(f"  Simple OBI: {obi.get('simple', 0):+.3f}")
                    decay = obi.get('decay_used', 0.8)
                    parts.append(f"  Weighted OBI: {obi.get('adaptive_weighted', 0):+.3f} (decay={decay})")
                    parts.append(
                        f"  Bid Volume: ${obi.get('bid_volume_usd', 0)/1e6:.1f}M "
                        f"({obi.get('bid_volume_btc', 0):.1f} BTC)"
                    )
                    parts.append(
                        f"  Ask Volume: ${obi.get('ask_volume_usd', 0)/1e6:.1f}M "
                        f"({obi.get('ask_volume_btc', 0):.1f} BTC)"
                    )

                # v2.0: Dynamics
                dynamics = order_book.get("dynamics", {})
                if dynamics and dynamics.get("samples_count", 0) > 0:
                    parts.append("  DYNAMICS (vs previous):")
                    if dynamics.get("obi_change") is not None:
                        parts.append(
                            f"    OBI Change: {dynamics['obi_change']:+.4f} "
                            f"({dynamics.get('obi_change_pct', 0):+.1f}%)"
                        )
                    if dynamics.get("bid_depth_change_pct") is not None:
                        parts.append(f"    Bid Depth: {dynamics['bid_depth_change_pct']:+.1f}%")
                    if dynamics.get("ask_depth_change_pct") is not None:
                        parts.append(f"    Ask Depth: {dynamics['ask_depth_change_pct']:+.1f}%")
                    parts.append(f"    Trend: {dynamics.get('trend', 'N/A')}")

                # v2.0: Pressure Gradient
                gradient = order_book.get("pressure_gradient", {})
                if gradient:
                    parts.append("  PRESSURE GRADIENT:")
                    parts.append(
                        f"    Bid: {gradient.get('bid_near_5', 0):.0%} near-5, "
                        f"{gradient.get('bid_near_10', 0):.0%} near-10 "
                        f"[{gradient.get('bid_concentration', 'N/A')}]"
                    )
                    parts.append(
                        f"    Ask: {gradient.get('ask_near_5', 0):.0%} near-5, "
                        f"{gradient.get('ask_near_10', 0):.0%} near-10 "
                        f"[{gradient.get('ask_concentration', 'N/A')}]"
                    )

                # 异常
                anomalies = order_book.get("anomalies", {})
                if anomalies and anomalies.get("has_significant"):
                    threshold = anomalies.get("threshold_used", 3.0)
                    reason = anomalies.get("threshold_reason", "normal")
                    parts.append(f"  ANOMALIES (threshold={threshold}x, {reason}):")
                    for a in anomalies.get("bid_anomalies", [])[:3]:  # 最多显示3个
                        parts.append(
                            f"    Bid @ ${a['price']:,.0f}: {a['volume_btc']:.0f} BTC "
                            f"({a['multiplier']:.1f}x)"
                        )
                    for a in anomalies.get("ask_anomalies", [])[:3]:  # 最多显示3个
                        parts.append(
                            f"    Ask @ ${a['price']:,.0f}: {a['volume_btc']:.0f} BTC "
                            f"({a['multiplier']:.1f}x)"
                        )

                # v2.0: 滑点 (含置信度)
                liquidity = order_book.get("liquidity", {})
                if liquidity:
                    parts.append(f"  Spread: {liquidity.get('spread_pct', 0):.3f}%")
                    slippage = liquidity.get("slippage", {})
                    if slippage.get("buy_1.0_btc"):
                        s = slippage["buy_1.0_btc"]
                        if s.get("estimated") is not None:
                            parts.append(
                                f"  Slippage (Buy 1 BTC): {s['estimated']:.3f}% "
                                f"[conf={s['confidence']:.0%}, range={s['range'][0]:.3f}%-{s['range'][1]:.3f}%]"
                            )

        # =========================================================================
        # 8. 历史上下文
        # =========================================================================
        historical = data.get("historical_context")
        if historical and historical.get("trend_direction") not in ["INSUFFICIENT_DATA", "ERROR"]:
            parts.append("\nHISTORICAL CONTEXT (Last 20 bars):")
            parts.append(
                f"  - Trend Direction: {historical.get('trend_direction', 'N/A')} "
                f"{historical.get('price_arrow', '')}"
            )
            parts.append(f"  - Momentum Shift: {historical.get('momentum_shift', 'N/A')}")
            parts.append(f"  - Price Change: {historical.get('price_change_pct', 0):+.2f}%")
            parts.append(f"  - Volume Ratio: {historical.get('current_volume_ratio', 1):.2f}x")
            parts.append(f"  - RSI Current: {historical.get('rsi_current', 0):.1f}")
            parts.append(f"  - MACD Current: {historical.get('macd_current', 0):.4f}")

            # 可视化趋势 (简化版)
            price_trend = historical.get('price_trend', [])
            if len(price_trend) >= 5:
                # 取最近5个点展示趋势
                trend_str = " → ".join([f"${p:,.0f}" for p in price_trend[-5:]])
                parts.append(f"  - Price Trend: {trend_str}")

        parts.append("\n" + "=" * 50)

        return "\n".join(parts)

    def _get_recent_volatility(self, technical_data: Dict) -> float:
        """
        获取近期波动率 (用于自适应参数)

        Parameters
        ----------
        technical_data : Dict
            技术指标数据

        Returns
        -------
        float
            相对波动率 (ATR / price)
        """
        atr = technical_data.get("atr", 0)
        price = technical_data.get("price", 1)
        if price > 0:
            return atr / price  # 相对波动率
        return 0.02  # 默认 2%

    def _no_data_orderbook(self, reason: str) -> Dict:
        """
        返回 NO_DATA 状态订单簿 (v2.0 Critical)

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
        import time
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
