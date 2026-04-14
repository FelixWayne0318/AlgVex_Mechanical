"use client";

import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import useSWR from "swr";
import dynamic from "next/dynamic";
import {
  ArrowRight,
  TrendingUp,
  TrendingDown,
  Shield,
  Zap,
  Bot,
  Activity,
  Target,
  AlertTriangle,
  BarChart3,
  Cpu,
  Globe,
  ChevronRight,
  Users,
} from "lucide-react";

import { Header } from "@/components/layout/header";
import { Footer } from "@/components/layout/footer";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useTranslation, type Locale } from "@/lib/i18n";
import { formatPnL, formatPercent } from "@/lib/utils";
import { TradeQualityCard } from "@/components/trade-evaluation/TradeQualityCard";

// Dynamic import with SSR disabled
const HeroAnimatedCandlestick = dynamic(
  () => import("@/components/charts/animated-candlestick").then(mod => mod.HeroAnimatedCandlestick),
  {
    ssr: false,
    loading: () => (
      <div className="relative w-full">
        <div className="h-[360px] flex items-center justify-center">
          <div className="flex items-center gap-2 text-muted-foreground">
            <div className="w-2 h-2 rounded-full bg-primary animate-pulse" />
            <span className="text-sm">图表加载中...</span>
          </div>
        </div>
      </div>
    )
  }
);

// Animated value component
function AnimatedValue({
  value,
  isLoading,
  className = "",
}: {
  value: string | number;
  isLoading: boolean;
  className?: string;
}) {
  if (isLoading) {
    return <span className="shimmer inline-block w-16 sm:w-20 h-6 sm:h-8 rounded" />;
  }
  return <span className={`number-animate ${className}`}>{value}</span>;
}

// Stats card component - responsive
function StatsCard({
  title,
  value,
  subtitle,
  icon: Icon,
  type = "neutral",
  isLoading = false,
}: {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: any;
  type?: "profit" | "loss" | "neutral";
  isLoading?: boolean;
}) {
  const colorClass =
    type === "profit"
      ? "text-[hsl(var(--profit))]"
      : type === "loss"
      ? "text-[hsl(var(--loss))]"
      : "text-foreground";

  const bgClass =
    type === "profit"
      ? "bg-[hsl(var(--profit))]/10"
      : type === "loss"
      ? "bg-[hsl(var(--loss))]/10"
      : "bg-primary/10";

  return (
    <Card className="stat-card border-border/50">
      <CardContent className="p-4 sm:p-6">
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1 sm:space-y-2 min-w-0 flex-1">
            <p className="text-xs sm:text-sm text-muted-foreground truncate">{title}</p>
            <div className="flex items-baseline gap-2">
              <AnimatedValue
                value={value}
                isLoading={isLoading}
                className={`text-xl sm:text-2xl lg:text-3xl font-bold ${colorClass}`}
              />
            </div>
            {subtitle && (
              <p className="text-[10px] sm:text-xs text-muted-foreground">{subtitle}</p>
            )}
          </div>
          <div className={`p-2 sm:p-3 rounded-lg sm:rounded-xl flex-shrink-0 ${bgClass}`}>
            <Icon
              className={`h-4 w-4 sm:h-5 sm:w-5 lg:h-6 lg:w-6 ${
                type === "profit"
                  ? "text-[hsl(var(--profit))]"
                  : type === "loss"
                  ? "text-[hsl(var(--loss))]"
                  : "text-primary"
              }`}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// Mini PnL Chart
function MiniPnLChart({ data }: { data: Array<{ cumulative_pnl: number }> }) {
  if (!data || data.length < 2) return null;

  const values = data.map((d) => d.cumulative_pnl);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const width = 100;
  const height = 40;
  const padding = 2;

  const points = values
    .map((v, i) => {
      const x = padding + (i / (values.length - 1)) * (width - 2 * padding);
      const y = height - padding - ((v - min) / range) * (height - 2 * padding);
      return `${x},${y}`;
    })
    .join(" ");

  const isPositive = values[values.length - 1] >= values[0];

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-8 sm:h-10">
      <defs>
        <linearGradient id="lineGradient" x1="0" y1="0" x2="0" y2="1">
          <stop
            offset="0%"
            stopColor={isPositive ? "hsl(var(--profit))" : "hsl(var(--loss))"}
            stopOpacity="0.3"
          />
          <stop
            offset="100%"
            stopColor={isPositive ? "hsl(var(--profit))" : "hsl(var(--loss))"}
            stopOpacity="0"
          />
        </linearGradient>
      </defs>
      <polygon
        points={`${padding},${height - padding} ${points} ${width - padding},${height - padding}`}
        fill="url(#lineGradient)"
      />
      <polyline
        points={points}
        fill="none"
        stroke={isPositive ? "hsl(var(--profit))" : "hsl(var(--loss))"}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// Feature card - responsive
function FeatureCard({
  icon: Icon,
  title,
  description,
}: {
  icon: any;
  title: string;
  description: string;
}) {
  return (
    <Card className="feature-card border-border/50 group hover:border-primary/30 transition-all duration-300">
      <CardContent className="p-5 sm:p-6 lg:p-8">
        <div className="icon-wrapper w-10 h-10 sm:w-12 sm:h-12 lg:w-14 lg:h-14 mb-4 sm:mb-5 lg:mb-6 rounded-xl sm:rounded-2xl bg-gradient-to-br from-primary/20 to-accent/10 flex items-center justify-center">
          <Icon className="h-5 w-5 sm:h-6 sm:w-6 lg:h-7 lg:w-7 text-primary" />
        </div>
        <h3 className="text-base sm:text-lg lg:text-xl font-semibold mb-2 sm:mb-3 group-hover:text-primary transition-colors">
          {title}
        </h3>
        <p className="text-sm sm:text-base text-muted-foreground leading-relaxed">{description}</p>
      </CardContent>
    </Card>
  );
}

export default function HomePage() {
  const router = useRouter();
  const locale = (router.locale || "en") as Locale;
  const { t } = useTranslation(locale);

  const { data: performance, error: perfError } = useSWR(
    "/api/public/performance?days=30",
    { refreshInterval: 60000 }
  );

  const isLoading = !performance && !perfError;
  const pnlType = (performance?.total_pnl || 0) >= 0 ? "profit" : ("loss" as const);

  return (
    <>
      <Head>
        <title>AlgVex - 算法驱动 Crypto Trading</title>
        <meta
          name="description"
          content="双策略算法交易：Prism 预判评分 + SRP 均值回归"
        />
        <link rel="icon" href="/favicon.ico" />
      </Head>

      <div className="min-h-screen gradient-bg noise-overlay">
        <Header locale={locale} t={t} />

        {/* Hero Section - pt-24 accounts for floating rounded header with extra spacing */}
        <section className="relative pt-24 sm:pt-28 lg:pt-32 pb-12 sm:pb-16 lg:pb-24 px-4 overflow-hidden">
          {/* Background effects */}
          <div className="absolute inset-0 grid-pattern opacity-30" />
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] sm:w-[600px] lg:w-[800px] h-[400px] sm:h-[600px] lg:h-[800px] bg-primary/5 rounded-full blur-3xl" />

          <div className="container mx-auto relative z-10">
            <div className="max-w-4xl mx-auto text-center">
              {/* Main Title */}
              <h1 className="text-3xl sm:text-5xl lg:text-7xl font-bold mb-4 sm:mb-6 leading-tight">
                <span className="text-primary text-glow">{t("hero.title")}</span>
                <br />
                <span className="bg-gradient-to-r from-foreground to-muted-foreground bg-clip-text text-transparent">
                  {t("hero.title2")}
                </span>
              </h1>

              <p className="text-base sm:text-lg lg:text-xl text-muted-foreground max-w-2xl mx-auto mb-8 sm:mb-10 lg:mb-12 leading-relaxed px-2">
                {t("hero.subtitle")}
              </p>

              {/* CTA Buttons - Same fixed width for both buttons */}
              <div className="flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-4 mb-10 sm:mb-12 lg:mb-16">
                <Link href="/copy" className="w-full sm:w-[260px]">
                  <Button size="lg" className="text-base sm:text-lg h-12 sm:h-14 w-full bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70 shadow-lg shadow-primary/25 border border-primary/20 justify-center">
                    <Users className="mr-2 h-4 w-4 sm:h-5 sm:w-5 flex-shrink-0" />
                    <span>{t("hero.cta")}</span>
                  </Button>
                </Link>
                <Link href="/performance" className="w-full sm:w-[260px]">
                  <Button size="lg" className="text-base sm:text-lg h-12 sm:h-14 w-full bg-background/60 backdrop-blur-xl border border-border/50 hover:bg-background/80 hover:border-primary/30 text-foreground justify-center">
                    <BarChart3 className="mr-2 h-4 w-4 sm:h-5 sm:w-5 flex-shrink-0" />
                    <span>{t("hero.stats")}</span>
                  </Button>
                </Link>
              </div>

              {/* Animated Candlestick Chart */}
              <HeroAnimatedCandlestick symbol="BTCUSDT" interval="30m" />
            </div>
          </div>
        </section>

        {/* Live Stats Section */}
        <section className="py-10 sm:py-12 lg:py-16 px-4 relative">
          <div className="container mx-auto">
            <div className="flex items-center justify-between mb-6 sm:mb-8">
              <div>
                <h2 className="text-xl sm:text-2xl font-bold">实时业绩</h2>
                <p className="text-sm text-muted-foreground">近 30 天</p>
              </div>
              <Link
                href="/performance"
                className="flex items-center gap-1 text-xs sm:text-sm text-primary hover:underline"
              >
                查看详情 <ChevronRight className="h-3 w-3 sm:h-4 sm:w-4" />
              </Link>
            </div>

            {/* Stats Grid - 2x2 on mobile, 4 columns on larger screens */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 lg:gap-6">
              <StatsCard
                title="总收益"
                value={isLoading ? "..." : formatPnL(performance?.total_pnl || 0)}
                subtitle={performance?.total_pnl_percent ? formatPercent(performance.total_pnl_percent) : undefined}
                icon={pnlType === "profit" ? TrendingUp : TrendingDown}
                type={pnlType}
                isLoading={isLoading}
              />
              <StatsCard
                title="胜率"
                value={isLoading ? "..." : `${performance?.win_rate || 0}%`}
                subtitle={`${performance?.winning_trades || 0}W / ${performance?.losing_trades || 0}L`}
                icon={Target}
                type="neutral"
                isLoading={isLoading}
              />
              <StatsCard
                title="最大回撤"
                value={isLoading ? "..." : `-${performance?.max_drawdown_percent || 0}%`}
                subtitle="峰谷回撤"
                icon={AlertTriangle}
                type="loss"
                isLoading={isLoading}
              />
              <StatsCard
                title="总交易"
                value={isLoading ? "..." : performance?.total_trades || 0}
                subtitle="已执行订单"
                icon={Activity}
                type="neutral"
                isLoading={isLoading}
              />
            </div>

            {/* Mini Chart */}
            {performance?.pnl_curve && performance.pnl_curve.length > 0 && (
              <Card className="mt-6 sm:mt-8 border-border/50">
                <CardContent className="p-4 sm:p-6">
                  <div className="flex items-center justify-between mb-3 sm:mb-4">
                    <div>
                      <h3 className="text-sm sm:text-base font-semibold">累计盈亏</h3>
                      <p className="text-xs sm:text-sm text-muted-foreground">30 天权益曲线</p>
                    </div>
                    <div className="text-right">
                      <p className={`text-lg sm:text-2xl font-bold ${
                        (performance?.total_pnl || 0) >= 0 ? "text-[hsl(var(--profit))]" : "text-[hsl(var(--loss))]"
                      }`}>
                        {formatPnL(performance?.total_pnl || 0)}
                      </p>
                    </div>
                  </div>
                  <div className="h-20 sm:h-32">
                    <MiniPnLChart data={performance.pnl_curve} />
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Trade Quality Card */}
            <div className="mt-6 sm:mt-8">
              <TradeQualityCard days={30} />
            </div>
          </div>
        </section>

        {/* Features Section */}
        <section className="py-12 sm:py-16 lg:py-20 px-4">
          <div className="container mx-auto">
            <div className="text-center mb-10 sm:mb-12 lg:mb-16">
              <h2 className="text-2xl sm:text-3xl lg:text-4xl font-bold mb-3 sm:mb-4">
                为什么选择 AlgVex？
              </h2>
              <p className="text-sm sm:text-base lg:text-lg text-muted-foreground max-w-2xl mx-auto">
                基于 141 features 预判评分引擎和机构级风控体系
              </p>
            </div>

            {/* Features Grid - 1 column on mobile, 2 on tablet, 3 on desktop */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6 lg:gap-8">
              <FeatureCard
                icon={Bot}
                title="3 维预判评分"
                description="Structure / Divergence / Order Flow 三维度独立评分，net_raw 阈值决策，零延迟。"
              />
              <FeatureCard
                icon={Shield}
                title="DCA 风控"
                description="固定 SL/TP + DCA 分层入场 + Trailing Stop，R/R=0.8:1 正期望。"
              />
              <FeatureCard
                icon={BarChart3}
                title="多时间框架"
                description="三层架构：1D 趋势层、4H 决策层、30M 执行层，确保入场时机精准。"
              />
              <FeatureCard
                icon={Zap}
                title="24/7 自动交易"
                description="全天候监控市场，自动执行交易，不错过任何机会。"
              />
              <FeatureCard
                icon={Cpu}
                title="订单流分析"
                description="CVD-Price 交叉、Taker Buy Ratio、Volume Climax 检测机构级资金流动。"
              />
              <FeatureCard
                icon={Globe}
                title="双策略架构"
                description="Prism 预判评分 + SRP 均值回归，两个独立账户互不干扰。"
              />
            </div>
          </div>
        </section>

        {/* CTA Section */}
        <section className="py-12 sm:py-16 lg:py-20 px-4">
          <div className="container mx-auto">
            <Card className="border-border/50 overflow-hidden relative">
              <div className="absolute inset-0 mesh-gradient opacity-50" />
              <CardContent className="p-8 sm:p-12 lg:p-16 relative z-10 text-center">
                <h2 className="text-2xl sm:text-3xl lg:text-4xl font-bold mb-3 sm:mb-4">
                  准备开始？
                </h2>
                <p className="text-sm sm:text-base lg:text-lg text-muted-foreground mb-6 sm:mb-8 max-w-xl mx-auto">
                  连接交易所账户，让算法自动执行交易策略。
                </p>
                <Link href="/copy">
                  <Button size="lg" className="glow-primary text-base sm:text-lg px-8 sm:px-10 h-12 sm:h-14">
                    立即开始
                    <ArrowRight className="ml-2 h-4 w-4 sm:h-5 sm:w-5" />
                  </Button>
                </Link>
              </CardContent>
            </Card>
          </div>
        </section>

        <Footer t={t} />
      </div>
    </>
  );
}
