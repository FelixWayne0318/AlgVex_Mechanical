"""
SRP Strategy API Routes v5.0

Public (read-only): parameters, state, cached results, service status
Admin (write): run backtest, run parity check
"""
from fastapi import APIRouter, Query

from services.srp_service import get_srp_service

# Public routes (no auth)
router = APIRouter(prefix="/public/srp", tags=["SRP Strategy"])


@router.get("/parameters")
async def get_srp_parameters():
    """Current SRP v5.0 parameters from configs/base.yaml."""
    return get_srp_service().get_parameters()


@router.get("/state")
async def get_srp_state():
    """Current position state (v5.0: virtual DCA, TP target, deal base)."""
    return get_srp_service().get_state()


@router.get("/service-status")
async def get_service_status():
    """NT nautilus-srp service status (active/inactive/failed)."""
    return get_srp_service().get_service_status()


@router.get("/backtest")
async def get_backtest_result():
    """Latest cached backtest result (NT BacktestEngine)."""
    return get_srp_service().get_backtest_result()


@router.get("/walkforward")
async def get_walkforward_result():
    """Latest walk-forward result (CLI-generated, read-only)."""
    return get_srp_service().get_walkforward_result()


# Admin routes (require auth)
admin_router = APIRouter(prefix="/admin/srp", tags=["SRP Strategy (Admin)"])


@admin_router.post("/backtest/run")
async def run_backtest(
    days: int = Query(default=456, ge=7, le=456),
    balance: float = Query(default=1500, ge=100, le=100000),
    srp_pct: float | None = Query(default=None, ge=0.1, le=10.0),
    dca_spacing: float | None = Query(default=None, ge=0.5, le=20.0),
    dca_mult: float | None = Query(default=None, ge=1.0, le=5.0),
    max_dca_count: int | None = Query(default=None, ge=0, le=10),
    tp_pct: float | None = Query(default=None, ge=0.5, le=20.0),
    sl_pct: float | None = Query(default=None, ge=1.0, le=30.0),
    include_equity_curve: bool = Query(default=False),
    include_trades: bool = Query(default=False),
):
    """Run NT BacktestEngine backtest (~30-60s). Returns full NT report."""
    return get_srp_service().run_backtest(
        days=days, balance=balance,
        srp_pct=srp_pct, dca_spacing=dca_spacing, dca_mult=dca_mult,
        max_dca_count=max_dca_count, tp_pct=tp_pct, sl_pct=sl_pct,
        include_equity_curve=include_equity_curve,
        include_trades=include_trades,
    )


@admin_router.post("/parity/run")
async def run_parity_check(
    days: int = Query(default=456, ge=7, le=456),
):
    """Run Pine Simulator vs Python Backtest parity check (~30s)."""
    return get_srp_service().run_parity_check(days=days)
