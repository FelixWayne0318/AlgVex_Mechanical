"use client";

import { useState } from "react";
import { useRouter } from "next/router";
import Head from "next/head";
import dynamic from "next/dynamic";
import useSWR from "swr";

import { Header } from "@/components/layout/header";
import { Footer } from "@/components/layout/footer";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatsCard } from "@/components/stats-card";
import { PnLChart } from "@/components/charts/pnl-chart";
import { useTranslation, type Locale } from "@/lib/i18n";
import { formatPercent, formatTimeAgo } from "@/lib/utils";
import { TradeTable } from "@/components/trade-evaluation/TradeTable";
import { BarChart3 } from "lucide-react";

const PerformanceAttribution = dynamic(
  () => import("@/components/admin/performance-attribution").then((mod) => mod.PerformanceAttribution),
  { ssr: false, loading: () => <div className="h-32 bg-muted/30 rounded-lg animate-pulse" /> }
);

const periodOptions = [
  { days: 30, label: "30D" },
  { days: 90, label: "90D" },
  { days: 180, label: "180D" },
  { days: 365, label: "1Y" },
];

export default function PerformancePage() {
  const router = useRouter();
  const locale = (router.locale || "en") as Locale;
  const { t } = useTranslation(locale);
  const [selectedPeriod, setSelectedPeriod] = useState(30);

  const { data: performance, error } = useSWR(
    `/api/public/performance?days=${selectedPeriod}`,
    { refreshInterval: 60000 }
  );

  const { data: attributionData } = useSWR(
    "/api/public/trade-evaluation/attribution",
    { refreshInterval: 120000 }
  );

  const isLoading = !performance && !error;

  return (
    <>
      <Head>
        <title>业绩分析 - AlgVex</title>
        <meta
          name="description"
          content="来自币安 Futures 的实时交易数据"
        />
      </Head>

      <div className="min-h-screen gradient-bg">
        <Header locale={locale} t={t} />

        {/* pt-24 accounts for floating rounded header with extra spacing */}
        <main className="pt-24 pb-16 px-4">
          <div className="container mx-auto">
            {/* Page Header */}
            <div className="mb-8">
              <h1 className="text-4xl font-bold mb-2">{t("perf.title")}</h1>
              <p className="text-muted-foreground">{t("perf.subtitle")}</p>
            </div>

            {/* Period Selector */}
            <div className="flex items-center gap-2 mb-8">
              <span className="text-muted-foreground mr-2">{t("perf.period")}:</span>
              {periodOptions.map((option) => (
                <Button
                  key={option.days}
                  variant={selectedPeriod === option.days ? "default" : "outline"}
                  size="sm"
                  onClick={() => setSelectedPeriod(option.days)}
                >
                  {option.label}
                </Button>
              ))}
            </div>

            {/* Stats Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
              <StatsCard
                title={t("stats.totalReturn")}
                value={
                  isLoading
                    ? "..."
                    : formatPercent(performance?.total_pnl_percent || 0)
                }
                subtitle={
                  performance?.total_pnl
                    ? `$${performance.total_pnl.toLocaleString()}`
                    : undefined
                }
                type={
                  (performance?.total_pnl_percent || 0) >= 0 ? "profit" : "loss"
                }
                icon="trending"
              />
              <StatsCard
                title={t("stats.winRate")}
                value={isLoading ? "..." : `${performance?.win_rate || 0}%`}
                subtitle={
                  performance
                    ? `${performance.winning_trades}W / ${performance.losing_trades}L`
                    : undefined
                }
                type="neutral"
                icon="target"
              />
              <StatsCard
                title={t("stats.maxDrawdown")}
                value={
                  isLoading
                    ? "..."
                    : `-${performance?.max_drawdown_percent || 0}%`
                }
                subtitle={
                  performance?.max_drawdown
                    ? `-$${performance.max_drawdown.toLocaleString()}`
                    : undefined
                }
                type="loss"
                icon="alert"
              />
              <StatsCard
                title={t("stats.totalTrades")}
                value={isLoading ? "..." : performance?.total_trades || 0}
                type="neutral"
                icon="activity"
              />
            </div>

            {/* Chart */}
            <Card className="border-border/50 mb-8">
              <CardHeader>
                <CardTitle>{t("perf.pnlCurve")}</CardTitle>
              </CardHeader>
              <CardContent>
                {performance?.pnl_curve ? (
                  <PnLChart data={performance.pnl_curve} />
                ) : (
                  <div className="h-[400px] flex items-center justify-center text-muted-foreground">
                    {isLoading ? t("common.loading") : t("common.error")}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Performance Attribution */}
            <Card className="border-border/50 mb-8">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <BarChart3 className="h-5 w-5" />
                  业绩归因
                </CardTitle>
              </CardHeader>
              <CardContent>
                <PerformanceAttribution data={attributionData} />
              </CardContent>
            </Card>

            {/* Trade Evaluation Table */}
            <div className="mb-8">
              <TradeTable limit={20} />
            </div>

            {/* Last Updated */}
            {performance?.last_updated && (
              <p className="text-sm text-muted-foreground text-center">
                {t("common.lastUpdated")}: {formatTimeAgo(performance.last_updated)}
              </p>
            )}
          </div>
        </main>

        <Footer t={t} />
      </div>
    </>
  );
}
