import { useRouter } from "next/router";
import Head from "next/head";
import { useTranslation, type Locale } from "@/lib/i18n";
import { Header } from "@/components/layout/header";
import { MechanicalScoreCard } from "@/components/mechanical/score-card";
import { MechanicalSignalHistory } from "@/components/mechanical/signal-history";
import { useMechanicalTimeSeries } from "@/hooks/useMechanical";

function ScoreChart() {
  const { series, isLoading } = useMechanicalTimeSeries(48);

  if (isLoading || !series || series.length === 0) {
    return (
      <div className="glass-card rounded-2xl p-5">
        <div className="text-sm font-semibold mb-3">net_raw 时间序列</div>
        <div className="h-40 flex items-center justify-center text-xs text-muted-foreground">图表数据加载中...</div>
      </div>
    );
  }

  // Simple SVG chart
  const width = 800;
  const height = 160;
  const padding = { top: 10, right: 10, bottom: 20, left: 40 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  const values = series.map(s => s.net_raw);
  const minVal = Math.min(-0.5, ...values);
  const maxVal = Math.max(0.5, ...values);
  const range = maxVal - minVal;

  const toX = (i: number) => padding.left + (i / (series.length - 1)) * chartW;
  const toY = (v: number) => padding.top + (1 - (v - minVal) / range) * chartH;

  const pathD = series.map((s, i) => `${i === 0 ? "M" : "L"} ${toX(i).toFixed(1)} ${toY(s.net_raw).toFixed(1)}`).join(" ");
  const zeroY = toY(0);

  // Area fill
  const areaD = pathD + ` L ${toX(series.length - 1).toFixed(1)} ${zeroY.toFixed(1)} L ${toX(0).toFixed(1)} ${zeroY.toFixed(1)} Z`;

  return (
    <div className="glass-card rounded-2xl p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-semibold">net_raw 时间序列 (48h)</span>
        <span className="text-[10px] text-muted-foreground">{series.length} 个数据点</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto">
        {/* Zero line */}
        <line x1={padding.left} y1={zeroY} x2={width - padding.right} y2={zeroY}
          stroke="hsl(215 20% 30%)" strokeWidth="1" strokeDasharray="4,4" />

        {/* Threshold lines */}
        {[0.20, 0.35, 0.45, -0.20, -0.35, -0.45].map(t => (
          <line key={t} x1={padding.left} y1={toY(t)} x2={width - padding.right} y2={toY(t)}
            stroke={Math.abs(t) >= 0.45 ? "hsl(245 85% 62% / 0.3)" : "hsl(215 20% 25% / 0.5)"}
            strokeWidth="0.5" strokeDasharray="2,4" />
        ))}

        {/* Area fill */}
        <path d={areaD} fill="url(#netRawGrad)" opacity="0.3" />

        {/* Line */}
        <path d={pathD} fill="none" stroke="hsl(217 91% 60%)" strokeWidth="1.5" />

        {/* Gradient def */}
        <defs>
          <linearGradient id="netRawGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="hsl(158 64% 52%)" />
            <stop offset="50%" stopColor="transparent" />
            <stop offset="100%" stopColor="hsl(0 84% 60%)" />
          </linearGradient>
        </defs>

        {/* Y-axis labels */}
        {[-0.4, -0.2, 0, 0.2, 0.4].map(v => (
          <text key={v} x={padding.left - 4} y={toY(v)} textAnchor="end"
            className="text-[8px] fill-slate-500 font-mono" dominantBaseline="middle">
            {v >= 0 ? "+" : ""}{v.toFixed(1)}
          </text>
        ))}

        {/* Latest value dot */}
        {series.length > 0 && (
          <circle cx={toX(series.length - 1)} cy={toY(series[series.length - 1].net_raw)}
            r="3" fill="hsl(217 91% 60%)" stroke="hsl(222 47% 7%)" strokeWidth="1.5" />
        )}
      </svg>
    </div>
  );
}

export default function MechanicalPage() {
  const router = useRouter();
  const locale = (router.locale || "en") as Locale;
  const { t } = useTranslation(locale);

  return (
    <>
      <Head>
        <title>Prism | AlgVex</title>
      </Head>
      <div className="min-h-screen gradient-bg">
        <Header locale={locale} t={t} />
        <main className="max-w-7xl mx-auto px-4 pt-24 pb-16">
          {/* Page header */}
          <div className="mb-8">
            <h1 className="text-2xl font-bold">
              <span className="gradient-text">Prism</span> 评分引擎
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              3 维预判评分：Structure / Divergence / Order Flow
            </p>
          </div>

          {/* Main grid */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Left: Score card */}
            <div className="lg:col-span-1">
              <MechanicalScoreCard />
            </div>

            {/* Right: Chart + History */}
            <div className="lg:col-span-2 space-y-6">
              <ScoreChart />
              <MechanicalSignalHistory limit={30} />
            </div>
          </div>
        </main>
      </div>
    </>
  );
}
