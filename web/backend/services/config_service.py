"""
Configuration Service - Integrated with AlgVex ConfigManager
Manages strategy configuration and system control
"""
import yaml
import subprocess
import re
import os
import sys
from pathlib import Path
from typing import Optional, Any, Dict, Tuple, List
from datetime import datetime

from core.config import settings

# Add AlgVex root to path for importing ConfigManager
ALGVEX_ROOT = settings.ALGVEX_PATH
if str(ALGVEX_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGVEX_ROOT))


class ConfigService:
    """Service for managing AlgVex configuration and system control"""

    def __init__(self):
        self.algvex_path = Path(settings.ALGVEX_PATH)
        self.service_name = settings.ALGVEX_SERVICE_NAME
        self.configs_path = self.algvex_path / "configs"
        self.base_config_path = self.configs_path / "base.yaml"

        # Validate service name to prevent command injection
        if not re.match(r'^[a-z0-9-]+$', self.service_name):
            raise ValueError(
                f"Invalid service name: {self.service_name}. "
                "Service name must contain only lowercase letters, numbers, and hyphens."
            )

    # =========================================================================
    # Configuration Reading
    # =========================================================================
    def read_base_config(self) -> Dict:
        """Read base.yaml configuration"""
        try:
            if self.base_config_path.exists():
                with open(self.base_config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Error reading base config: {e}")
        return {}

    def read_env_config(self, env: str = "production") -> Dict:
        """Read environment-specific config overlay"""
        env_path = self.configs_path / f"{env}.yaml"
        try:
            if env_path.exists():
                with open(env_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Error reading {env} config: {e}")
        return {}

    def get_merged_config(self, env: str = "production") -> Dict:
        """Get merged configuration (base + environment overlay)"""
        base = self.read_base_config()
        overlay = self.read_env_config(env)
        return self._deep_merge(base, overlay)

    def _deep_merge(self, base: Dict, overlay: Dict) -> Dict:
        """Deep merge two dictionaries"""
        result = base.copy()
        for key, value in overlay.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def read_strategy_config(self) -> Dict:
        """Read strategy configuration - alias for get_merged_config"""
        return self.get_merged_config()

    # =========================================================================
    # Configuration Writing
    # =========================================================================
    def write_base_config(self, config: Dict) -> bool:
        """Write to base.yaml configuration"""
        try:
            # Backup current config
            if self.base_config_path.exists():
                backup_path = self.base_config_path.with_suffix(".yaml.bak")
                with open(self.base_config_path, "r") as f:
                    backup_content = f.read()
                with open(backup_path, "w") as f:
                    f.write(backup_content)

            # Write new config
            with open(self.base_config_path, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            return True
        except Exception as e:
            print(f"Error writing config: {e}")
            return False

    def write_strategy_config(self, config: Dict) -> bool:
        """Write strategy configuration - alias for write_base_config"""
        return self.write_base_config(config)

    def update_config_value(self, path: str, value: Any) -> bool:
        """
        Update a specific value in the base configuration

        path: dot-separated path like "capital.leverage" or "risk.min_confidence_to_trade"
        """
        config = self.read_base_config()
        if not config:
            return False

        # Navigate to the parent and set the value
        keys = path.split(".")
        current = config
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        # Type conversion based on existing value type
        old_value = current.get(keys[-1])
        if old_value is not None:
            if isinstance(old_value, bool):
                if isinstance(value, str):
                    value = value.lower() in ('true', '1', 'yes')
            elif isinstance(old_value, int) and not isinstance(old_value, bool):
                value = int(value)
            elif isinstance(old_value, float):
                value = float(value)

        current[keys[-1]] = value
        return self.write_base_config(config)

    def get_config_value(self, path: str, default: Any = None) -> Any:
        """Get a specific value from configuration"""
        config = self.get_merged_config()
        keys = path.split(".")
        current = config
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    # =========================================================================
    # Configuration Sections (for UI)
    # =========================================================================
    def get_config_sections(self) -> Dict[str, Any]:
        """Get configuration organized by sections for admin UI with field metadata"""
        config = self.get_merged_config()

        # Field definitions with labels, types, and descriptions
        field_definitions = {
            "trading": [
                {"path": "trading.instrument_id", "label": "Instrument ID", "type": "string", "description": "Trading pair identifier"},
                {"path": "trading.timeframe", "label": "Timeframe", "type": "select", "options": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"], "description": "K-line timeframe"},
                {"path": "trading.historical_bars_limit", "label": "Historical Bars", "type": "number", "description": "Number of historical K-lines to fetch"},
            ],
            "capital": [
                {"path": "capital.equity", "label": "Backup Equity", "type": "number", "description": "Fallback equity value (USDT)"},
                {"path": "capital.leverage", "label": "Leverage", "type": "number", "description": "Trading leverage (3-10 recommended)"},
                {"path": "capital.use_real_balance_as_equity", "label": "Use Real Balance", "type": "boolean", "description": "Auto-fetch real balance from Binance"},
            ],
            "position": [
                {"path": "position.base_usdt_amount", "label": "Base Position (USDT)", "type": "number", "description": "Base position size in USDT (min: 100)"},
                {"path": "position.high_confidence_multiplier", "label": "High Confidence Multiplier", "type": "number", "description": "Position multiplier for high confidence signals"},
                {"path": "position.medium_confidence_multiplier", "label": "Medium Confidence Multiplier", "type": "number", "description": "Position multiplier for medium confidence signals"},
                {"path": "position.low_confidence_multiplier", "label": "Low Confidence Multiplier", "type": "number", "description": "Position multiplier for low confidence signals"},
                {"path": "position.max_position_ratio", "label": "Max Position Ratio", "type": "number", "description": "Maximum position as % of equity (0.30 = 30%)"},
                {"path": "position.min_trade_amount", "label": "Min Trade Amount (BTC)", "type": "number", "description": "Minimum trade quantity"},
            ],
            "risk": [
                {"path": "risk.min_confidence_to_trade", "label": "Min Confidence", "type": "select", "options": ["LOW", "MEDIUM", "HIGH"], "description": "Minimum confidence level to execute trades"},
                {"path": "risk.allow_reversals", "label": "Allow Reversals", "type": "boolean", "description": "Allow position direction reversal"},
                {"path": "risk.rsi_extreme_threshold_upper", "label": "RSI Overbought", "type": "number", "description": "RSI overbought threshold"},
                {"path": "risk.rsi_extreme_threshold_lower", "label": "RSI Oversold", "type": "number", "description": "RSI oversold threshold"},
                {"path": "risk.stop_loss.enabled", "label": "Stop Loss Enabled", "type": "boolean", "description": "Enable automatic stop loss"},
                {"path": "risk.stop_loss.buffer_pct", "label": "SL Buffer %", "type": "number", "description": "Stop loss buffer percentage"},
                {"path": "risk.take_profit.high_confidence_pct", "label": "TP High Confidence %", "type": "number", "description": "Take profit for high confidence (0.03 = 3%)"},
                {"path": "risk.take_profit.medium_confidence_pct", "label": "TP Medium Confidence %", "type": "number", "description": "Take profit for medium confidence"},
                {"path": "risk.take_profit.low_confidence_pct", "label": "TP Low Confidence %", "type": "number", "description": "Take profit for low confidence"},
            ],
            # v49.0: AI section removed (mechanical mode, zero AI API calls)
            "indicators": [
                {"path": "indicators.rsi_period", "label": "RSI Period", "type": "number", "description": "RSI calculation period"},
                {"path": "indicators.macd_fast", "label": "MACD Fast", "type": "number", "description": "MACD fast period"},
                {"path": "indicators.macd_slow", "label": "MACD Slow", "type": "number", "description": "MACD slow period"},
                {"path": "indicators.macd_signal", "label": "MACD Signal", "type": "number", "description": "MACD signal period"},
                {"path": "indicators.bb_period", "label": "BB Period", "type": "number", "description": "Bollinger Bands period"},
                {"path": "indicators.bb_std", "label": "BB Std Dev", "type": "number", "description": "Bollinger Bands standard deviation"},
                {"path": "indicators.volume_ma_period", "label": "Volume MA Period", "type": "number", "description": "Volume moving average period"},
            ],
            "telegram": [
                {"path": "telegram.enabled", "label": "Telegram Enabled", "type": "boolean", "description": "Enable Telegram notifications"},
                {"path": "telegram.bot_token", "label": "Control Bot Token", "type": "string", "sensitive": True, "description": "Private control bot token (from .env)"},
                {"path": "telegram.chat_id", "label": "Control Chat ID", "type": "string", "description": "Private chat ID for control commands"},
                # v14.0: Notification group (separate bot for public signal broadcasting)
                {"path": "telegram.notification_group.enabled", "label": "Notification Group Enabled", "type": "boolean", "description": "Enable public notification group broadcasting"},
                {"path": "telegram.notification_group.bot_token", "label": "Notification Bot Token", "type": "string", "sensitive": True, "description": "Notification group bot token (from .env)"},
                {"path": "telegram.notification_group.chat_id", "label": "Notification Group Chat ID", "type": "string", "description": "Notification group chat ID"},
                {"path": "telegram.notification_group.link", "label": "Notification Group Link", "type": "string", "description": "Public invite link (e.g. https://t.me/AlgVex)"},
                {"path": "telegram.community_group.link", "label": "Community Group Link", "type": "string", "description": "Community chat invite link (e.g. https://t.me/AlgVex_Community)"},
                # Notification toggles
                {"path": "telegram.notify.signals", "label": "Notify Signals", "type": "boolean", "description": "Send signal notifications"},
                {"path": "telegram.notify.fills", "label": "Notify Fills", "type": "boolean", "description": "Send order fill notifications"},
                {"path": "telegram.notify.errors", "label": "Notify Errors", "type": "boolean", "description": "Send error notifications"},
                {"path": "telegram.notify.heartbeat", "label": "Notify Heartbeat", "type": "boolean", "description": "Send periodic heartbeat status"},
            ],
            "timing": [
                {"path": "timing.timer_interval_sec", "label": "Timer Interval (sec)", "type": "number", "description": "Analysis interval in seconds (production: 1200s)"},
            ],
            "logging": [
                {"path": "logging.level", "label": "Log Level", "type": "select", "options": ["DEBUG", "INFO", "WARNING", "ERROR"], "description": "Logging verbosity"},
                {"path": "logging.to_file", "label": "Log to File", "type": "boolean", "description": "Write logs to file"},
                {"path": "logging.log_signals", "label": "Log Signals", "type": "boolean", "description": "Log trading signals"},
                # v49.0: log_ai_responses removed (mechanical mode)
            ],
            "multi_timeframe": [
                {"path": "multi_timeframe.enabled", "label": "MTF Enabled", "type": "boolean", "description": "Enable multi-timeframe analysis"},
                {"path": "multi_timeframe.trend_layer.timeframe", "label": "Trend Layer TF", "type": "select", "options": ["1d", "4h", "1h"], "description": "Trend layer timeframe"},
                {"path": "multi_timeframe.trend_layer.sma_period", "label": "Trend SMA Period", "type": "number", "description": "SMA period for trend detection"},
                {"path": "multi_timeframe.decision_layer.timeframe", "label": "Decision Layer TF", "type": "select", "options": ["4h", "1h", "15m"], "description": "Decision layer timeframe"},
                # v49.0: debate_rounds removed (mechanical mode)
                {"path": "multi_timeframe.execution_layer.default_timeframe", "label": "Execution TF", "type": "select", "options": ["30m", "15m", "5m", "1m"], "description": "Execution layer timeframe"},
                {"path": "multi_timeframe.execution_layer.rsi_entry_min", "label": "RSI Entry Min", "type": "number", "description": "Minimum RSI for entry"},
                {"path": "multi_timeframe.execution_layer.rsi_entry_max", "label": "RSI Entry Max", "type": "number", "description": "Maximum RSI for entry"},
            ],
            "order_flow": [
                {"path": "order_flow.enabled", "label": "Order Flow Enabled", "type": "boolean", "description": "Enable order flow analysis"},
                {"path": "order_flow.binance.use_taker_data", "label": "Use Taker Data", "type": "boolean", "description": "Use taker buy/sell volume"},
                {"path": "order_flow.binance.bars_for_analysis", "label": "Bars for Analysis", "type": "number", "description": "Number of recent bars to analyze"},
                {"path": "order_flow.buy_ratio.bullish_threshold", "label": "Bullish Threshold", "type": "number", "description": "Buy ratio above this is bullish (0.55 = 55%)"},
                {"path": "order_flow.buy_ratio.bearish_threshold", "label": "Bearish Threshold", "type": "number", "description": "Buy ratio below this is bearish (0.45 = 45%)"},
                {"path": "order_flow.coinalyze.enabled", "label": "Coinalyze Enabled", "type": "boolean", "description": "Enable Coinalyze derivatives data"},
            ],
            "sentiment": [
                {"path": "sentiment.enabled", "label": "Sentiment Enabled", "type": "boolean", "description": "Enable sentiment analysis"},
                {"path": "sentiment.weight", "label": "Sentiment Weight", "type": "number", "description": "Weight in decision (0.30 = 30%)"},
                {"path": "sentiment.update_interval_minutes", "label": "Update Interval (min)", "type": "number", "description": "Sentiment data refresh interval"},
            ],
            "execution": [
                {"path": "execution.order_type", "label": "Order Type", "type": "select", "options": ["MARKET", "LIMIT"], "description": "Default order type"},
                {"path": "execution.reduce_only_for_closes", "label": "Reduce Only for Closes", "type": "boolean", "description": "Use reduce-only for close orders"},
                {"path": "execution.position_adjustment_threshold", "label": "Position Adjust Threshold", "type": "number", "description": "Min BTC diff to adjust position"},
            ],
            "network": [
                {"path": "network.binance.api_timeout", "label": "API Timeout (sec)", "type": "number", "description": "Binance API request timeout"},
                {"path": "network.binance.balance_cache_ttl", "label": "Balance Cache TTL (sec)", "type": "number", "description": "Balance cache duration"},
                {"path": "network.telegram.message_timeout", "label": "Telegram Timeout (sec)", "type": "number", "description": "Telegram message timeout"},
            ],
        }

        # Build sections list with populated values
        sections = []
        section_meta = {
            "trading": ("Trading Configuration", "Core trading parameters"),
            "capital": ("Capital & Leverage", "Account funding and leverage settings"),
            "position": ("Position Management", "Position sizing and limits"),
            "risk": ("Risk Management", "Stop loss, take profit, and risk controls"),
            # v49.0: AI section removed
            "indicators": ("Technical Indicators", "Indicator periods and parameters"),
            "telegram": ("Telegram Notifications", "Notification settings"),
            "timing": ("Timing", "Timer and interval settings"),
            "logging": ("Logging", "Log levels and outputs"),
            "multi_timeframe": ("Multi-Timeframe (MTF)", "Three-layer MTF framework settings"),
            "order_flow": ("Order Flow", "Order flow and derivatives data"),
            "sentiment": ("Sentiment Analysis", "Market sentiment settings"),
            "execution": ("Execution", "Order execution settings"),
            "network": ("Network", "API and network settings"),
        }

        for section_id, fields in field_definitions.items():
            if section_id not in section_meta:
                continue

            title, description = section_meta[section_id]
            populated_fields = []

            for field in fields:
                path = field["path"]
                value = self.get_config_value(path)
                populated_fields.append({
                    **field,
                    "value": value
                })

            sections.append({
                "id": section_id,
                "title": title,
                "description": description,
                "fields": populated_fields,
            })

        return {"sections": sections}

    # =========================================================================
    # Service Control
    # =========================================================================
    def get_service_status(self) -> Dict:
        """Get AlgVex systemd service status"""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", self.service_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            is_active = result.stdout.strip() == "active"

            # Get more details
            result = subprocess.run(
                ["systemctl", "show", self.service_name,
                 "--property=ActiveState,SubState,MainPID,MemoryCurrent,CPUUsageNSec,ExecMainStartTimestamp"],
                capture_output=True,
                text=True,
                timeout=5
            )
            props = {}
            for line in result.stdout.strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    props[k] = v

            # Parse uptime
            start_time = props.get("ExecMainStartTimestamp", "")
            uptime = ""
            if start_time and is_active:
                try:
                    # Parse format like "Thu 2024-01-30 10:00:00 UTC"
                    parts = start_time.split()
                    if len(parts) >= 3:
                        date_str = f"{parts[1]} {parts[2]}"
                        start_dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                        delta = datetime.now() - start_dt
                        hours, remainder = divmod(int(delta.total_seconds()), 3600)
                        minutes, seconds = divmod(remainder, 60)
                        if hours > 24:
                            days = hours // 24
                            hours = hours % 24
                            uptime = f"{days}d {hours}h {minutes}m"
                        else:
                            uptime = f"{hours}h {minutes}m {seconds}s"
                except Exception:
                    pass

            # Parse memory
            memory = props.get("MemoryCurrent", "0")
            try:
                memory_mb = int(memory) / (1024 * 1024)
                memory_str = f"{memory_mb:.1f} MB"
            except Exception:
                memory_str = "N/A"

            return {
                "running": is_active,
                "state": props.get("ActiveState", "unknown"),
                "sub_state": props.get("SubState", "unknown"),
                "pid": props.get("MainPID", "0"),
                "memory": memory_str,
                "uptime": uptime,
                "start_time": start_time,
            }
        except Exception as e:
            print(f"Error getting service status: {e}")
            return {
                "running": False,
                "state": "error",
                "sub_state": str(e),
                "pid": "0",
                "memory": "N/A",
                "uptime": "",
                "start_time": "",
            }

    def restart_service(self) -> Tuple[bool, str]:
        """Restart AlgVex service"""
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "restart", self.service_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                return True, "Service restarted successfully"
            else:
                return False, result.stderr or "Failed to restart service"
        except subprocess.TimeoutExpired:
            return False, "Restart command timed out"
        except Exception as e:
            return False, str(e)

    def stop_service(self) -> Tuple[bool, str]:
        """Stop AlgVex service"""
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "stop", self.service_name],
                capture_output=True,
                text=True,
                timeout=15
            )
            if result.returncode == 0:
                return True, "Service stopped successfully"
            else:
                return False, result.stderr or "Failed to stop service"
        except Exception as e:
            return False, str(e)

    def start_service(self) -> Tuple[bool, str]:
        """Start AlgVex service"""
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "start", self.service_name],
                capture_output=True,
                text=True,
                timeout=15
            )
            if result.returncode == 0:
                return True, "Service started successfully"
            else:
                return False, result.stderr or "Failed to start service"
        except Exception as e:
            return False, str(e)

    # =========================================================================
    # Logs
    # =========================================================================
    def get_recent_logs(self, lines: int = 100) -> str:
        """Get recent service logs from journalctl"""
        try:
            result = subprocess.run(
                ["journalctl", "-u", self.service_name, "-n", str(lines),
                 "--no-pager", "--no-hostname"],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.stdout
        except Exception as e:
            return f"Error fetching logs: {e}"

    def get_log_file_content(self, lines: int = 200) -> str:
        """Get recent logs from the log file"""
        log_path = self.algvex_path / "logs" / "ai_strategy.log"
        try:
            if log_path.exists():
                with open(log_path, "r") as f:
                    all_lines = f.readlines()
                    return "".join(all_lines[-lines:])
            return "Log file not found"
        except Exception as e:
            return f"Error reading log file: {e}"

    # =========================================================================
    # System Info
    # =========================================================================
    def get_system_info(self) -> Dict:
        """Get system information including trading system version"""
        # Detect trading system version from CLAUDE.md or latest changelog
        system_version = "v49.0"  # Mechanical hybrid decision mode

        # Read active environment
        config = self.read_base_config()
        active_env = "production"
        prod_config = self.configs_path / "production.yaml"
        if prod_config.exists():
            active_env = "production"

        # v49.0: Strategy mode from config
        strategy_mode = "mechanical"
        try:
            strategy_mode = config.get("anticipatory", {}).get("zone_entry", {}).get("enabled", True) and "mechanical" or "unknown"
        except (AttributeError, TypeError):
            pass

        info = {
            "algvex_path": str(self.algvex_path),
            "service_name": self.service_name,
            "system_version": system_version,
            "strategy_mode": strategy_mode,
            "active_env": active_env,
            "python_version": "",
            "nautilus_version": "",
            "git_branch": "",
            "git_commit": "",
            "git_commit_date": "",
            "web_version": "2.0.0",
        }

        try:
            # Python version
            result = subprocess.run(
                [str(self.algvex_path / "venv" / "bin" / "python"), "--version"],
                capture_output=True, text=True, timeout=5
            )
            info["python_version"] = result.stdout.strip()
        except Exception:
            pass

        try:
            # NautilusTrader version
            result = subprocess.run(
                [str(self.algvex_path / "venv" / "bin" / "pip"), "show", "nautilus_trader"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split("\n"):
                if line.startswith("Version:"):
                    info["nautilus_version"] = line.split(":")[1].strip()
                    break
        except Exception:
            pass

        try:
            # Git info
            result = subprocess.run(
                ["git", "-C", str(self.algvex_path), "branch", "--show-current"],
                capture_output=True, text=True, timeout=5
            )
            info["git_branch"] = result.stdout.strip()

            result = subprocess.run(
                ["git", "-C", str(self.algvex_path), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5
            )
            info["git_commit"] = result.stdout.strip()

            result = subprocess.run(
                ["git", "-C", str(self.algvex_path), "log", "-1", "--format=%ci"],
                capture_output=True, text=True, timeout=5
            )
            info["git_commit_date"] = result.stdout.strip()
        except Exception:
            pass

        return info

    # =========================================================================
    # Diagnostics
    # =========================================================================
    def run_diagnostics(self) -> Dict:
        """Run basic diagnostics and return checks in UI-friendly format"""
        checks = []

        # Check config
        config_valid = False
        try:
            config = self.get_merged_config()
            if config:
                config_valid = True
                checks.append({
                    "name": "Configuration",
                    "status": "pass",
                    "message": "configs/base.yaml loaded successfully"
                })
            else:
                checks.append({
                    "name": "Configuration",
                    "status": "fail",
                    "message": "Failed to load configuration"
                })
        except Exception as e:
            checks.append({
                "name": "Configuration",
                "status": "fail",
                "message": f"Config error: {e}"
            })

        # Check service
        status = self.get_service_status()
        service_running = status.get("running", False)
        if service_running:
            checks.append({
                "name": "Trading Service",
                "status": "pass",
                "message": f"nautilus-trader is running (PID: {status.get('pid', 'N/A')})"
            })
        else:
            checks.append({
                "name": "Trading Service",
                "status": "fail",
                "message": f"Service is not running (state: {status.get('state', 'unknown')})"
            })

        # Check API keys (from env file)
        env_path = Path.home() / ".env.algvex"
        binance_configured = False
        telegram_configured = False
        # v49.0: deepseek_configured removed (mechanical mode)

        if env_path.exists():
            try:
                with open(env_path) as f:
                    content = f.read()
                    if "BINANCE_API_KEY=" in content and "BINANCE_API_SECRET=" in content:
                        # Check if they have actual values (not empty)
                        binance_configured = "BINANCE_API_KEY=\"\"" not in content and "BINANCE_API_KEY=''" not in content
                    if "TELEGRAM_BOT_TOKEN=" in content:
                        telegram_configured = "TELEGRAM_BOT_TOKEN=\"\"" not in content
                    # v49.0: DeepSeek check removed

                checks.append({
                    "name": "Environment File",
                    "status": "pass",
                    "message": "~/.env.algvex found"
                })
            except Exception as e:
                checks.append({
                    "name": "Environment File",
                    "status": "fail",
                    "message": f"Error reading env file: {e}"
                })
        else:
            checks.append({
                "name": "Environment File",
                "status": "fail",
                "message": "~/.env.algvex not found"
            })

        # Binance API
        if binance_configured:
            checks.append({
                "name": "Binance API",
                "status": "pass",
                "message": "API credentials configured"
            })
        else:
            checks.append({
                "name": "Binance API",
                "status": "fail",
                "message": "BINANCE_API_KEY or BINANCE_API_SECRET not set"
            })

        # v49.0: DeepSeek diagnostic removed (mechanical mode, zero AI API calls)

        # Telegram
        if telegram_configured:
            checks.append({
                "name": "Telegram Bot",
                "status": "pass",
                "message": "Bot token configured"
            })
        else:
            checks.append({
                "name": "Telegram Bot",
                "status": "warn",
                "message": "TELEGRAM_BOT_TOKEN not set (optional)"
            })

        # Check log directory
        log_dir = self.algvex_path / "logs"
        if log_dir.exists():
            checks.append({
                "name": "Log Directory",
                "status": "pass",
                "message": f"{log_dir} exists"
            })
        else:
            checks.append({
                "name": "Log Directory",
                "status": "warn",
                "message": "logs/ directory not found"
            })

        # Check venv
        venv_python = self.algvex_path / "venv" / "bin" / "python"
        if venv_python.exists():
            checks.append({
                "name": "Python Virtual Environment",
                "status": "pass",
                "message": "venv/bin/python exists"
            })
        else:
            checks.append({
                "name": "Python Virtual Environment",
                "status": "fail",
                "message": "venv not found - run setup.sh"
            })

        return {"checks": checks}


# Singleton instance
config_service = ConfigService()
