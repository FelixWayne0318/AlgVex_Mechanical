'use client';

import { motion } from 'framer-motion';

interface RiskMetricsData {
  max_drawdown: number;
  max_drawdown_percent: number;
  sharpe_ratio: number;
  profit_factor: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  best_trade: number;
  worst_trade: number;
}

interface RiskMetricsProps {
  data: RiskMetricsData;
}

function MetricCard({
  label,
  value,
  subValue,
  icon,
  color = 'primary',
  index = 0,
}: {
  label: string;
  value: string | number;
  subValue?: string;
  icon: React.ReactNode;
  color?: 'primary' | 'profit' | 'loss' | 'warning';
  index?: number;
}) {
  const colorClasses = {
    primary: 'from-primary/20 to-transparent border-primary/20 text-primary',
    profit: 'from-[hsl(var(--profit))]/20 to-transparent border-[hsl(var(--profit))]/20 text-[hsl(var(--profit))]',
    loss: 'from-[hsl(var(--loss))]/20 to-transparent border-[hsl(var(--loss))]/20 text-[hsl(var(--loss))]',
    warning: 'from-[hsl(var(--warning))]/20 to-transparent border-[hsl(var(--warning))]/20 text-[hsl(var(--warning))]',
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05 }}
      className={`relative overflow-hidden rounded-xl border bg-gradient-to-br ${colorClasses[color]} p-4`}
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs text-muted-foreground mb-1">{label}</p>
          <p className="text-xl font-bold text-foreground">{value}</p>
          {subValue && <p className="text-xs text-muted-foreground mt-1">{subValue}</p>}
        </div>
        <div className={`p-2 rounded-lg bg-background/50`}>{icon}</div>
      </div>
    </motion.div>
  );
}

export function RiskMetrics({ data }: RiskMetricsProps) {
  if (!data) return null;
  const formatCurrency = (value: number) => {
    const v = value || 0;
    if (Math.abs(v) >= 1000) {
      return `$${(v / 1000).toFixed(1)}k`;
    }
    return `$${v.toFixed(2)}`;
  };

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
      {/* Win Rate */}
      <MetricCard
        label="Win Rate"
        value={`${data.win_rate}%`}
        subValue={`Target: 50%+`}
        color={data.win_rate >= 50 ? 'profit' : 'loss'}
        index={0}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        }
      />

      {/* Profit Factor */}
      <MetricCard
        label="Profit Factor"
        value={(data.profit_factor || 0).toFixed(2)}
        subValue={(data.profit_factor || 0) >= 1.5 ? 'Excellent' : (data.profit_factor || 0) >= 1 ? 'Good' : 'Poor'}
        color={(data.profit_factor || 0) >= 1.5 ? 'profit' : (data.profit_factor || 0) >= 1 ? 'warning' : 'loss'}
        index={1}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
          </svg>
        }
      />

      {/* Sharpe Ratio */}
      <MetricCard
        label="Sharpe Ratio"
        value={(data.sharpe_ratio || 0).toFixed(2)}
        subValue={(data.sharpe_ratio || 0) >= 2 ? 'Excellent' : (data.sharpe_ratio || 0) >= 1 ? 'Good' : 'Review needed'}
        color={(data.sharpe_ratio || 0) >= 2 ? 'profit' : (data.sharpe_ratio || 0) >= 1 ? 'primary' : 'warning'}
        index={2}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
        }
      />

      {/* Max Drawdown */}
      <MetricCard
        label="Max Drawdown"
        value={formatCurrency(data.max_drawdown)}
        subValue={data.max_drawdown_percent ? `${data.max_drawdown_percent.toFixed(1)}%` : undefined}
        color="loss"
        index={3}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 17h8m0 0V9m0 8l-8-8-4 4-6-6" />
          </svg>
        }
      />

      {/* Average Win */}
      <MetricCard
        label="Avg Win"
        value={formatCurrency(data.avg_win)}
        color="profit"
        index={4}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        }
      />

      {/* Average Loss */}
      <MetricCard
        label="Avg Loss"
        value={formatCurrency(data.avg_loss)}
        color="loss"
        index={5}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        }
      />

      {/* Best Trade */}
      <MetricCard
        label="Best Trade"
        value={formatCurrency(data.best_trade)}
        color="profit"
        index={6}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
          </svg>
        }
      />

      {/* Worst Trade */}
      <MetricCard
        label="Worst Trade"
        value={formatCurrency(data.worst_trade)}
        color="loss"
        index={7}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
        }
      />
    </div>
  );
}
