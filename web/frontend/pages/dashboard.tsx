"use client";

import Head from "next/head";
import { useRouter } from "next/router";
import useSWR from "swr";
import dynamic from "next/dynamic";
import {
  Activity, Zap, Shield, Clock, TrendingUp, Layers,
  AlertTriangle, Target, BarChart3,
} from "lucide-react";

import { Header } from "@/components/layout/header";
import { Footer } from "@/components/layout/footer";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { useTranslation, type Locale } from "@/lib/i18n";

// Loading skeletons
function ChartSkeleton() {
  return (
    <div className="h-64 bg-muted/30 rounded-lg animate-pulse flex items-center justify-center">
      <BarChart3 className="h-8 w-8 text-muted-foreground/50" />
    </div>
  );
}
function ListSkeleton() {
  return (
    <div className="space-y-3">
      {[1, 2, 3].map((i) => (
        <div key={i} className="h-16 bg-muted/30 rounded-lg animate-pulse" />
      ))}
    </div>
  );
}
function CardSkeleton() {
  return <div className="h-32 bg-muted/30 rounded-lg animate-pulse" />;
}

// Dynamic imports (SSR disabled for animated components)
const BotStatus = dynamic(
  () => import("@/components/trading/bot-status").then((mod) => mod.BotStatus),
  { ssr: false }
);
const MarketIntelligenceBar = dynamic(
  () => import("@/components/trading/market-intelligence-bar").then((mod) => mod.MarketIntelligenceBar),
  { ssr: false }
);
const PerformanceStats = dynamic(
  () => import("@/components/trading/stats-cards").then((mod) => mod.PerformanceStats),
  { ssr: false }
);
const MechanicalScoreCard = dynamic(
  () => import("@/components/mechanical/score-card").then((mod) => mod.MechanicalScoreCard),
  { ssr: false, loading: () => <CardSkeleton /> }
);
const MechanicalSignalHistory = dynamic(
  () => import("@/components/mechanical/signal-history").then((mod) => mod.MechanicalSignalHistory),
  { ssr: false, loading: () => <ListSkeleton /> }
);
const RiskMetrics = dynamic(
  () => import("@/components/trading/risk-metrics").then((mod) => mod.RiskMetrics),
  { ssr: false }
);
const TradeTimeline = dynamic(
  () => import("@/components/trading/trade-timeline").then((mod) => mod.TradeTimeline),
  { ssr: false }
);

// Admin components (moved from admin dashboard)
const EquityCurve = dynamic(
  () => import("@/components/admin/equity-curve").then((mod) => mod.EquityCurve),
  { ssr: false, loading: () => <ChartSkeleton /> }
);
const LayerOrders = dynamic(
  () => import("@/components/admin/layer-orders").then((mod) => mod.LayerOrders),
  { ssr: false, loading: () => <ListSkeleton /> }
);
// v49.0: AIDecisionDetail removed — replaced by MechanicalScoreCard
const SafetyEvents = dynamic(
  () => import("@/components/admin/safety-events").then((mod) => mod.SafetyEvents),
  { ssr: false, loading: () => <ListSkeleton /> }
);
const SLTPAdjustments = dynamic(
  () => import("@/components/admin/sltp-adjustments").then((mod) => mod.SLTPAdjustments),
  { ssr: false, loading: () => <ListSkeleton /> }
);

export default function DashboardPage() {
  const router = useRouter();
  const locale = (router.locale || "en") as Locale;
  const { t } = useTranslation(locale);

  // Bot status
  const { data: systemStatus } = useSWR("/api/public/system-status", {
    refreshInterval: 15000,
  });

  // Performance stats (now includes equity_history + risk_metrics)
  const { data: perfStats, error: perfError } = useSWR(
    "/api/public/performance",
    { refreshInterval: 60000 }
  );

  // Recent trades
  const { data: recentTrades } = useSWR("/api/public/trades/recent?limit=10", {
    refreshInterval: 60000,
  });

  // Layer orders
  const { data: layerOrdersData } = useSWR("/api/public/layer-orders", {
    refreshInterval: 10000,
  });

  // Safety events
  const { data: safetyEventsData } = useSWR("/api/public/safety-events", {
    refreshInterval: 15000,
  });

  // SL/TP adjustments
  const { data: sltpAdjustments } = useSWR("/api/public/sltp-adjustments", {
    refreshInterval: 15000,
  });

  const isPerfLoading = !perfStats && !perfError;
  const perfData = perfStats?.data || perfStats;

  // Map system status to bot status type
  const botStatus: "running" | "paused" | "stopped" | "error" =
    systemStatus?.trading_active
      ? systemStatus?.is_paused
        ? "paused"
        : "running"
      : "stopped";

  // Build performance stats for PerformanceStats component
  const statsData = {
    total_pnl: perfData?.total_pnl || 0,
    today_pnl: perfData?.today_pnl || 0,
    week_pnl: perfData?.week_pnl || 0,
    month_pnl: perfData?.month_pnl || 0,
    win_rate: perfData?.win_rate || 0,
    total_trades: perfData?.total_trades || 0,
  };

  // Build risk metrics data
  const riskData = {
    max_drawdown: perfData?.max_drawdown || 0,
    max_drawdown_percent: perfData?.max_drawdown_percent || 0,
    sharpe_ratio: perfData?.sharpe_ratio || 0,
    profit_factor: perfData?.profit_factor || 0,
    win_rate: perfData?.win_rate || 0,
    avg_win: perfData?.avg_win || 0,
    avg_loss: perfData?.avg_loss || 0,
    best_trade: perfData?.best_trade || 0,
    worst_trade: perfData?.worst_trade || 0,
  };

  // Safely extract array from API response
  const toArray = (data: any, key?: string): any[] => {
    if (!data) return [];
    if (key && Array.isArray(data[key])) return data[key];
    if (Array.isArray(data)) return data;
    return [];
  };

  // Build trade timeline data
  const tradesRaw = recentTrades?.data || recentTrades;
  const trades = toArray(tradesRaw, 'trades').map(
    (t: any, i: number) => ({
      id: t.id || `trade-${i}`,
      symbol: t.symbol || "BTCUSDT",
      time: t.time || t.timestamp || new Date().toISOString(),
      time_display:
        t.time_display ||
        new Date(t.time || t.timestamp || "").toLocaleString(),
      pnl: t.pnl || t.realized_pnl || 0,
      pnl_percent: t.pnl_percent || 0,
      side: t.side || "LONG",
      is_profit: (t.pnl || t.realized_pnl || 0) >= 0,
    })
  );

  return (
    <>
      <Head>
        <title>
          Dashboard - AlgVex {locale === "zh" ? "实时监控" : "Live Monitor"}
        </title>
        <meta
          name="description"
          content="实时交易机器人监控面板"
        />
      </Head>

      <div className="min-h-screen gradient-bg noise-overlay">
        <Header locale={locale} t={t} />

        <main className="pt-24 sm:pt-28 pb-12 px-4">
          <div className="container mx-auto max-w-7xl space-y-6">
            {/* Page Title */}
            <div className="flex items-center justify-between">
              <div>
                <h1 className="text-2xl sm:text-3xl font-bold">
                  {locale === "zh" ? "实时监控" : "Live Dashboard"}
                </h1>
                <p className="text-sm text-muted-foreground mt-1">
                  {locale === "zh"
                    ? "双策略实时状态 — Prism + SRP"
                    : "Dual strategy live monitor — Prism + SRP"}
                </p>
              </div>
            </div>

            {/* Row 1: Bot Status + Market Intelligence */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <ErrorBoundary>
                <BotStatus
                  status={botStatus}
                  lastTradeTime={
                    systemStatus?.last_trade_time || perfStats?.last_trade_time
                  }
                  nextAnalysisTime={systemStatus?.next_analysis_time}
                  timerIntervalSec={systemStatus?.timer_interval_sec || 900}
                />
              </ErrorBoundary>

              <Card className="lg:col-span-2 border-border/50">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Activity className="h-4 w-4 text-primary" />
                    {locale === "zh" ? "市场数据" : "Market Intelligence"}
                  </CardTitle>
                </CardHeader>
                <CardContent className="pb-3">
                  <ErrorBoundary>
                    <MarketIntelligenceBar />
                  </ErrorBoundary>
                </CardContent>
              </Card>
            </div>

            {/* Row 2: Performance Stats */}
            <Card className="border-border/50">
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Clock className="h-4 w-4 text-primary" />
                  {locale === "zh" ? "业绩概览" : "Performance Overview"}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ErrorBoundary>
                  <PerformanceStats stats={statsData} loading={isPerfLoading} />
                </ErrorBoundary>
              </CardContent>
            </Card>

            {/* Row 3: Equity Curve + Risk Metrics */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <Card className="border-border/50">
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <TrendingUp className="h-4 w-4 text-primary" />
                    {locale === "zh" ? "权益曲线" : "Equity Curve"}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ErrorBoundary>
                    <EquityCurve data={perfData?.equity_history} />
                  </ErrorBoundary>
                </CardContent>
              </Card>

              {perfData && (
                <Card className="border-border/50">
                  <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Shield className="h-4 w-4 text-primary" />
                      {locale === "zh" ? "风险指标" : "Risk Metrics"}
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <ErrorBoundary>
                      <RiskMetrics data={riskData} />
                    </ErrorBoundary>
                  </CardContent>
                </Card>
              )}
            </div>

            {/* Row 4: Mechanical Score + Layer Orders */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <ErrorBoundary>
                <MechanicalScoreCard />
              </ErrorBoundary>

              <Card className="border-border/50">
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Layers className="h-4 w-4 text-primary" />
                    {locale === "zh" ? "层级订单" : "Layer Orders"}
                    {layerOrdersData?.count > 0 && (
                      <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">
                        {layerOrdersData.count}
                      </span>
                    )}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ErrorBoundary>
                    <LayerOrders
                      layers={layerOrdersData?.layers}
                      count={layerOrdersData?.count}
                    />
                  </ErrorBoundary>
                </CardContent>
              </Card>
            </div>

            {/* Row 5: Safety Events (only show if events exist) */}
            {safetyEventsData?.count > 0 && (
              <Card className="border-red-500/30 bg-red-500/5">
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base text-red-500">
                    <AlertTriangle className="h-4 w-4" />
                    {locale === "zh" ? "安全事件" : "Safety Events"}
                    <span className="text-xs bg-red-500/10 text-red-500 px-2 py-0.5 rounded-full">
                      {safetyEventsData.count}
                    </span>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ErrorBoundary>
                    <SafetyEvents
                      events={safetyEventsData?.events}
                      count={safetyEventsData?.count}
                    />
                  </ErrorBoundary>
                </CardContent>
              </Card>
            )}

            {/* Row 6: Mechanical Signal History + Trade Timeline */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <ErrorBoundary>
                <MechanicalSignalHistory limit={10} />
              </ErrorBoundary>

              <Card className="border-border/50">
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Activity className="h-4 w-4 text-primary" />
                    {locale === "zh" ? "交易时间线" : "Trade Timeline"}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ErrorBoundary>
                    <TradeTimeline trades={trades} maxItems={10} />
                  </ErrorBoundary>
                </CardContent>
              </Card>
            </div>

            {/* Row 7: SL/TP Adjustments (only show if exists) */}
            {sltpAdjustments?.count > 0 && (
              <Card className="border-border/50">
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Target className="h-4 w-4 text-primary" />
                    {locale === "zh" ? "SL/TP 调整" : "SL/TP Adjustments"}
                    <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded-full">
                      {sltpAdjustments.count}
                    </span>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ErrorBoundary>
                    <SLTPAdjustments
                      adjustments={sltpAdjustments?.adjustments}
                      count={sltpAdjustments?.count}
                    />
                  </ErrorBoundary>
                </CardContent>
              </Card>
            )}
          </div>
        </main>

        <Footer t={t} />
      </div>
    </>
  );
}
