# utils/config_manager.py

from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from dataclasses import dataclass, field
from dotenv import load_dotenv
import os
import logging


@dataclass
class ConfigValidationError:
    """配置验证错误"""
    field: str
    message: str
    value: Any
    severity: str = "error"  # error / warning


class ConfigManager:
    """
    统一配置管理器

    注意: 此类负责加载和合并配置，最终配置应传递给
    NautilusTrader 的 StrategyConfig 子类进行类型验证。

    功能:
    - 分层加载配置 (base → env → .env)
    - 深度合并配置字典
    - 基础验证（范围检查、依赖验证）
    - 环境切换
    - 配置迁移日志

    集成方式:
        config_dict = ConfigManager(env='production').load()
        strategy_config = AITradingStrategyConfig(**config_dict['strategy'])
    """

    # 路径别名映射: 旧路径 → 新路径 (用于向后兼容)
    PATH_ALIASES = {
        ('strategy', 'instrument_id'): ('trading', 'instrument_id'),
        ('strategy', 'bar_type'): ('trading', 'bar_type'),
        ('strategy', 'equity'): ('capital', 'equity'),
        ('strategy', 'leverage'): ('capital', 'leverage'),
        ('strategy', 'use_real_balance_as_equity'): ('capital', 'use_real_balance_as_equity'),
        ('strategy', 'position_management'): ('position',),
        ('strategy', 'indicators'): ('indicators',),
        # v49.0: ('strategy', 'deepseek') alias removed (mechanical mode)
        ('strategy', 'risk'): ('risk',),
        ('strategy', 'telegram'): ('telegram',),
        ('strategy', 'timer_interval_sec'): ('timing', 'timer_interval_sec'),
        ('strategy', 'execution'): ('execution',),
    }

    def __init__(
        self,
        config_dir: Path = None,
        env: str = "production",
        logger: logging.Logger = None
    ):
        """
        初始化配置管理器

        Parameters
        ----------
        config_dir : Path
            配置目录，默认为项目根目录/configs
        env : str
            环境名称: production / development / backtest
        logger : logging.Logger
            日志记录器
        """
        self.config_dir = config_dir or Path(__file__).parent.parent / "configs"
        self.env = env
        self._config: Dict[str, Any] = {}
        self._errors: List[ConfigValidationError] = []
        self._warnings: List[ConfigValidationError] = []
        self.logger = logger or logging.getLogger(__name__)

    def load(self) -> Dict[str, Any]:
        """
        加载并合并所有配置

        Returns
        -------
        dict
            合并后的配置字典
        """
        self.logger.info(f"Loading configuration for environment: {self.env}")

        # 1. 加载 base.yaml
        base_config = self._load_yaml("base.yaml")
        self._config = base_config
        self.logger.debug(f"Loaded base.yaml with {len(base_config)} top-level keys")

        # 2. 加载环境配置并合并
        env_file = f"{self.env}.yaml"
        if (self.config_dir / env_file).exists():
            env_config = self._load_yaml(env_file)
            self._config = self._deep_merge(self._config, env_config)
            self.logger.debug(f"Merged {env_file}")
        else:
            self.logger.warning(f"Environment config not found: {env_file}")

        # 3. 加载 .env 敏感信息
        self._load_env_secrets()

        # 4. 验证配置
        self.validate()

        # 5. 打印配置摘要
        self._log_config_summary()

        return self._config

    def _load_yaml(self, filename: str) -> Dict[str, Any]:
        """加载 YAML 文件"""
        path = self.config_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}

    def _deep_merge(self, base: dict, override: dict) -> dict:
        """
        深度合并字典，override 覆盖 base
        """
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def _load_env_secrets(self):
        """从 .env 加载敏感信息"""
        # 加载 ~/.env.algvex
        env_path = Path.home() / ".env.algvex"
        if env_path.exists():
            load_dotenv(env_path)
            self.logger.debug(f"Loaded secrets from {env_path}")
        else:
            self.logger.warning(f"Secrets file not found: {env_path}")

        # 映射环境变量到配置 (完整映射，共 9 个核心变量)
        env_mappings = {
            # Binance 主网 API
            'BINANCE_API_KEY': ('binance', 'api_key'),
            'BINANCE_API_SECRET': ('binance', 'api_secret'),

            # Binance 测试网 API (可选，回测/开发环境)
            'BINANCE_TESTNET_API_KEY': ('binance', 'testnet_api_key'),
            'BINANCE_TESTNET_API_SECRET': ('binance', 'testnet_api_secret'),

            # v49.0: DEEPSEEK_API_KEY mapping removed (mechanical mode, zero AI calls)

            # Telegram 通知
            'TELEGRAM_BOT_TOKEN': ('telegram', 'bot_token'),
            'TELEGRAM_CHAT_ID': ('telegram', 'chat_id'),
            # v14.0: 通知群机器人 (独立 bot token)
            'TELEGRAM_NOTIFICATION_BOT_TOKEN': ('telegram', 'notification_group', 'bot_token'),
            'TELEGRAM_NOTIFICATION_CHAT_ID': ('telegram', 'notification_group', 'chat_id'),

            # 运行模式控制
            'TEST_MODE': ('runtime', 'test_mode'),
            'AUTO_CONFIRM': ('runtime', 'auto_confirm'),
        }

        for env_var, config_path in env_mappings.items():
            value = os.getenv(env_var)
            if value:
                # Convert string 'true'/'false' to boolean for boolean fields
                if env_var in ['TEST_MODE', 'AUTO_CONFIRM']:
                    value = value.lower() in ['true', '1', 'yes']
                self._set_nested(self._config, config_path, value)

    def _set_nested(self, d: dict, path: tuple, value: Any):
        """设置嵌套字典值 (自动创建/修复父级字典)"""
        for key in path[:-1]:
            # 如果键不存在，创建空字典
            if key not in d:
                d[key] = {}
            # 如果键存在但不是字典 (例如 YAML 中的 None)，替换为空字典
            elif not isinstance(d[key], dict):
                d[key] = {}
            d = d[key]
        d[path[-1]] = value

    def validate(self) -> bool:
        """
        验证配置

        Returns
        -------
        bool
            是否通过验证
        """
        self._errors = []
        self._warnings = []

        # 类型和范围验证规则
        # (字段路径, 类型, 最小值, 最大值, 必填)
        rules = [
            # 资金配置
            (('capital', 'equity'), (int, float), 100, 1000000, True),
            (('capital', 'leverage'), (int, float), 1, 125, True),

            # 仓位管理
            (('position', 'base_usdt_amount'), (int, float), 100, None, True),
            (('position', 'max_position_ratio'), float, 0.01, 1.0, True),
            (('position', 'min_trade_amount'), float, 0.0001, 1.0, True),

            # 风险管理
            (('risk', 'rsi_extreme_threshold_upper'), (int, float), 50, 100, True),
            (('risk', 'rsi_extreme_threshold_lower'), (int, float), 0, 50, True),

            # 交易逻辑
            (('trading_logic', 'min_notional_usdt'), (int, float), 1, 10000, True),
            (('trading_logic', 'min_sl_distance_pct'), float, 0.001, 0.1, True),
            (('trading_logic', 'default_sl_pct'), float, 0.005, 0.2, True),
            # v7.1: R/R gate — core safety parameter, must be validated
            (('trading_logic', 'min_rr_ratio'), (int, float), 1.0, 5.0, True),
            (('trading_logic', 'counter_trend_rr_multiplier'), (int, float), 1.0, 3.0, True),

            # 定时器
            (('timing', 'timer_interval_sec'), int, 60, 86400, True),

            # v46.0: AI config validation removed (mechanical mode, no DeepSeek)

            # 网络配置
            (('network', 'instrument_discovery', 'max_retries'), int, 1, 300, True),
            (('network', 'binance', 'recv_window'), int, 1000, 60000, True),
            (('network', 'bar_persistence', 'max_limit'), int, 100, 2000, True),
            (('network', 'bar_persistence', 'timeout'), int, 1, 60, True),
            (('network', 'oco_manager', 'socket_timeout'), int, 1, 30, True),

            # 交易逻辑
            (('trading_logic', 'quantity_adjustment_step'), float, 0.0001, 0.01, True),
        ]

        for path, expected_type, min_val, max_val, required in rules:
            value = self._get_nested(self._config, path)

            if value is None:
                if required:
                    self._errors.append(ConfigValidationError(
                        field='.'.join(path),
                        message="Required field is missing",
                        value=None
                    ))
                continue

            # 类型检查
            if not isinstance(value, expected_type):
                self._errors.append(ConfigValidationError(
                    field='.'.join(path),
                    message=f"Expected {expected_type}, got {type(value).__name__}",
                    value=value
                ))
                continue

            # 范围检查
            if min_val is not None and value < min_val:
                self._errors.append(ConfigValidationError(
                    field='.'.join(path),
                    message=f"Value {value} is below minimum {min_val}",
                    value=value
                ))

            if max_val is not None and value > max_val:
                self._errors.append(ConfigValidationError(
                    field='.'.join(path),
                    message=f"Value {value} is above maximum {max_val}",
                    value=value
                ))

        # 依赖验证
        self._validate_dependencies()

        return len(self._errors) == 0

    def _validate_dependencies(self):
        """验证配置依赖关系"""
        # RSI 阈值顺序
        rsi_upper = self.get('risk', 'rsi_extreme_threshold_upper')
        rsi_lower = self.get('risk', 'rsi_extreme_threshold_lower')
        if rsi_upper and rsi_lower and rsi_lower >= rsi_upper:
            self._errors.append(ConfigValidationError(
                field='risk.rsi_extreme_threshold_*',
                message=f"RSI lower ({rsi_lower}) must be less than upper ({rsi_upper})",
                value=(rsi_lower, rsi_upper)
            ))

        # MACD 周期顺序
        macd_fast = self.get('indicators', 'macd_fast')
        macd_slow = self.get('indicators', 'macd_slow')
        if macd_fast and macd_slow and macd_fast >= macd_slow:
            self._errors.append(ConfigValidationError(
                field='indicators.macd_*',
                message=f"MACD fast ({macd_fast}) must be less than slow ({macd_slow})",
                value=(macd_fast, macd_slow)
            ))

        # Telegram 依赖
        if self.get('telegram', 'enabled'):
            if not self.get('telegram', 'bot_token'):
                self._warnings.append(ConfigValidationError(
                    field='telegram.bot_token',
                    message="Telegram enabled but bot_token not set",
                    value=None,
                    severity="warning"
                ))
            if not self.get('telegram', 'chat_id'):
                self._warnings.append(ConfigValidationError(
                    field='telegram.chat_id',
                    message="Telegram enabled but chat_id not set",
                    value=None,
                    severity="warning"
                ))

    def _get_nested(self, d: dict, path: tuple) -> Any:
        """获取嵌套字典值"""
        for key in path:
            if not isinstance(d, dict) or key not in d:
                return None
            d = d[key]
        return d

    def get(self, *path, default=None) -> Any:
        """
        获取配置值，支持路径别名兼容

        Example:
            config.get('capital', 'equity')
            config.get('ai', 'deepseek', 'temperature')

        旧路径也支持（向后兼容）:
            config.get('strategy', 'deepseek', 'temperature')  # → ai.deepseek.temperature
        """
        # 1. 先尝试原始路径
        value = self._get_nested(self._config, path)
        if value is not None:
            return value

        # 2. 尝试路径别名
        for old_prefix, new_prefix in self.PATH_ALIASES.items():
            if path[:len(old_prefix)] == old_prefix:
                new_path = new_prefix + path[len(old_prefix):]
                value = self._get_nested(self._config, new_path)
                if value is not None:
                    self.logger.debug(f"Path alias: {path} → {new_path}")
                    return value

        # 3. 特殊处理: skip_on_divergence 和 use_confidence_fusion
        # 这两个参数从 strategy.risk.* 移到 ai.signal.*
        if path == ('ai', 'signal', 'skip_on_divergence'):
            value = (
                self._get_nested(self._config, ('ai', 'signal', 'skip_on_divergence'))
                or self._get_nested(self._config, ('strategy', 'risk', 'skip_on_divergence'))
            )
            if value is not None:
                return value

        if path == ('ai', 'signal', 'use_confidence_fusion'):
            value = (
                self._get_nested(self._config, ('ai', 'signal', 'use_confidence_fusion'))
                or self._get_nested(self._config, ('strategy', 'risk', 'use_confidence_fusion'))
            )
            if value is not None:
                return value

        return default

    def get_errors(self) -> List[ConfigValidationError]:
        """获取验证错误列表"""
        return self._errors

    def get_warnings(self) -> List[ConfigValidationError]:
        """获取验证警告列表"""
        return self._warnings

    def _log_config_summary(self):
        """记录配置摘要"""
        self.logger.info("=" * 50)
        self.logger.info("Configuration Summary")
        self.logger.info("=" * 50)
        self.logger.info(f"  Environment: {self.env}")
        self.logger.info(f"  Instrument: {self.get('trading', 'instrument_id')}")
        equity = self.get('capital', 'equity')
        if equity:
            self.logger.info(f"  Equity: ${equity:,.2f}")
        self.logger.info(f"  Leverage: {self.get('capital', 'leverage')}x")
        self.logger.info(f"  Timer: {self.get('timing', 'timer_interval_sec')}s")
        self.logger.info(f"  Strategy Mode: {self.get('strategy_mode', default='mechanical')}")
        self.logger.info(f"  RSI Thresholds: {self.get('risk', 'rsi_extreme_threshold_lower')}/{self.get('risk', 'rsi_extreme_threshold_upper')}")
        self.logger.info(f"  Telegram: {'Enabled' if self.get('telegram', 'enabled') else 'Disabled'}")

        if self._errors:
            self.logger.error(f"  Validation Errors: {len(self._errors)}")
            for error in self._errors:
                self.logger.error(f"    - {error.field}: {error.message}")
        else:
            self.logger.info("  Validation: PASSED")

        if self._warnings:
            self.logger.warning(f"  Warnings: {len(self._warnings)}")
            for warning in self._warnings:
                self.logger.warning(f"    - {warning.field}: {warning.message}")

        self.logger.info("=" * 50)

    def print_summary(self):
        """打印配置摘要到控制台 (用于命令行测试)"""
        print("=" * 60)
        print("  Configuration Summary")
        print("=" * 60)
        print(f"  Environment: {self.env}")
        print(f"  Instrument: {self.get('trading', 'instrument_id')}")
        equity = self.get('capital', 'equity')
        if equity:
            print(f"  Equity: ${equity:,.2f}")
        print(f"  Leverage: {self.get('capital', 'leverage')}x")
        print(f"  Timer: {self.get('timing', 'timer_interval_sec')}s")
        print(f"  Strategy Mode: {self.get('strategy_mode', default='mechanical')}")
        print(f"  RSI Thresholds: {self.get('risk', 'rsi_extreme_threshold_lower')}/{self.get('risk', 'rsi_extreme_threshold_upper')}")
        print(f"  Telegram: {'Enabled' if self.get('telegram', 'enabled') else 'Disabled'}")

        if self._errors:
            print(f"\n  ⚠️ Validation Errors ({len(self._errors)}):")
            for error in self._errors:
                print(f"    - {error.field}: {error.message}")
        else:
            print("\n  ✅ Configuration validated successfully")

        if self._warnings:
            print(f"\n  ⚠️ Warnings ({len(self._warnings)}):")
            for warning in self._warnings:
                print(f"    - {warning.field}: {warning.message}")

        print("=" * 60)

    def _mask_sensitive(self, value: str) -> str:
        """
        掩蔽敏感信息

        改进版本 (v2.5.4):
        - 修复 8 字符密钥不被掩蔽的漏洞
        - 任何长度 >= 6 的值都掩蔽
        """
        if not isinstance(value, str):
            return value
        if not value:
            return "(未设置)"
        # 任何长度 >= 6 的值都掩蔽
        if len(value) >= 6:
            return f"{value[:4]}***{value[-2:]}"
        return "***"  # 太短的值完全隐藏


# Singleton instance
_instance: Optional[ConfigManager] = None


def get_config(env: str = "production", reload: bool = False) -> ConfigManager:
    """
    获取 ConfigManager 单例

    Parameters
    ----------
    env : str
        环境名称
    reload : bool
        是否强制重新加载配置

    Returns
    -------
    ConfigManager
        配置管理器实例
    """
    global _instance
    if _instance is None or reload:
        _instance = ConfigManager(env=env)
        _instance.load()
    return _instance
