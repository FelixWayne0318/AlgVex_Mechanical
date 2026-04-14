"""
Base module for the diagnostics system.

Contains core classes, utilities, and shared functionality used
across all diagnostic steps.
"""

import io
import os
import sys
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# =============================================================================
# Virtual Environment Helper
# =============================================================================

def ensure_venv() -> bool:
    """
    Ensure running in virtual environment, auto-switch if not.

    Returns:
        True if already in venv, False if switched
    """
    project_dir = Path(__file__).parent.parent.parent.absolute()
    venv_python = project_dir / "venv" / "bin" / "python"

    in_venv = (
        hasattr(sys, 'real_prefix') or
        (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
    )

    if not in_venv and venv_python.exists():
        print(f"\033[93m[!]\033[0m Detected non-venv environment, auto-switching...")
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)

    return in_venv


# =============================================================================
# Output Utilities
# =============================================================================

class TeeOutput:
    """Simultaneously output to terminal and buffer for export."""

    def __init__(self, stream, buffer: io.StringIO):
        self.stream = stream
        self.buffer = buffer

    def write(self, data: str) -> None:
        self.stream.write(data)
        self.buffer.write(data)

    def flush(self) -> None:
        self.stream.flush()


def print_wrapped(text: str, indent: str = "    ", width: int = 80) -> None:
    """Print auto-wrapped text with indentation."""
    for i in range(0, len(text), width):
        print(f"{indent}{text[i:i+width]}")


def print_section(title: str, char: str = "-", width: int = 70) -> None:
    """Print a section header."""
    print(char * width)
    print(f"  {title}")
    print(char * width)


def print_box(title: str, width: int = 70) -> None:
    """Print a box header for data sections."""
    border = "━" * (width - 2)
    print(f"  ┏{border}┓")
    # Center the title
    padding = (width - 4 - len(title)) // 2
    print(f"  ┃{' ' * padding}{title}{' ' * (width - 4 - padding - len(title))}┃")
    print(f"  ┗{border}┛")


# =============================================================================
# Data Type Helpers
# =============================================================================

def safe_float(value: Any) -> Optional[float]:
    """
    Safely convert value to float, handling strings and None.

    AI may return strings or numbers, this handles both.
    """
    if value is None:
        return None
    try:
        if isinstance(value, str):
            # Remove currency symbols and commas
            value = value.replace('$', '').replace(',', '').strip()
        return float(value)
    except (ValueError, TypeError):
        return None


def mask_sensitive(value: str, visible_chars: int = 4) -> str:
    """
    Mask sensitive string (API keys, tokens) for safe display.

    Args:
        value: The sensitive string
        visible_chars: Number of characters to show at start

    Returns:
        Masked string like "sk-a****" or "******* (len=32)"
    """
    if not value:
        return "(not set)"
    if len(value) <= visible_chars * 2:
        return "*" * len(value)
    return f"{value[:visible_chars]}{'*' * 8} (len={len(value)})"


# =============================================================================
# Mock Objects for Testing
# =============================================================================

class MockBar:
    """
    Mock bar object for indicator updates.

    Mimics the structure of NautilusTrader Bar objects.
    Used for feeding historical data to indicators.
    """

    def __init__(self, o: float, h: float, l: float, c: float, v: float, ts: int):
        # OHLC invariant: low <= open,close <= high
        if l > h:
            raise ValueError(f"MockBar OHLC invalid: low({l}) > high({h})")
        if o < l or o > h:
            raise ValueError(f"MockBar OHLC invalid: open({o}) outside [{l}, {h}]")
        if c < l or c > h:
            raise ValueError(f"MockBar OHLC invalid: close({c}) outside [{l}, {h}]")
        self.open = Decimal(str(o))
        self.high = Decimal(str(h))
        self.low = Decimal(str(l))
        self.close = Decimal(str(c))
        self.volume = Decimal(str(v))
        self.ts_init = int(ts)

    def __repr__(self) -> str:
        return f"MockBar(o={self.open}, h={self.high}, l={self.low}, c={self.close})"


# =============================================================================
# Binance API Helpers
# =============================================================================

def fetch_binance_klines(
    symbol: str,
    interval: str,
    limit: int,
    timeout: int = 15
) -> List[List]:
    """
    Fetch klines from Binance Futures API.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        interval: K-line interval (e.g., "30m", "4h", "1d")
        limit: Number of klines to fetch
        timeout: Request timeout in seconds

    Returns:
        List of kline data or empty list on failure
    """
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        response = requests.get(url, params=params, timeout=timeout)
        if response.status_code == 200:
            klines = response.json()
            # v6.5: Binance klines API always returns the current (incomplete)
            # candle as the last element.  Strip it so all callers receive only
            # completed bars — prevents volume artifacts (e.g. 0.03x ratio).
            if isinstance(klines, list) and len(klines) > 1:
                klines = klines[:-1]
            return klines
        return []
    except (requests.RequestException, ValueError):
        return []


def create_bar_from_kline(kline: List, bar_type: str = "") -> MockBar:
    """
    Create a MockBar from Binance kline data.

    Args:
        kline: Binance kline array [timestamp, open, high, low, close, volume, ...]
        bar_type: Bar type string (for logging only)

    Returns:
        MockBar object
    """
    return MockBar(
        float(kline[1]),  # open
        float(kline[2]),  # high
        float(kline[3]),  # low
        float(kline[4]),  # close
        float(kline[5]),  # volume
        int(kline[0])     # timestamp
    )


def parse_bar_interval(bar_type_str: str) -> str:
    """
    Parse NautilusTrader bar type string to Binance interval.

    Args:
        bar_type_str: Like "BTCUSDT-PERP.BINANCE-30-MINUTE-LAST-EXTERNAL"

    Returns:
        Binance interval like "30m", "4h", "1d"
    """
    # Check from longest to shortest to avoid substring matches
    # e.g., "15-MINUTE" should not match "5-MINUTE"
    if "30-MINUTE" in bar_type_str:
        return "30m"
    elif "15-MINUTE" in bar_type_str:
        return "15m"
    elif "5-MINUTE" in bar_type_str:
        return "5m"
    elif "1-MINUTE" in bar_type_str:
        return "1m"
    elif "4-HOUR" in bar_type_str:
        return "4h"
    elif "1-HOUR" in bar_type_str:
        return "1h"
    elif "1-DAY" in bar_type_str:
        return "1d"
    else:
        return "30m"  # Default (v18.2 execution layer)


@contextmanager
def step_timer(label: str, timings: dict):
    """Context manager to time a step and store the result."""
    start = time.monotonic()
    yield
    elapsed = time.monotonic() - start
    timings[label] = elapsed
    print(f"  [{elapsed:.2f}s] {label}")


def extract_symbol(instrument_id: str) -> str:
    """
    Extract trading symbol from instrument ID.

    Args:
        instrument_id: Like "BTCUSDT-PERP.BINANCE"

    Returns:
        Symbol like "BTCUSDT"
    """
    return instrument_id.split('-')[0]


# =============================================================================
# Diagnostic Context (Shared State)
# =============================================================================

@dataclass
class DiagnosticContext:
    """
    Shared context for all diagnostic steps.

    Holds all data collected during the diagnostic process,
    eliminating the need for global variables.
    """

    # Configuration
    env: str = "production"
    summary_mode: bool = False
    export_mode: bool = False
    push_to_github: bool = False
    push_branch: str = "main"  # Default push target (server should push to main)
    send_telegram: bool = False  # v25.0: Send summary to Telegram private chat after export

    # Project paths
    project_root: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent)

    # Strategy configuration (loaded from main_live.py)
    strategy_config: Any = None
    base_config: Dict = field(default_factory=dict)

    # Thresholds (loaded from config, not hardcoded)
    bb_overbought_threshold: float = 80.0
    bb_oversold_threshold: float = 20.0
    ls_ratio_extreme_bullish: float = 1.5
    ls_ratio_bullish: float = 1.2
    ls_ratio_extreme_bearish: float = 0.67
    ls_ratio_bearish: float = 0.83

    # Market data
    symbol: str = "BTCUSDT"
    interval: str = "30m"  # Indicator warmup bars (v18.2: 30M subscription)
    klines_raw: List = field(default_factory=list)
    current_price: float = 0.0
    snapshot_timestamp: str = ""

    # Indicator data
    indicator_manager: Any = None
    technical_data: Dict = field(default_factory=dict)

    # Position data
    current_position: Optional[Dict] = None
    account_balance: Dict = field(default_factory=dict)
    account_context: Dict = field(default_factory=dict)  # v4.7: Portfolio risk fields
    binance_leverage: int = 10  # v4.8: Real leverage from Binance API

    # Sentiment data
    sentiment_data: Dict = field(default_factory=dict)

    # Price data
    price_data: Dict = field(default_factory=dict)

    # MTF data
    order_flow_report: Optional[Dict] = None
    order_flow_report_4h: Optional[Dict] = None  # v18 Item 16: 4H CVD order flow
    derivatives_report: Optional[Dict] = None
    orderbook_report: Optional[Dict] = None  # v3.7: Order book depth data
    binance_derivatives_data: Optional[Dict] = None  # v3.21: Binance Top Traders, Taker Ratio
    fear_greed_data: Optional[Dict] = None           # v44.0: Fear & Greed Index
    _hmm_regime: Optional[Dict] = None               # v2.0: HMM regime detection result
    _kelly_info: Optional[Dict] = None               # v2.0: Kelly sizing info
    binance_funding_rate: Optional[Dict] = None  # v4.8: Binance 8h funding rate (主要数据源)
    sr_zones_data: Optional[Dict] = None  # v2.6.0: S/R Zone Calculator data
    sr_bars_data: Optional[List] = None  # v4.0: 200 bars for S/R Swing Detection
    atr_value: float = 0.0               # v4.0 (E1): ATR from S/R bars (0.0 matches live _cached_atr_value default)
    bars_data_4h: Optional[List] = None  # v4.0: 4H bars for S/R pivot + volume profile
    bars_data_1d: Optional[List] = None  # v4.0: 1D bars for S/R swing detection
    daily_bar: Optional[Dict] = None     # v4.0: Last daily bar for pivot calculation
    weekly_bar: Optional[Dict] = None    # v4.0: Aggregated weekly bar for pivot

    # AI decision data
    multi_agent: Any = None
    signal_data: Dict = field(default_factory=dict)
    final_signal: str = "HOLD"
    ai_call_trace: List = field(default_factory=list)  # Full AI I/O trace for log export

    # Timing data (v3.0.0 diagnostic)
    step_timings: Dict = field(default_factory=dict)

    # v4.12: Code integrity & math verification results
    code_integrity_results: List = field(default_factory=list)
    math_verification_results: List = field(default_factory=list)
    step_results: List = field(default_factory=list)  # (id, pass, desc) tuples

    # Output buffer for export
    output_buffer: io.StringIO = field(default_factory=io.StringIO)
    original_stdout: Any = None

    # Step tracking
    current_step: int = 0
    total_steps: int = 34  # 28 data steps + 3 order flow + code_integrity + math_verify + json_output
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def base_currency(self) -> str:
        """Extract base currency from symbol (e.g., BTCUSDT → BTC, ETHUSDT → ETH)."""
        if 'USDT' in self.symbol:
            return self.symbol.replace('USDT', '')
        return self.symbol.split('-')[0] if '-' in self.symbol else 'BTC'

    def add_error(self, message: str) -> None:
        """Add an error message."""
        self.errors.append(message)
        print(f"  ❌ {message}")

    def add_warning(self, message: str) -> None:
        """Add a warning message."""
        self.warnings.append(message)
        print(f"  ⚠️ {message}")

    def print_step(self, title: str) -> None:
        """Print step header with progress."""
        self.current_step += 1
        print(f"[{self.current_step}/{self.total_steps}] {title}")

    def load_thresholds_from_config(self) -> None:
        """Load threshold values from base_config, with defaults."""
        indicators = self.base_config.get('indicators', {})

        self.bb_overbought_threshold = indicators.get('bb_overbought_threshold', 80.0)
        self.bb_oversold_threshold = indicators.get('bb_oversold_threshold', 20.0)
        self.ls_ratio_extreme_bullish = indicators.get('ls_ratio_extreme_bullish', 1.5)
        self.ls_ratio_bullish = indicators.get('ls_ratio_bullish', 1.2)
        self.ls_ratio_extreme_bearish = indicators.get('ls_ratio_extreme_bearish', 0.67)
        self.ls_ratio_bearish = indicators.get('ls_ratio_bearish', 0.83)


# =============================================================================
# Diagnostic Step Base Class
# =============================================================================

class DiagnosticStep(ABC):
    """
    Abstract base class for diagnostic steps.

    Each diagnostic step should inherit from this class and
    implement the run() method.
    """

    name: str = "Unnamed Step"
    description: str = ""

    def __init__(self, ctx: DiagnosticContext):
        self.ctx = ctx

    @abstractmethod
    def run(self) -> bool:
        """
        Execute the diagnostic step.

        Returns:
            True if step completed successfully, False otherwise
        """
        pass

    def should_skip(self) -> bool:
        """
        Check if this step should be skipped.

        Override in subclasses for conditional execution.
        """
        return False

    def print_header(self) -> None:
        """Print step header."""
        self.ctx.print_step(self.name)
        if self.description:
            print(f"  {self.description}")


# =============================================================================
# Diagnostic Runner
# =============================================================================

class DiagnosticRunner:
    """
    Main runner for the diagnostic system.

    Orchestrates all diagnostic steps in the correct order.
    """

    def __init__(
        self,
        env: str = "production",
        summary_mode: bool = False,
        export_mode: bool = False,
        push_to_github: bool = False,
        push_branch: str = "main",
        send_telegram: bool = False,
    ):
        self.ctx = DiagnosticContext(
            env=env,
            summary_mode=summary_mode,
            export_mode=export_mode,
            push_to_github=push_to_github,
            push_branch=push_branch,
            send_telegram=send_telegram,
        )
        self.steps: List[DiagnosticStep] = []

        # v2.4.8: Load dotenv early to ensure environment variables are available
        # for all diagnostic steps (including APIHealthCheck which runs first)
        self._load_environment()

    def _load_environment(self) -> None:
        """Load environment variables from .env files early."""
        try:
            from dotenv import load_dotenv
            env_permanent = Path.home() / ".env.algvex"
            env_local = self.ctx.project_root / ".env"

            if env_permanent.exists():
                load_dotenv(env_permanent)
            elif env_local.exists():
                load_dotenv(env_local)
            else:
                load_dotenv()
        except ImportError:
            print("  ℹ️ python-dotenv not installed, skipping .env loading")

    def add_step(self, step_class: type) -> None:
        """Add a diagnostic step."""
        self.steps.append(step_class(self.ctx))

    def setup_output_capture(self) -> None:
        """Setup output capture for export mode."""
        if self.ctx.export_mode:
            self.ctx.original_stdout = sys.stdout
            sys.stdout = TeeOutput(sys.stdout, self.ctx.output_buffer)

    def restore_output(self) -> None:
        """Restore original stdout."""
        if self.ctx.export_mode and self.ctx.original_stdout:
            sys.stdout = self.ctx.original_stdout

    def run_all(self) -> bool:
        """
        Run all diagnostic steps.

        Returns:
            True if all steps passed, False if any failed
        """
        try:
            self.setup_output_capture()

            print("=" * 70)
            print("  实盘信号诊断工具 (100% Live-Consistent)")
            print("  基于 TradingAgents 架构 + R/R 硬性门槛")
            print("=" * 70)
            print()

            success = True
            for step in self.steps:
                if step.should_skip():
                    print(f"  ⏭️ Skipped: {step.name}")
                    continue

                try:
                    step.print_header()
                    if not step.run():
                        success = False
                        self.ctx.add_error(f"Step failed: {step.name}")
                    print()
                except KeyboardInterrupt:
                    print("\n  用户中断")
                    raise
                except Exception as e:
                    success = False
                    self.ctx.add_error(f"Step {step.name} raised exception: {e}")
                    import traceback
                    traceback.print_exc()

            self._print_final_summary()
            return success

        finally:
            self.restore_output()

    def _print_final_summary(self) -> None:
        """Print final diagnostic summary."""
        print("=" * 70)
        print("  诊断完成")
        print("=" * 70)

        if self.ctx.errors:
            print(f"\n  ❌ 错误数: {len(self.ctx.errors)}")
            for error in self.ctx.errors[:5]:
                print(f"     • {error}")

        if self.ctx.warnings:
            print(f"\n  ⚠️ 警告数: {len(self.ctx.warnings)}")
            for warning in self.ctx.warnings[:5]:
                print(f"     • {warning}")

        if not self.ctx.errors and not self.ctx.warnings:
            print("\n  ✅ 所有检查通过")

    def export_results(self) -> Optional[Path]:
        """
        Export diagnostic results to file + AI call trace to separate log.

        Returns:
            Path to exported file, or None if not in export mode
        """
        if not self.ctx.export_mode:
            return None

        self.restore_output()

        logs_dir = self.ctx.project_root / "logs"
        logs_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 1. Main diagnosis report
        filename = f"diagnosis_{timestamp}.txt"
        filepath = logs_dir / filename

        output_content = self.ctx.output_buffer.getvalue()
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(output_content)
        except OSError as e:
            print(f"  ❌ Failed to write diagnosis report: {e}")
            return None

        # 2. AI call trace log (full input/output for every API call)
        ai_log_filename = f"ai_calls_{timestamp}.txt"
        ai_log_filepath = logs_dir / ai_log_filename
        self._export_ai_call_trace(ai_log_filepath)

        print()
        print("=" * 70)
        print("  📤 诊断结果导出")
        print("=" * 70)
        print(f"  ✅ 诊断报告: {filepath}")
        print(f"     ({len(output_content):,} 字符)")
        print(f"  ✅ AI 调用日志: {ai_log_filepath}")
        if ai_log_filepath.exists():
            print(f"     ({ai_log_filepath.stat().st_size:,} 字节, 完整 AI 输入/输出)")

        if self.ctx.push_to_github:
            self._push_to_github_multi([filepath, ai_log_filepath])

        if self.ctx.send_telegram:
            self._send_telegram_summary(filepath, ai_log_filepath)

        return filepath

    def _export_ai_call_trace(self, filepath: Path) -> None:
        """Export full AI call trace with complete input/output to a separate log file."""
        trace = self.ctx.ai_call_trace
        try:
            if not trace:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write("No AI calls recorded in this diagnostic session.\n")
                return
        except OSError as e:
            print(f"  ⚠️ Failed to write AI call trace: {e}")
            return

        try:
            self._write_ai_call_trace(filepath, trace)
        except OSError as e:
            print(f"  ⚠️ Failed to write AI call trace: {e}")

    def _write_ai_call_trace(self, filepath: Path, trace: list) -> None:
        """Write the AI call trace content to file."""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(f"  AI API Call Trace — {len(trace)} Sequential Calls\n")
            f.write(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")

            # Check if any call has cache data
            has_cache = any(
                call.get('tokens', {}).get('cache_hit') is not None
                for call in trace
            )

            # Summary table
            if has_cache:
                f.write("┌─────┬──────────────────┬────────┬────────────┬──────────┬──────────┬────────────┬────────────┐\n")
                f.write("│  #  │ Agent            │  Time  │  Tokens    │  Prompt  │  Reply   │ Cache Hit  │ Cache Miss │\n")
                f.write("├─────┼──────────────────┼────────┼────────────┼──────────┼──────────┼────────────┼────────────┤\n")
            else:
                f.write("┌─────┬──────────────────┬────────┬────────────┬──────────┬──────────┐\n")
                f.write("│  #  │ Agent            │  Time  │  Tokens    │  Prompt  │  Reply   │\n")
                f.write("├─────┼──────────────────┼────────┼────────────┼──────────┼──────────┤\n")
            total_time = 0
            total_tokens = 0
            total_cache_hit = 0
            total_cache_miss = 0
            for i, call in enumerate(trace, 1):
                label = call.get('label', f'call_{i}')
                elapsed = call.get('elapsed_sec', 0)
                tokens = call.get('tokens', {})
                prompt_tk = tokens.get('prompt', 0)
                completion_tk = tokens.get('completion', 0)
                total_tk = tokens.get('total', 0)
                total_time += elapsed
                total_tokens += total_tk
                if has_cache:
                    ch = tokens.get('cache_hit', 0) or 0
                    cm = tokens.get('cache_miss', 0) or 0
                    total_cache_hit += ch
                    total_cache_miss += cm
                    f.write(f"│ {i:<3} │ {label:<16} │ {elapsed:>5.1f}s │ {total_tk:>10,} │ {prompt_tk:>8,} │ {completion_tk:>8,} │ {ch:>10,} │ {cm:>10,} │\n")
                else:
                    f.write(f"│ {i:<3} │ {label:<16} │ {elapsed:>5.1f}s │ {total_tk:>10,} │ {prompt_tk:>8,} │ {completion_tk:>8,} │\n")
            if has_cache:
                f.write("├─────┼──────────────────┼────────┼────────────┼──────────┼──────────┼────────────┼────────────┤\n")
                f.write(f"│     │ TOTAL            │ {total_time:>5.1f}s │ {total_tokens:>10,} │          │          │ {total_cache_hit:>10,} │ {total_cache_miss:>10,} │\n")
                f.write("└─────┴──────────────────┴────────┴────────────┴──────────┴──────────┴────────────┴────────────┘\n")
            else:
                f.write("├─────┼──────────────────┼────────┼────────────┼──────────┼──────────┤\n")
                f.write(f"│     │ TOTAL            │ {total_time:>5.1f}s │ {total_tokens:>10,} │          │          │\n")
                f.write("└─────┴──────────────────┴────────┴────────────┴──────────┴──────────┘\n")
            f.write("\n")

            # Cache summary (if cache data available)
            if has_cache and total_cache_hit > 0:
                total_prompt = total_cache_hit + total_cache_miss
                hit_pct = (total_cache_hit / total_prompt * 100) if total_prompt > 0 else 0
                # DeepSeek pricing: cache hit $0.028/M, cache miss $0.28/M
                cost_without_cache = total_prompt * 0.28 / 1_000_000
                cost_with_cache = (total_cache_hit * 0.028 + total_cache_miss * 0.28) / 1_000_000
                savings_pct = ((cost_without_cache - cost_with_cache) / cost_without_cache * 100) if cost_without_cache > 0 else 0
                f.write(f"  DeepSeek Prefix Cache Summary:\n")
                f.write(f"    Cache Hit:  {total_cache_hit:>8,} tokens ({hit_pct:.1f}%)\n")
                f.write(f"    Cache Miss: {total_cache_miss:>8,} tokens\n")
                f.write(f"    Cost Savings: {savings_pct:.1f}% (hit=$0.028/M vs miss=$0.28/M)\n")
                f.write("\n")

            # Detailed call logs
            for i, call in enumerate(trace, 1):
                label = call.get('label', f'call_{i}')
                elapsed = call.get('elapsed_sec', 0)
                tokens = call.get('tokens', {})
                temp = call.get('temperature', 0)
                messages = call.get('messages', [])
                response = call.get('response', '')

                f.write("\n" + "=" * 80 + "\n")
                f.write(f"  CALL {i}/{len(trace)}: {label}\n")
                cache_str = ""
                ch = tokens.get('cache_hit')
                cm = tokens.get('cache_miss')
                if ch is not None:
                    cache_str = f"  |  Cache: {ch:,} hit / {cm or 0:,} miss"
                f.write(f"  Temperature: {temp}  |  Time: {elapsed:.1f}s  |  Tokens: {tokens.get('total', 0):,}{cache_str}\n")
                # v27.0: Version metadata per call for traceability
                sv = call.get('schema_version', '')
                fv = call.get('feature_version', '')
                ph = call.get('prompt_hash', '')
                jm = call.get('json_mode', False)
                if sv or fv or ph:
                    f.write(f"  Schema: {sv}  |  Features: {fv}  |  prompt_hash: {ph}  |  json_mode: {jm}\n")
                f.write("=" * 80 + "\n")

                for msg in messages:
                    role = msg.get('role', 'unknown').upper()
                    content = msg.get('content', '')
                    f.write(f"\n{'─'*40} [{role} PROMPT] {'─'*40}\n\n")
                    f.write(content)
                    f.write(f"\n\n[{role} PROMPT length: {len(content):,} chars]\n")

                f.write(f"\n{'─'*40} [AI RESPONSE (raw)] {'─'*40}\n\n")
                f.write(response)
                f.write(f"\n\n[AI RESPONSE length: {len(response):,} chars]\n")

                # v27.0: Show validated output (after schema filtering) when available
                validated = call.get('validated_output')
                if validated is not None:
                    import json as _json
                    validated_str = _json.dumps(validated, indent=2, default=str)
                    f.write(f"\n{'─'*40} [VALIDATED OUTPUT] {'─'*40}\n\n")
                    f.write(validated_str)
                    f.write(f"\n\n[VALIDATED OUTPUT length: {len(validated_str):,} chars]\n")

            f.write("\n" + "=" * 80 + "\n")
            f.write("  END OF AI CALL TRACE\n")
            f.write("=" * 80 + "\n")

    def _push_to_github_multi(self, filepaths: list) -> None:
        """Push multiple export files to GitHub in a single commit.

        Default: push to current branch (HEAD).
        --push-branch overrides target branch.
        """
        import subprocess

        filenames = [fp.name for fp in filepaths if fp.exists()]
        commit_msg = f"chore: Add diagnosis report + AI call trace ({', '.join(filenames)})"

        try:
            os.chdir(self.ctx.project_root)

            current_branch = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True, text=True, check=True
            ).stdout.strip()

            # Use --push-branch if explicitly set, otherwise current branch
            branch = self.ctx.push_branch if self.ctx.push_branch != "main" else current_branch

            if current_branch != branch:
                print(f"  ℹ️  当前 {current_branch}，切换到 {branch}...")
                subprocess.run(['git', 'checkout', branch], check=True, capture_output=True)

            for fp in filepaths:
                if fp.exists():
                    subprocess.run(['git', 'add', '-f', str(fp)], check=True, capture_output=True)

            subprocess.run(['git', 'commit', '-m', commit_msg], check=True, capture_output=True)

            push_result = subprocess.run(
                ['git', 'push', '-u', 'origin', branch],
                capture_output=True, text=True
            )
            if push_result.returncode != 0:
                stderr = push_result.stderr.strip()
                print(f"  ⚠️ Git push 失败 (exit {push_result.returncode}):")
                print(f"     {stderr}")
                paths_str = ' '.join(str(fp) for fp in filepaths)
                print(f"     已提交到本地，请手动: git push origin {branch}")
                return

            print(f"  ✅ 已推送到 GitHub (分支: {branch})")
            for fn in filenames:
                print(f"  📎 logs/{fn}")

            # Switch back if we changed branches
            if current_branch != branch:
                subprocess.run(['git', 'checkout', current_branch], check=True, capture_output=True)
                print(f"  ℹ️  已切回 {current_branch}")

        except subprocess.CalledProcessError as e:
            raw_stderr = getattr(e, 'stderr', None) or b""
            stderr = raw_stderr.decode() if isinstance(raw_stderr, bytes) else str(raw_stderr)
            print(f"  ⚠️ Git 操作失败: {e}")
            if stderr:
                print(f"     详情: {stderr.strip()}")
            paths_str = ' '.join(str(fp) for fp in filepaths)
            print(f"     请手动提交: git add -f {paths_str} && git commit && git push")

    def _send_telegram_summary(self, report_path: Path, ai_log_path: Path) -> None:
        """Send diagnostic summary + full report files to Telegram private chat.

        Sends:
        1. Text summary (signal, checks, cache, branch)
        2. Diagnosis report as document (sendDocument)
        3. AI call trace as document (sendDocument)

        Uses lightweight requests.post directly (no TelegramBot queue/db dependency).
        Designed for nohup usage: diagnose completes → sends result → user sees on phone.
        """
        import subprocess

        token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')

        if not token or not chat_id:
            print("  ⚠️ Telegram 环境变量未设置 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
            return

        # Detect current git branch
        branch = "unknown"
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True, text=True, check=True,
                cwd=str(self.ctx.project_root),
            )
            branch = result.stdout.strip()
        except Exception:
            pass

        # Build summary message from context
        ctx = self.ctx
        sd = ctx.signal_data or {}

        # Aggregate all check results: code integrity + math verification + step_results
        ci_results = getattr(ctx, 'code_integrity_results', [])
        mv_results = getattr(ctx, 'math_verification_results', [])
        ci_passed = sum(1 for r in ci_results if r.get('pass'))
        ci_failed = len(ci_results) - ci_passed
        mv_passed = sum(1 for r in mv_results if r.get('pass'))
        mv_failed = len(mv_results) - mv_passed
        sr_passed = sum(1 for _, ok, _ in ctx.step_results if ok)
        sr_failed = len(ctx.step_results) - sr_passed

        total = len(ci_results) + len(mv_results) + len(ctx.step_results)
        passed = ci_passed + mv_passed + sr_passed
        failed = ci_failed + mv_failed + sr_failed
        errors_count = len(ctx.errors)
        warnings_count = len(ctx.warnings)

        signal = sd.get('signal', ctx.final_signal)
        confidence = sd.get('confidence', 'N/A')
        risk_level = sd.get('risk_level', '')
        risk_appetite = sd.get('risk_appetite', '')
        quality_score = sd.get('_quality_score', '')

        # AI call stats
        trace = ctx.ai_call_trace
        cache_hit = sum(c.get('tokens', {}).get('cache_hit', 0) or 0 for c in trace)
        cache_miss = sum(c.get('tokens', {}).get('cache_miss', 0) or 0 for c in trace)
        total_tokens = sum(c.get('tokens', {}).get('total', 0) for c in trace)
        total_time = sum(c.get('elapsed_sec', 0) for c in trace)
        ai_calls = len(trace)

        # Status header (Chinese-English mixed output per CLAUDE.md)
        status_emoji = "✅" if failed == 0 and errors_count == 0 else "❌"
        lines = [f"{status_emoji} *实时诊断 Realtime Diagnosis*"]

        # ── Signal block ──
        signal_cn = {"LONG": "开多", "SHORT": "开空", "HOLD": "观望", "CLOSE": "平仓", "REDUCE": "减仓"}.get(signal, signal)
        signal_emoji = {"LONG": "🟢", "SHORT": "🔴", "HOLD": "⏸", "CLOSE": "🔻", "REDUCE": "📉"}.get(signal, "❓")
        lines.append("")
        lines.append(f"{signal_emoji} *信号: {signal_cn}* ({signal}) | 信心: *{confidence}*")
        if ctx.current_price:
            lines.append(f"💰 BTC ${ctx.current_price:,.2f}")

        # Judge winning side + key reasons
        judge = sd.get('judge_decision', {})
        winning_side = judge.get('winning_side', '')
        if winning_side:
            side_cn = {"BULL": "看多", "BEAR": "看空", "TIE": "中立"}.get(winning_side, winning_side)
            side_emoji = {"BULL": "🐂", "BEAR": "🐻", "TIE": "⚖️"}.get(winning_side, "")
            lines.append(f"{side_emoji} Judge 裁决: {side_cn} ({winning_side})")
        key_reasons = judge.get('key_reasons', [])
        if key_reasons:
            for reason in key_reasons[:2]:
                # Truncate long reasons, escape Markdown special chars
                r = reason[:60].replace('*', '').replace('_', '').replace('`', '')
                lines.append(f"  - {r}")

        # SL/TP (only for actionable signals)
        if signal in ('LONG', 'SHORT'):
            sl = safe_float(sd.get('stop_loss'))
            tp = safe_float(sd.get('take_profit'))
            if sl and tp and ctx.current_price:
                rr = abs(tp - ctx.current_price) / abs(ctx.current_price - sl) if abs(ctx.current_price - sl) > 0 else 0
                lines.append(f"🛡 止损 SL ${sl:,.0f} | 止盈 TP ${tp:,.0f} (R/R {rr:.1f}:1)")
            elif sl:
                lines.append(f"🛡 止损 SL ${sl:,.0f}")

        # Entry Timing Agent
        _timing = sd.get('_timing_assessment', {})
        if _timing:
            if sd.get('_timing_rejected'):
                orig = sd.get('_timing_original_signal', '?')
                lines.append(f"⏱ 入场时机: REJECT ({orig} -> HOLD)")
            elif sd.get('_timing_confidence_adjusted'):
                lines.append(f"⏱ 入场时机: {sd['_timing_confidence_adjusted']}")
            else:
                verdict = _timing.get('verdict', _timing.get('overall_assessment', ''))
                if verdict:
                    lines.append(f"⏱ 入场时机: {verdict}")

        # Risk blocks / gates
        blocks = []
        if sd.get('_risk_blocked'):
            blocks.append(f"风控否决: {sd.get('_risk_block_reason', 'N/A')[:40]}")
        if sd.get('_fr_entry_blocked'):
            blocks.append("FR 入场阻止")
        if sd.get('_liq_buffer_blocked'):
            blocks.append("清算缓冲不足")
        if blocks:
            lines.append(f"🚫 {' | '.join(blocks)}")

        # ── AI Performance block ──
        lines.append("")
        quality_str = f" | 质量 {quality_score}/100" if quality_score != '' else ""
        _time_str = f"{total_time:.0f}s" if total_time > 0 else ""
        _token_str = f", {total_tokens:,} tokens" if total_tokens > 0 else ""
        _perf_str = f", {_time_str}{_token_str}" if _time_str or _token_str else ""
        lines.append(f"🤖 AI: {ai_calls} 次调用{_perf_str}{quality_str}")

        if cache_hit > 0:
            total_prompt = cache_hit + cache_miss
            hit_pct = cache_hit / total_prompt * 100 if total_prompt > 0 else 0
            lines.append(f"💾 缓存: {cache_hit:,} 命中 ({hit_pct:.0f}%) / {cache_miss:,} 未命中")

        # Risk context
        risk_parts = []
        if risk_level:
            risk_parts.append(f"风险: {risk_level}")
        if risk_appetite:
            risk_parts.append(f"风控偏好: {risk_appetite}")
        if risk_parts:
            lines.append(f"📊 {' | '.join(risk_parts)}")

        # ── v2.0 Components block ──
        lines.append("")
        # HMM Regime
        _regime = getattr(ctx, '_hmm_regime', None)
        if _regime:
            _regime_cn = {'TRENDING_UP': '上升趋势', 'TRENDING_DOWN': '下降趋势',
                          'RANGING': '震荡', 'HIGH_VOLATILITY': '高波动',
                          'STRONG_TREND': '强趋势', 'WEAK_TREND': '弱趋势'}.get(_regime.get('regime', ''), _regime.get('regime', ''))
            _src = _regime.get('source', 'unknown')
            lines.append(f"🔀 体制: {_regime_cn} ({_src})")

        # Instructor stats
        _agent_labels = {'Bull', 'Bear', 'Judge', 'Entry Timing', 'Risk'}
        instructor_ok = sum(1 for c in trace if c.get('method') == 'instructor')
        _agent_calls = sum(1 for c in trace if any(a in c.get('label', '') for a in _agent_labels))
        instructor_fb = _agent_calls - instructor_ok
        if _agent_calls > 0:
            lines.append(f"🔧 Instructor: {instructor_ok}/{_agent_calls} 成功" + (f", {instructor_fb} 回退" if instructor_fb > 0 else ""))

        # Kelly
        _kelly = getattr(ctx, '_kelly_info', None)
        if _kelly:
            lines.append(f"📐 Kelly: {_kelly.get('size_pct', 'N/A')}% ({_kelly.get('source', 'N/A')})")

        # Fear & Greed
        _fg = getattr(ctx, 'fear_greed_data', None)
        if _fg and isinstance(_fg, dict) and _fg.get('value') is not None:
            _extreme = " ⚠️" if _fg.get('is_extreme') else ""
            lines.append(f"😱 恐贪: {_fg.get('value')}/100 ({_fg.get('classification', 'N/A')}){_extreme}")

        # ── Check results block ──
        lines.append("")
        lines.append(f"✅ 检查: {passed}/{total} 通过")
        if failed > 0:
            lines.append(f"❌ 失败: {failed}")
        if errors_count > 0:
            lines.append(f"🔴 错误: {errors_count}")
            # Show first 2 errors (truncated)
            for err in ctx.errors[:2]:
                e = err[:50].replace('*', '').replace('_', '').replace('`', '')
                lines.append(f"  - {e}")
        if warnings_count > 0:
            lines.append(f"⚠️ 警告: {warnings_count}")
            # Show first 2 warnings (truncated)
            for w in ctx.warnings[:2]:
                w_text = w[:50].replace('*', '').replace('_', '').replace('`', '')
                lines.append(f"  - {w_text}")

        # ── Files block (repo-relative path for GitHub navigation) ──
        lines.append("")
        if report_path.exists():
            size_kb = report_path.stat().st_size / 1024
            try:
                rel_report = report_path.relative_to(self.ctx.project_root)
            except ValueError:
                rel_report = report_path.name
            lines.append(f"📎 诊断报告: `{rel_report}` ({size_kb:.0f} KB)")
        if ai_log_path.exists():
            size_kb = ai_log_path.stat().st_size / 1024
            try:
                rel_ai_log = ai_log_path.relative_to(self.ctx.project_root)
            except ValueError:
                rel_ai_log = ai_log_path.name
            lines.append(f"📎 AI 日志: `{rel_ai_log}` ({size_kb:.0f} KB)")

        # ── Footer ──
        lines.append("")
        lines.append(f"🔀 分支: `{branch}`")
        if ctx.push_to_github:
            lines.append(f"✅ 已推送到 GitHub")

        message = "\n".join(line for line in lines if line is not None)

        # Step 1: Send text summary
        self._telegram_send_text(token, chat_id, message)

        # Step 2: Send diagnosis report as document
        if report_path.exists():
            self._telegram_send_document(token, chat_id, report_path, f"📋 诊断报告 | {signal_cn} ({signal}) {confidence}")

        # Step 3: Send AI call trace as document
        if ai_log_path.exists():
            caption = f"🤖 AI 调用日志 ({ai_calls} 次, {total_time:.0f}s, {total_tokens:,} tokens)"
            self._telegram_send_document(token, chat_id, ai_log_path, caption)

    def _telegram_send_text(self, token: str, chat_id: str, message: str) -> None:
        """Send a text message to Telegram private chat."""
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'Markdown',
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            result = resp.json()
            if result.get('ok'):
                print("  ✅ 诊断摘要已发送到 Telegram 私聊")
            else:
                desc = result.get('description', 'unknown error')
                # Retry without Markdown on parse error
                if "can't parse" in desc.lower() or "parse entities" in desc.lower():
                    payload.pop('parse_mode', None)
                    resp = requests.post(url, json=payload, timeout=10)
                    if resp.json().get('ok'):
                        print("  ✅ 诊断摘要已发送到 Telegram 私聊 (plain text)")
                        return
                print(f"  ⚠️ Telegram 文本发送失败: {desc}")
        except Exception as e:
            print(f"  ⚠️ Telegram 文本发送异常: {e}")

    def _telegram_send_document(self, token: str, chat_id: str, filepath: Path, caption: str) -> None:
        """Send a file as document to Telegram private chat (sendDocument API)."""
        url = f"https://api.telegram.org/bot{token}/sendDocument"

        try:
            with open(filepath, 'rb') as f:
                resp = requests.post(
                    url,
                    data={'chat_id': chat_id, 'caption': caption},
                    files={'document': (filepath.name, f, 'text/plain')},
                    timeout=30,
                )
            result = resp.json()
            if result.get('ok'):
                size_kb = filepath.stat().st_size / 1024
                print(f"  ✅ {caption}: {filepath.name} ({size_kb:.0f} KB) 已发送")
            else:
                desc = result.get('description', 'unknown error')
                print(f"  ⚠️ {caption} 发送失败: {desc}")
        except Exception as e:
            print(f"  ⚠️ {caption} 发送异常: {e}")
