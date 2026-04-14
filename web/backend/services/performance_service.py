"""
Performance Analytics Service - v4.0
直接调用币安账户 API 获取数据，确保与币安显示一致。

核心原则:
- 账户余额: 直接从 /fapi/v2/account 获取 (不自己算)
- Net PnL: 包含 REALIZED_PNL + FUNDING_FEE + COMMISSION (和币安一致)
- 分页: 自动翻页获取全量 income 记录 (不再限制 1000 条)
- 简单: 不过度计算，数据来源 = 币安 API
"""
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import hmac
import hashlib
import time
import httpx
import logging

import numpy as np
import pandas as pd

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from dotenv import load_dotenv

# Load environment variables from multiple possible locations
env_paths = [
    os.path.expanduser("~/.env.algvex"),
    os.path.join(os.path.dirname(__file__), "../../../.env"),
    os.path.join(os.path.dirname(__file__), "../.env"),
    ".env"
]

for env_path in env_paths:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        logger.info(f"Loaded env from: {env_path}")
        break


class PerformanceService:
    """直接调用币安 API 获取交易绩效数据"""

    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_API_SECRET", "")
        self.base_url = "https://fapi.binance.com"
        self._client: Optional[httpx.AsyncClient] = None

        if self.api_key:
            logger.info("API Key loaded: ****...****")
        else:
            logger.warning("BINANCE_API_KEY not found!")
        if self.api_secret:
            logger.info("API Secret loaded: ****...****")
        else:
            logger.warning("BINANCE_API_SECRET not found!")

    @asynccontextmanager
    async def _get_client(self):
        """Shared httpx client with connection pooling"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        yield self._client

    def _sign_request(self, params: dict) -> dict:
        """Sign request with HMAC SHA256"""
        params["timestamp"] = int(time.time() * 1000)
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    # =========================================================================
    # 币安 API 直接调用
    # =========================================================================

    async def _get_account_info(self) -> dict:
        """
        直接调用 /fapi/v2/account 获取账户信息。
        返回: totalWalletBalance, totalUnrealizedProfit, availableBalance 等
        """
        if not self.api_key or not self.api_secret:
            return {}

        try:
            params = self._sign_request({})
            headers = {"X-MBX-APIKEY": self.api_key}

            async with self._get_client() as client:
                response = await client.get(
                    f"{self.base_url}/fapi/v2/account",
                    params=params,
                    headers=headers,
                    timeout=10.0
                )

                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"Account info: balance={data.get('totalWalletBalance')}, "
                                f"unrealizedPnL={data.get('totalUnrealizedProfit')}")
                    return data
                else:
                    logger.error(f"Account API failed: {response.status_code} - {response.text}")
                    return {}
        except Exception as e:
            logger.error(f"Error fetching account info: {e}")
            return {}

    async def _get_income_paginated(self, days: int = 90) -> list:
        """
        分页获取全量 income 记录 (所有类型)。
        币安 /fapi/v1/income 每次最多 1000 条，自动翻页直到拿完。
        """
        if not self.api_key or not self.api_secret:
            return []

        all_records = []
        start_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
        max_pages = 20  # 安全上限，防止无限循环

        try:
            headers = {"X-MBX-APIKEY": self.api_key}

            async with self._get_client() as client:
                for page in range(max_pages):
                    params = self._sign_request({
                        "startTime": start_time,
                        "limit": 1000
                    })

                    response = await client.get(
                        f"{self.base_url}/fapi/v1/income",
                        params=params,
                        headers=headers,
                        timeout=15.0
                    )

                    if response.status_code != 200:
                        logger.error(f"Income API failed (page {page}): {response.status_code}")
                        break

                    batch = response.json()
                    if not batch:
                        break

                    all_records.extend(batch)

                    # 如果不到 1000 条，说明已经拿完
                    if len(batch) < 1000:
                        break

                    # 翻页: startTime = 最后一条的时间 + 1ms
                    start_time = batch[-1]["time"] + 1

            logger.info(f"Fetched {len(all_records)} total income records ({days} days, {page + 1} pages)")
            return all_records

        except Exception as e:
            logger.error(f"Error fetching income history: {e}")
            return all_records

    async def check_connection(self) -> dict:
        """Diagnostic: Check API connectivity and credentials"""
        result = {
            "api_key_loaded": bool(self.api_key),
            "api_secret_loaded": bool(self.api_secret),
            "api_key_preview": f"{self.api_key[:8]}...{self.api_key[-4:]}" if self.api_key else None,
            "connection_ok": False,
            "account_accessible": False,
            "balance": 0,
            "error": None
        }

        if not self.api_key or not self.api_secret:
            result["error"] = "API credentials not loaded"
            return result

        try:
            account = await self._get_account_info()
            if account:
                result["connection_ok"] = True
                result["account_accessible"] = True
                result["balance"] = float(account.get("totalWalletBalance", 0))
            else:
                result["error"] = "Failed to fetch account info"
        except Exception as e:
            result["error"] = str(e)

        return result

    # =========================================================================
    # 绩效统计 (直接基于币安数据)
    # =========================================================================

    async def get_performance_stats(self) -> dict:
        """
        直接从币安 API 获取绩效数据。
        - 账户余额: /fapi/v2/account
        - PnL 历史: /fapi/v1/income (全量分页)
        - Net PnL = REALIZED_PNL + FUNDING_FEE + COMMISSION (和币安一致)
        """
        # 1. 获取账户信息
        account = await self._get_account_info()
        wallet_balance = float(account.get("totalWalletBalance", 0))
        unrealized_pnl = float(account.get("totalUnrealizedProfit", 0))
        available_balance = float(account.get("availableBalance", 0))

        # 2. 获取全量 income 记录 (分页)
        all_income = await self._get_income_paginated(days=90)

        # 3. 按类型分类
        income_by_type: Dict[str, float] = {}
        for record in all_income:
            income_type = record.get("incomeType", "UNKNOWN")
            amount = float(record.get("income", 0))
            income_by_type[income_type] = income_by_type.get(income_type, 0) + amount

        logger.info(f"Income breakdown: {income_by_type}")

        # 4. Net PnL = 所有 income 加总 (和币安账户一致)
        net_pnl = sum(float(r["income"]) for r in all_income)

        # 5. 提取 REALIZED_PNL 记录用于交易统计
        realized_records = [r for r in all_income if r.get("incomeType") == "REALIZED_PNL"]
        realized_pnl_values = [float(r["income"]) for r in realized_records]

        total_trades = len(realized_pnl_values)
        wins = [p for p in realized_pnl_values if p > 0]
        losses = [p for p in realized_pnl_values if p < 0]
        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0
        risk_reward = avg_win / avg_loss if avg_loss > 0 else 0

        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        best_trade = max(realized_pnl_values) if realized_pnl_values else 0
        worst_trade = min(realized_pnl_values) if realized_pnl_values else 0

        # 6. Net PnL 百分比 (基于: 当前余额 - net_pnl = 起始资金)
        initial_equity = wallet_balance - net_pnl if wallet_balance > 0 else 0
        # 如果 initial_equity <= 0 (可能有额外入金/出金), 用当前余额
        if initial_equity <= 0:
            initial_equity = wallet_balance
        total_pnl_percent = (net_pnl / initial_equity * 100) if initial_equity > 0 else 0

        # 7. Equity curve + Max Drawdown (基于每日 ALL income)
        daily_pnl: Dict[str, float] = {}
        for record in all_income:
            ts = datetime.fromtimestamp(record["time"] / 1000)
            date_str = ts.strftime("%Y-%m-%d")
            daily_pnl[date_str] = daily_pnl.get(date_str, 0) + float(record["income"])

        sorted_dates = sorted(daily_pnl.keys())
        equity_curve = []
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for date_str in sorted_dates:
            cumulative += daily_pnl[date_str]
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
            equity_curve.append({
                "date": date_str,
                "daily_pnl": round(daily_pnl[date_str], 2),
                "cumulative_pnl": round(cumulative, 2)
            })

        max_drawdown_percent = (max_dd / initial_equity * 100) if initial_equity > 0 else 0

        # 8. Time-based PnL (ALL income types, 和币安一致)
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())
        month_start = today_start.replace(day=1)

        today_pnl = 0.0
        week_pnl = 0.0
        month_pnl = 0.0

        for record in all_income:
            ts = datetime.fromtimestamp(record["time"] / 1000)
            amount = float(record["income"])
            if ts >= today_start:
                today_pnl += amount
            if ts >= week_start:
                week_pnl += amount
            if ts >= month_start:
                month_pnl += amount

        # 9. Sharpe Ratio (基于每日净收益率, 简单但正确)
        sharpe_ratio = 0.0
        sortino_ratio = 0.0
        if len(sorted_dates) > 1 and initial_equity > 0:
            daily_returns = pd.Series([daily_pnl[d] / initial_equity for d in sorted_dates])
            mean_return = daily_returns.mean()
            std_return = daily_returns.std()
            if std_return > 0:
                sharpe_ratio = (mean_return / std_return) * (365 ** 0.5)  # 年化 (crypto 24/7)
            # Sortino: 只用负收益的标准差
            downside = daily_returns[daily_returns < 0]
            downside_std = downside.std() if len(downside) > 1 else 0
            if downside_std > 0:
                sortino_ratio = (mean_return / downside_std) * (365 ** 0.5)

        # Handle NaN
        if pd.isna(sharpe_ratio):
            sharpe_ratio = 0.0
        if pd.isna(sortino_ratio):
            sortino_ratio = 0.0

        return {
            # 交易统计 (基于 REALIZED_PNL 记录)
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": round(win_rate, 1),
            # PnL (Net = 包含手续费和资金费率, 和币安一致)
            "total_pnl": round(net_pnl, 2),
            "total_pnl_percent": round(total_pnl_percent, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "risk_reward": round(risk_reward, 2),
            "best_trade": round(best_trade, 2),
            "worst_trade": round(worst_trade, 2),
            # 风险指标
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_percent": round(max_drawdown_percent, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "sortino_ratio": round(sortino_ratio, 2),
            # calmar_ratio/var_95/cvar_99: 已删除 (未计算，不在前端展示)
            # 时间段 PnL
            "today_pnl": round(today_pnl, 2),
            "week_pnl": round(week_pnl, 2),
            "month_pnl": round(month_pnl, 2),
            # 曲线数据
            "pnl_curve": equity_curve,
            # 元数据
            "period_days": 90,
            "last_updated": datetime.now().isoformat(),
            "initial_equity": round(initial_equity, 2),
            # avg_trade_duration: 已删除 (未计算，不在前端展示)
            # 调试信息
            "_debug": {
                "api_key_loaded": bool(self.api_key),
                "wallet_balance": round(wallet_balance, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "available_balance": round(available_balance, 2),
                "total_income_records": len(all_income),
                "income_breakdown": {k: round(v, 4) for k, v in income_by_type.items()},
                "net_pnl_check": round(net_pnl, 4),
                "realized_pnl_only": round(sum(realized_pnl_values), 4) if realized_pnl_values else 0,
            }
        }

    def _load_trading_memory(self) -> list:
        """Load trading_memory.json for enriching trade data with side and pnl%."""
        import json
        from pathlib import Path
        algvex_path = os.environ.get("ALGVEX_PATH", os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
        memory_path = Path(algvex_path) / "data" / "trading_memory.json"
        try:
            if memory_path.exists():
                with open(memory_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Could not load trading_memory.json: {e}")
        return []

    async def get_recent_trades_formatted(self, limit: int = 20) -> list:
        """Get recent trades formatted for timeline display"""
        all_income = await self._get_income_paginated(days=30)
        realized = [r for r in all_income if r.get("incomeType") == "REALIZED_PNL"]
        # 按时间倒序，取最近 limit 条
        realized.sort(key=lambda x: x.get("time", 0), reverse=True)
        realized = realized[:limit]

        # Load trading_memory for real side and pnl% data
        memories = self._load_trading_memory()
        # Build lookup by timestamp (minute precision) for matching
        memory_by_minute: Dict[str, Dict] = {}
        for m in memories:
            ts_str = m.get('timestamp', '')[:16]  # "2026-02-21T14:35"
            if ts_str:
                memory_by_minute[ts_str] = m

        formatted_trades = []
        for record in realized:
            ts = datetime.fromtimestamp(record["time"] / 1000)
            pnl = float(record["income"])

            # Try to match with trading_memory for correct side and pnl%
            side = "LONG" if pnl > 0 else "SHORT"
            pnl_percent = 0.0
            ts_key = ts.strftime("%Y-%m-%dT%H:%M")
            matched = memory_by_minute.get(ts_key)
            # Also check ±1 minute window for timing differences
            if not matched:
                for offset_sec in [60, -60]:
                    alt_key = (ts + timedelta(seconds=offset_sec)).strftime("%Y-%m-%dT%H:%M")
                    matched = memory_by_minute.get(alt_key)
                    if matched:
                        break
            if matched:
                side = matched.get('decision', side)
                pnl_percent = round(matched.get('pnl', 0), 2)

            formatted_trades.append({
                "id": record.get("tranId", ""),
                "symbol": record.get("symbol", "BTCUSDT"),
                "time": ts.isoformat(),
                "time_display": ts.strftime("%m/%d %H:%M"),
                "pnl": round(pnl, 2),
                "pnl_percent": pnl_percent,
                "side": side,
                "is_profit": pnl > 0
            })

        return formatted_trades

    # Legacy compatibility methods
    async def get_trade_history(self, symbol: Optional[str] = None, limit: int = 100) -> list:
        """Legacy: Get trade history from Binance Futures"""
        if not self.api_key or not self.api_secret:
            return []

        all_trades = []
        symbols_to_query = [symbol] if symbol else ["BTCUSDT"]

        try:
            headers = {"X-MBX-APIKEY": self.api_key}
            async with self._get_client() as client:
                for sym in symbols_to_query:
                    params = self._sign_request({"symbol": sym, "limit": limit})
                    response = await client.get(
                        f"{self.base_url}/fapi/v1/userTrades",
                        params=params,
                        headers=headers,
                        timeout=10.0
                    )
                    if response.status_code == 200:
                        all_trades.extend(response.json())

            all_trades.sort(key=lambda x: x.get("time", 0), reverse=True)
            return all_trades[:limit]
        except Exception as e:
            logger.error(f"Error fetching trade history: {e}")
            return []

    async def get_income_history(self, income_type: Optional[str] = None, limit: int = 1000, days: int = 30) -> list:
        """Legacy compatibility wrapper"""
        all_records = await self._get_income_paginated(days=days)
        if income_type:
            all_records = [r for r in all_records if r.get("incomeType") == income_type]
        return all_records[:limit]


# Singleton instance
_performance_service = None

def get_performance_service() -> PerformanceService:
    global _performance_service
    if _performance_service is None:
        _performance_service = PerformanceService()
    return _performance_service
