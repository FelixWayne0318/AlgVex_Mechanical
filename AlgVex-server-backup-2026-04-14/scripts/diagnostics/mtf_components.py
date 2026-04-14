"""
MTF Components Module

Tests Multi-Timeframe data collection components.
"""

import os
from typing import Dict, Optional

from .base import (
    DiagnosticContext,
    DiagnosticStep,
    fetch_binance_klines,
    mask_sensitive,
)


class MTFComponentTester(DiagnosticStep):
    """
    Test MTF v2.1 components integration.

    Tests:
    - BinanceKlineClient
    - OrderFlowProcessor
    - CoinalyzeClient
    - AIDataAssembler
    - OrderBookProcessor (if enabled)
    """

    name = "MTF v2.1 组件集成测试"

    def run(self) -> bool:
        print("-" * 70)

        try:
            # Test individual components
            self._test_binance_kline_client()
            self._test_order_flow_processor()
            self._test_coinalyze_client()
            self._test_ai_data_assembler()
            self._test_order_book()
            self._test_sr_zone_calculator()

            print()
            print("  ✅ MTF v2.1 + Order Book 组件集成测试完成")
            return True

        except Exception as e:
            self.ctx.add_error(f"MTF 组件测试失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _test_binance_kline_client(self) -> None:
        """Test BinanceKlineClient."""
        print("  [9.1] 测试 BinanceKlineClient...")
        try:
            from utils.binance_kline_client import BinanceKlineClient

            kline_client = BinanceKlineClient(timeout=10)
            print("     ✅ BinanceKlineClient 导入成功")

            # Test get_klines
            klines = kline_client.get_klines(
                symbol=self.ctx.symbol,
                interval="30m",
                limit=10
            )
            if klines and len(klines) > 0:
                print(f"     ✅ get_klines: 返回 {len(klines)} 根 K线")
            else:
                print("     ⚠️ get_klines 返回空数据")

        except ImportError as e:
            print(f"     ❌ 无法导入 BinanceKlineClient: {e}")
        except Exception as e:
            print(f"     ❌ BinanceKlineClient 测试失败: {e}")

    def _test_order_flow_processor(self) -> None:
        """Test OrderFlowProcessor."""
        print()
        print("  [9.2] 测试 OrderFlowProcessor...")
        try:
            from utils.order_flow_processor import OrderFlowProcessor
            from utils.binance_kline_client import BinanceKlineClient

            processor = OrderFlowProcessor(logger=None)
            print("     ✅ OrderFlowProcessor 导入成功")

            kline_client = BinanceKlineClient(timeout=10)
            klines = kline_client.get_klines(
                symbol=self.ctx.symbol,
                interval="30m",
                limit=10
            )

            if klines:
                result = processor.process_klines(klines)
                if result:
                    print(f"     ✅ process_klines: buy_ratio={result.get('buy_ratio', 0):.4f}")
                    cvd_trend = result.get('cvd_trend', 'N/A')
                    print(f"        cvd_trend: {cvd_trend}")
                    print(f"        volume_usdt: ${result.get('volume_usdt', 0):,.0f}")
                    # v5.6: Validate CVD cold start bootstrap
                    cvd_history_len = len(processor._cvd_history) if hasattr(processor, '_cvd_history') else 0
                    if cvd_history_len >= 5:
                        print(f"        cvd_history: {cvd_history_len} bars (✅ 已初始化)")
                    elif cvd_history_len > 0:
                        print(f"        cvd_history: {cvd_history_len} bars (⚠️ COLD_START, 需 ≥5 bars)")
                    else:
                        print(f"        cvd_history: 0 bars (❌ 未初始化)")
                    if cvd_trend == 'COLD_START':
                        print(f"        ⚠️ CVD 仍处于冷启动状态，需更多数据")

        except ImportError as e:
            print(f"     ❌ 无法导入 OrderFlowProcessor: {e}")
        except Exception as e:
            print(f"     ❌ OrderFlowProcessor 测试失败: {e}")

    def _test_coinalyze_client(self) -> None:
        """Test CoinalyzeClient."""
        print()
        print("  [9.3] 测试 CoinalyzeClient...")
        try:
            from utils.coinalyze_client import CoinalyzeClient
            from utils.binance_kline_client import BinanceKlineClient

            coinalyze_cfg = self.ctx.base_config.get('order_flow', {}).get('coinalyze', {})
            coinalyze_enabled = coinalyze_cfg.get('enabled', False)
            coinalyze_api_key = coinalyze_cfg.get('api_key') or os.getenv('COINALYZE_API_KEY')

            coinalyze_client = CoinalyzeClient(
                api_key=coinalyze_api_key,
                timeout=coinalyze_cfg.get('timeout', 10),
                max_retries=coinalyze_cfg.get('max_retries', 2),
                logger=None
            )
            print("     ✅ CoinalyzeClient 导入成功")

            if not coinalyze_enabled:
                print("     ℹ️ Coinalyze 未启用")
            elif not coinalyze_api_key:
                print("     ⚠️ Coinalyze API Key 未配置")
            else:
                print(f"     📊 Coinalyze API 测试 (Key: {mask_sensitive(coinalyze_api_key)})")

                symbol = coinalyze_cfg.get('symbol', 'BTCUSDT_PERP.A')

                # Test Open Interest
                oi_data = coinalyze_client.get_open_interest(symbol=symbol)
                if oi_data:
                    bc = self.ctx.base_currency
                    oi_val = oi_data.get('value', 0)
                    oi_usd = float(oi_val) * self.ctx.current_price if self.ctx.current_price else 0
                    print(f"        ✅ OI: ${oi_usd:,.0f} ({float(oi_val):,.2f} {bc})")
                else:
                    print("        ❌ OI 获取失败")

                # Test Funding Rate (使用 Binance 作为主要数据源)
                kline_client = BinanceKlineClient(timeout=10)
                binance_fr = kline_client.get_funding_rate(symbol=self.ctx.symbol)
                if binance_fr:
                    print(f"        ✅ Settled FR: {binance_fr.get('funding_rate_pct', 0):.5f}% | Predicted FR: {binance_fr.get('predicted_rate_pct', 0):.5f}%")
                    # v4.8: 保存 Binance funding rate 到 context (主要数据源)
                    self.ctx.binance_funding_rate = binance_fr

        except ImportError as e:
            print(f"     ❌ 无法导入 CoinalyzeClient: {e}")
        except Exception as e:
            print(f"     ❌ CoinalyzeClient 测试失败: {e}")

    def _test_ai_data_assembler(self) -> None:
        """Test AIDataAssembler."""
        print()
        print("  [9.4] 测试 AIDataAssembler...")
        try:
            from utils.ai_data_assembler import AIDataAssembler
            from utils.binance_kline_client import BinanceKlineClient
            from utils.order_flow_processor import OrderFlowProcessor
            from utils.coinalyze_client import CoinalyzeClient
            from utils.sentiment_client import SentimentDataFetcher

            kline_client = BinanceKlineClient(timeout=10)
            processor = OrderFlowProcessor(logger=None)

            coinalyze_cfg = self.ctx.base_config.get('order_flow', {}).get('coinalyze', {})
            coinalyze_api_key = coinalyze_cfg.get('api_key') or os.getenv('COINALYZE_API_KEY')
            coinalyze_client = CoinalyzeClient(
                api_key=coinalyze_api_key,
                timeout=10,
                logger=None
            )

            sentiment_client = SentimentDataFetcher()

            assembler = AIDataAssembler(
                binance_kline_client=kline_client,
                order_flow_processor=processor,
                coinalyze_client=coinalyze_client,
                sentiment_client=sentiment_client,
                logger=None
            )
            print("     ✅ AIDataAssembler 导入成功")

            assembled = assembler.assemble(
                technical_data=self.ctx.technical_data,
                position_data=self.ctx.current_position,
                symbol=self.ctx.symbol,
                interval=self.ctx.interval
            )

            print(f"     ✅ 数据组装完成:")

            # Validate content quality, not just existence
            tech = assembled.get('technical')
            if tech and isinstance(tech, dict):
                tech_keys = len(tech)
                has_rsi = 'rsi' in tech
                print(f"        - 技术指标: ✅ {tech_keys} 字段 (RSI={'有' if has_rsi else '缺'})")
            else:
                print(f"        - 技术指标: ❌ 缺失或非 dict")

            order_flow = assembled.get('order_flow')
            if order_flow and isinstance(order_flow, dict):
                of_keys = list(order_flow.keys())[:3]
                print(f"        - 订单流: ✅ keys={of_keys}")
            elif order_flow is None:
                print(f"        - 订单流: ⚠️ None (API 可能未返回)")
            else:
                print(f"        - 订单流: ❌ 格式异常: {type(order_flow)}")

            derivatives = assembled.get('derivatives')
            if derivatives and isinstance(derivatives, dict):
                print(f"        - 衍生品: ✅ {len(derivatives)} 字段")
            else:
                print(f"        - 衍生品: ⚠️ {'None' if derivatives is None else type(derivatives).__name__}")

            sentiment = assembled.get('sentiment')
            if sentiment and isinstance(sentiment, dict):
                ls_ratio = sentiment.get('long_short_ratio')
                degraded = sentiment.get('degraded', False)
                if degraded:
                    print(f"        - 情绪数据: ⚠️ 降级模式 (synthetic neutral)")
                elif ls_ratio is not None:
                    print(f"        - 情绪数据: ✅ L/S ratio={ls_ratio:.4f}")
                else:
                    print(f"        - 情绪数据: ⚠️ 缺少 long_short_ratio")
            else:
                print(f"        - 情绪数据: ❌ 缺失")

        except ImportError as e:
            print(f"     ❌ 无法导入 AIDataAssembler: {e}")
        except Exception as e:
            print(f"     ❌ AIDataAssembler 测试失败: {e}")

    def _test_order_book(self) -> None:
        """Test Order Book components."""
        print()
        print("  [9.5] 测试 Order Book (v3.7)...")

        order_book_cfg = self.ctx.base_config.get('order_book', {})
        order_book_enabled = order_book_cfg.get('enabled', False)

        if not order_book_enabled:
            print("     ℹ️ Order Book 未启用 (order_book.enabled = false)")
            print("     → 若要启用，修改 configs/base.yaml: order_book.enabled: true")
            return

        try:
            from utils.binance_orderbook_client import BinanceOrderBookClient
            from utils.orderbook_processor import OrderBookProcessor

            ob_api_cfg = order_book_cfg.get('api', {})
            ob_proc_cfg = order_book_cfg.get('processing', {})

            ob_client = BinanceOrderBookClient(
                timeout=ob_api_cfg.get('timeout', 10),
                max_retries=ob_api_cfg.get('max_retries', 2),
                logger=None
            )
            print("     ✅ BinanceOrderBookClient 导入成功")

            weighted_obi_cfg = ob_proc_cfg.get('weighted_obi', {})
            anomaly_cfg = ob_proc_cfg.get('anomaly_detection', {})

            # Ensure all required keys are present (avoid KeyError)
            weighted_obi_config = {
                "base_decay": weighted_obi_cfg.get('base_decay', 0.8),
                "adaptive": weighted_obi_cfg.get('adaptive', True),
                "volatility_factor": weighted_obi_cfg.get('volatility_factor', 0.1),
                "min_decay": weighted_obi_cfg.get('min_decay', 0.5),
                "max_decay": weighted_obi_cfg.get('max_decay', 0.95),
            }

            ob_processor = OrderBookProcessor(
                price_band_pct=ob_proc_cfg.get('price_band_pct', 0.5),
                base_anomaly_threshold=anomaly_cfg.get('base_threshold', 3.0),
                slippage_amounts=ob_proc_cfg.get('slippage_amounts', [0.1, 0.5, 1.0]),
                weighted_obi_config=weighted_obi_config,
                history_size=ob_proc_cfg.get('history', {}).get('size', 10),
                logger=None
            )
            print("     ✅ OrderBookProcessor 导入成功")

            # Get order book
            ob_limit = ob_api_cfg.get('limit', 100)
            raw_ob = ob_client.get_order_book(symbol=self.ctx.symbol, limit=ob_limit)

            if raw_ob:
                bids = raw_ob.get('bids', [])
                asks = raw_ob.get('asks', [])
                print(f"     ✅ 订单簿获取成功: {len(bids)} bids, {len(asks)} asks")

                if bids and asks:
                    best_bid = float(bids[0][0])
                    best_ask = float(asks[0][0])
                    spread = best_ask - best_bid
                    spread_pct = (spread / best_bid) * 100
                    print(f"        盘口: Bid ${best_bid:,.2f} | Ask ${best_ask:,.2f}")
                    print(f"        Spread: ${spread:.2f} ({spread_pct:.4f}%)")

                # Process
                ob_result = ob_processor.process(
                    order_book=raw_ob,
                    current_price=self.ctx.current_price,
                    volatility=0.02
                )

                if ob_result:
                    obi = ob_result.get('obi', {})
                    print(f"        OBI Simple: {obi.get('simple', 0):+.4f}")
                    print(f"        OBI Adaptive: {obi.get('adaptive_weighted', 0):+.4f}")

        except ImportError as e:
            print(f"     ❌ 无法导入订单簿模块: {e}")
        except Exception as e:
            print(f"     ❌ Order Book 测试失败: {e}")

    def _test_sr_zone_calculator(self) -> None:
        """Test S/R Zone Calculator."""
        print()
        print("  [9.5.5] S/R Zone Calculator 测试 (v2.0):")
        try:
            from utils.sr_zone_calculator import SRZoneCalculator, SRLevel, SRSourceType
            print("     ✅ SRZoneCalculator 导入成功")

            # Get data from context
            test_bb_data = None
            test_sma_data = None

            if self.ctx.technical_data:
                bb_upper = self.ctx.technical_data.get('bb_upper')
                bb_lower = self.ctx.technical_data.get('bb_lower')
                if bb_upper and bb_lower:
                    test_bb_data = {
                        'upper': bb_upper,
                        'lower': bb_lower,
                        'middle': self.ctx.technical_data.get('bb_middle'),
                    }

                sma_50 = self.ctx.technical_data.get('sma_50')
                sma_200 = self.ctx.technical_data.get('sma_200')
                if sma_50 or sma_200:
                    test_sma_data = {'sma_50': sma_50, 'sma_200': sma_200}

            sr_calc = SRZoneCalculator(
                cluster_pct=0.5,
                zone_expand_pct=0.1,
                hard_control_threshold_pct=1.0,
            )

            sr_result = sr_calc.calculate(
                current_price=self.ctx.current_price,
                bb_data=test_bb_data,
                sma_data=test_sma_data,
                orderbook_anomalies=None,
                bars_data=self.ctx.sr_bars_data,
                bars_data_4h=self.ctx.bars_data_4h,
                bars_data_1d=self.ctx.bars_data_1d,
                daily_bar=self.ctx.daily_bar,
                weekly_bar=self.ctx.weekly_bar,
            )

            has_bars = bool(self.ctx.sr_bars_data)
            has_4h = bool(self.ctx.bars_data_4h)
            has_1d = bool(self.ctx.bars_data_1d)
            has_pivot = bool(self.ctx.daily_bar)
            print(f"     📊 当前价格: ${self.ctx.current_price:,.0f}")
            print(f"     📊 数据源: Swing30M={'✅' if has_bars else '❌'}, Swing4H={'✅' if has_4h else '❌'}, Swing1D={'✅' if has_1d else '❌'}, Pivot={'✅' if has_pivot else '❌'}")

            # Display resistance zones (v17.0: max 1)
            resistance_zones = sr_result.get('resistance_zones', [])
            bc = self.ctx.base_currency
            if resistance_zones:
                zone = resistance_zones[0]
                wall_info = ""
                if zone.has_order_wall:
                    w_usd = zone.wall_size_btc * self.ctx.current_price if self.ctx.current_price else 0
                    w_str = f"${w_usd/1e6:.1f}M" if w_usd >= 1e6 else f"${w_usd/1e3:.0f}K"
                    wall_info = f" [Wall: {w_str} ({zone.wall_size_btc:.1f} {bc})]"
                print(f"     🔴 阻力位: R1 ${zone.price_center:,.0f} ({zone.distance_pct:.1f}% away) [{zone.strength}]{wall_info}")
            else:
                print(f"     🔴 阻力位: None detected")

            # Display support zones (v17.0: max 1)
            support_zones = sr_result.get('support_zones', [])
            if support_zones:
                zone = support_zones[0]
                wall_info = ""
                if zone.has_order_wall:
                    w_usd = zone.wall_size_btc * self.ctx.current_price if self.ctx.current_price else 0
                    w_str = f"${w_usd/1e6:.1f}M" if w_usd >= 1e6 else f"${w_usd/1e3:.0f}K"
                    wall_info = f" [Wall: {w_str} ({zone.wall_size_btc:.1f} {bc})]"
                print(f"     🟢 支撑位: S1 ${zone.price_center:,.0f} ({zone.distance_pct:.1f}% away) [{zone.strength}]{wall_info}")
            else:
                print(f"     🟢 支撑位: None detected")

            # Hard control status (v3.16: AI 建议，非本地覆盖)
            hard_control = sr_result.get('hard_control', {})
            block_long = hard_control.get('block_long', False)
            block_short = hard_control.get('block_short', False)
            if block_long or block_short:
                print(f"     📋 AI 建议: 避免 LONG={block_long}, 避免 SHORT={block_short} (v3.16 AI 自主判断)")
            else:
                print(f"     ✅ S/R Zone 建议: 无限制")

            print("     ✅ S/R Zone Calculator 测试完成")

        except ImportError as e:
            print(f"     ❌ 无法导入 SRZoneCalculator: {e}")
        except Exception as e:
            print(f"     ❌ S/R Zone 测试失败: {e}")

    def should_skip(self) -> bool:
        return self.ctx.summary_mode


class TelegramChecker(DiagnosticStep):
    """
    Verify Telegram command handling + v14.0 dual-channel routing.

    Tests bot connectivity, command handler setup, and notification channel.
    """

    name = "Telegram 命令处理 + v14.0 双频道验证"

    def run(self) -> bool:
        print("-" * 70)

        try:
            import requests

            telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
            telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')

            if not telegram_token:
                self.ctx.add_warning("TELEGRAM_BOT_TOKEN 未配置")
                return True
            if not telegram_chat_id:
                self.ctx.add_warning("TELEGRAM_CHAT_ID 未配置")
                return True

            print(f"  ✅ Telegram 配置已加载")
            print(f"     Bot Token: {mask_sensitive(telegram_token)}")
            print(f"     Chat ID: {telegram_chat_id}")

            # Check module imports
            print()
            print("  📋 Telegram 模块检查:")

            from utils.telegram_bot import TelegramBot
            print("     ✅ TelegramBot 类可导入")

            if hasattr(TelegramBot, 'send_message_sync'):
                print("     ✅ TelegramBot.send_message_sync 方法存在")
            else:
                self.ctx.add_warning("TelegramBot.send_message_sync 方法缺失")

            from utils.telegram_command_handler import (
                TelegramCommandHandler,
                QUERY_COMMANDS,
                CONTROL_COMMANDS,
                CONTROL_COMMANDS_WITH_ARGS,
            )
            print("     ✅ TelegramCommandHandler 类可导入")

            # Check command dispatch registries (v3.0: commands dispatched via strategy_callback)
            required_query = ['status', 'position', 'balance', 'analyze', 'orders']
            required_control = ['pause', 'resume', 'close']
            required_control_args = ['force_analysis', 'modify_sl', 'modify_tp']

            print("     📋 命令注册检查 (dispatch registry):")
            for cmd in required_query:
                if cmd in QUERY_COMMANDS:
                    print(f"        ✅ query/{cmd} → '{QUERY_COMMANDS[cmd]}'")
                else:
                    print(f"        ⚠️ query/{cmd} 未注册")
            for cmd in required_control:
                if cmd in CONTROL_COMMANDS:
                    print(f"        ✅ control/{cmd} (PIN required)")
                else:
                    print(f"        ⚠️ control/{cmd} 未注册")
            for cmd in required_control_args:
                if cmd in CONTROL_COMMANDS_WITH_ARGS:
                    print(f"        ✅ control/{cmd} (with args)")
                else:
                    print(f"        ⚠️ control/{cmd} 未注册")

            # Test API connectivity
            print()
            print("  📤 Telegram API 连通性测试:")
            api_url = f"https://api.telegram.org/bot{telegram_token}/getMe"
            resp = requests.get(api_url, timeout=10)

            if resp.status_code == 200:
                bot_info = resp.json()
                if bot_info.get('ok'):
                    result = bot_info.get('result', {})
                    print(f"     ✅ Bot Token 有效")
                    print(f"        Bot 名称: @{result.get('username', 'N/A')}")
                else:
                    print(f"     ❌ Bot Token 无效")
            else:
                print(f"     ❌ API 错误: {resp.status_code}")

            # v14.0: Dual-channel check
            print()
            print("  📋 v14.0 双频道 Telegram 检查:")

            notif_token = os.getenv('TELEGRAM_NOTIFICATION_BOT_TOKEN')
            notif_chat_id = os.getenv('TELEGRAM_NOTIFICATION_CHAT_ID')

            if notif_token and notif_chat_id:
                print(f"     ✅ 通知频道配置:")
                print(f"        Notification Bot Token: {mask_sensitive(notif_token)}")
                print(f"        Notification Chat ID: {notif_chat_id}")

                # Test notification bot API connectivity
                notif_api_url = f"https://api.telegram.org/bot{notif_token}/getMe"
                try:
                    notif_resp = requests.get(notif_api_url, timeout=10)
                    if notif_resp.status_code == 200:
                        notif_info = notif_resp.json()
                        if notif_info.get('ok'):
                            notif_result = notif_info.get('result', {})
                            print(f"     ✅ 通知频道 Bot 有效: @{notif_result.get('username', 'N/A')}")
                        else:
                            print(f"     ❌ 通知频道 Bot Token 无效")
                    else:
                        print(f"     ❌ 通知频道 API 错误: {notif_resp.status_code}")
                except Exception as e_notif:
                    print(f"     ⚠️ 通知频道连接失败: {e_notif}")

                # Verify broadcast parameter
                if hasattr(TelegramBot, 'send_message_sync'):
                    import inspect
                    sig = inspect.signature(TelegramBot.send_message_sync)
                    if 'broadcast' in sig.parameters:
                        print(f"     ✅ send_message_sync(broadcast=...) 路由参数存在")
                    else:
                        print(f"     ❌ send_message_sync 缺少 broadcast 参数")
                        self.ctx.add_warning("v14.0: broadcast 参数缺失")
            else:
                missing = []
                if not notif_token:
                    missing.append("TELEGRAM_NOTIFICATION_BOT_TOKEN")
                if not notif_chat_id:
                    missing.append("TELEGRAM_NOTIFICATION_CHAT_ID")
                print(f"     ⚠️ 通知频道未配置 ({', '.join(missing)})")
                print(f"        → 所有消息将发送到私聊 (单频道模式)")

            print()
            print("  ✅ Telegram 验证完成")
            return True

        except Exception as e:
            self.ctx.add_warning(f"Telegram 验证失败: {e}")
            return True  # Non-critical

    def should_skip(self) -> bool:
        return self.ctx.summary_mode


class ErrorRecoveryChecker(DiagnosticStep):
    """
    v6.2: Verify error recovery mechanisms with actual function calls.

    Tests fallback logic for various failure scenarios:
    [1] _create_fallback_signal returns correct structure
    [2] API retry/JSON extraction methods exist with correct signatures
    [3] SL/TP validation failure → Level 2 fallback chain
    [4] Data client graceful degradation
    """

    name = "v6.2 错误恢复机制验证 (实际调用)"

    def run(self) -> bool:
        print("-" * 70)

        all_ok = True

        # [1] Test _create_fallback_signal actually returns correct structure
        print("  [1] MultiAgentAnalyzer fallback (实际调用):")
        try:
            from agents.multi_agent_analyzer import MultiAgentAnalyzer
            if hasattr(MultiAgentAnalyzer, '_create_fallback_signal'):
                # Call it with a mock instance (staticmethod or classmethod check)
                import inspect
                sig = inspect.signature(MultiAgentAnalyzer._create_fallback_signal)
                params = list(sig.parameters.keys())

                # Create minimal instance for testing
                ma = MultiAgentAnalyzer()
                fallback = ma._create_fallback_signal({"price": 68000.0})

                # Validate structure
                required_keys = ['signal', 'confidence']
                has_signal = fallback.get('signal') in ('HOLD', 'hold')
                has_confidence = fallback.get('confidence') in ('LOW', 'low')
                has_keys = all(k in fallback for k in required_keys)

                if has_signal and has_confidence and has_keys:
                    print(f"     ✅ _create_fallback_signal: signal={fallback.get('signal')}, "
                          f"confidence={fallback.get('confidence')}")
                else:
                    print(f"     ❌ _create_fallback_signal 结构异常: {fallback}")
                    self.ctx.add_error(f"fallback signal 结构异常: {fallback}")
                    all_ok = False
            else:
                print("     ❌ _create_fallback_signal 方法不存在")
                self.ctx.add_error("_create_fallback_signal 方法不存在")
                all_ok = False
        except Exception as e:
            print(f"     ⚠️ fallback 测试异常: {e}")
            self.ctx.add_warning(f"fallback 测试异常: {e}")

        # [2] API retry methods exist with correct structure
        print()
        print("  [2] API 重试机制 (方法签名验证):")
        try:
            from agents.multi_agent_analyzer import MultiAgentAnalyzer
            import inspect
            retry_methods = {
                '_call_api_with_retry': 'API 调用重试',
                '_extract_json_with_retry': 'JSON 解析重试',
            }
            for method_name, desc in retry_methods.items():
                if hasattr(MultiAgentAnalyzer, method_name):
                    sig = inspect.signature(getattr(MultiAgentAnalyzer, method_name))
                    print(f"     ✅ {method_name}: {desc} (params: {list(sig.parameters.keys())[:4]})")
                else:
                    print(f"     ❌ {method_name}: 缺失")
                    all_ok = False
        except Exception as e:
            print(f"     ⚠️ 重试机制检查异常: {e}")

        # [3] SL/TP validation failure chain (actual call)
        print()
        print("  [3] SL/TP 验证失败恢复链 (实际调用):")
        try:
            from strategy.trading_logic import validate_multiagent_sltp
            price = self.ctx.current_price or 68000.0

            # Level 1 rejection → should return False
            is_valid, _, _, reason = validate_multiagent_sltp(
                side='BUY', multi_sl=price * 0.999, multi_tp=price * 1.001,
                entry_price=price,
            )
            if not is_valid:
                print(f"     ✅ Level 1 正确拒绝噪音 SL: {reason[:60]}")
            else:
                print(f"     ❌ Level 1 未拒绝噪音 SL!")
                all_ok = False

            # None SL/TP → should return False
            is_valid2, _, _, reason2 = validate_multiagent_sltp(
                side='BUY', multi_sl=None, multi_tp=None,
                entry_price=price,
            )
            if not is_valid2:
                print(f"     ✅ Level 1 正确拒绝 None SL/TP: {reason2[:60]}")
            else:
                print(f"     ❌ Level 1 未拒绝 None SL/TP!")
                all_ok = False
        except Exception as e:
            print(f"     ⚠️ SL/TP 恢复链测试异常: {e}")

        # [4] Data client graceful degradation (verify methods exist)
        print()
        print("  [4] 数据客户端降级机制:")
        clients_to_check = [
            ('utils.coinalyze_client', 'CoinalyzeClient', 'Coinalyze OI/FR'),
            ('utils.binance_orderbook_client', 'BinanceOrderBookClient', 'Binance 订单簿'),
            ('utils.binance_derivatives_client', 'BinanceDerivativesClient', 'Binance Top Traders'),
        ]
        for module_name, class_name, desc in clients_to_check:
            try:
                module = __import__(module_name, fromlist=[class_name])
                cls = getattr(module, class_name, None)
                if cls:
                    print(f"     ✅ {desc} ({class_name}): 可导入")
                else:
                    print(f"     ⚠️ {desc}: {class_name} 不存在于 {module_name}")
            except ImportError as e:
                print(f"     ⚠️ {desc}: 导入失败 ({e}) — 降级为中性默认值")

        print()
        if all_ok:
            print("  ✅ 错误恢复机制验证完成 (所有调用通过)")
        else:
            print("  ⚠️ 错误恢复机制部分失败 — 详见上方")

        return all_ok

    def should_skip(self) -> bool:
        return self.ctx.summary_mode
