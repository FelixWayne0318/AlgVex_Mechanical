'use client';

import { useEffect, useRef, useState } from 'react';
import { motion, useSpring, useTransform } from 'framer-motion';

interface AnimatedNumberProps {
  value: number;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  duration?: number;
  className?: string;
}

export function AnimatedNumber({
  value,
  prefix = '',
  suffix = '',
  decimals = 2,
  duration = 1,
  className = '',
}: AnimatedNumberProps) {
  const [displayValue, setDisplayValue] = useState(`${prefix}${(value || 0).toFixed(decimals)}${suffix}`);
  const spring = useSpring(value || 0, { stiffness: 100, damping: 20 });
  const display = useTransform(spring, (current) =>
    `${prefix}${current.toFixed(decimals)}${suffix}`
  );

  useEffect(() => {
    spring.set(value || 0);
  }, [spring, value]);

  useEffect(() => {
    const unsub = display.on('change', (v) => setDisplayValue(v));
    return unsub;
  }, [display]);

  return <span className={className}>{displayValue}</span>;
}

interface StatsCardProps {
  title: string;
  value: number;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  change?: number;
  changeLabel?: string;
  icon?: React.ReactNode;
  color?: 'default' | 'profit' | 'loss' | 'primary';
  loading?: boolean;
}

export function StatsCard({
  title,
  value,
  prefix = '',
  suffix = '',
  decimals = 2,
  change,
  changeLabel,
  icon,
  color = 'default',
  loading = false,
}: StatsCardProps) {
  const colorClasses = {
    default: 'border-border',
    profit: 'border-[hsl(var(--profit))]/30',
    loss: 'border-[hsl(var(--loss))]/30',
    primary: 'border-primary/30',
  };

  const valueColors = {
    default: 'text-foreground',
    profit: 'text-[hsl(var(--profit))]',
    loss: 'text-[hsl(var(--loss))]',
    primary: 'text-primary',
  };

  if (loading) {
    return (
      <div className="rounded-xl border border-border bg-card/50 p-4">
        <div className="animate-pulse space-y-3">
          <div className="h-4 w-24 bg-muted rounded" />
          <div className="h-8 w-32 bg-muted rounded" />
          <div className="h-3 w-20 bg-muted rounded" />
        </div>
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className={`rounded-xl border ${colorClasses[color]} bg-card/50 p-4 hover:bg-card transition-colors`}
    >
      <div className="flex items-start justify-between mb-2">
        <span className="text-sm text-muted-foreground">{title}</span>
        {icon && <div className="text-muted-foreground">{icon}</div>}
      </div>

      <div className={`text-lg sm:text-xl md:text-2xl font-bold ${valueColors[color]}`}>
        <AnimatedNumber
          value={value}
          prefix={prefix}
          suffix={suffix}
          decimals={decimals}
        />
      </div>

      {(change !== undefined || changeLabel) && (
        <div className="mt-2 flex items-center gap-1">
          {change !== undefined && (
            <span
              className={`text-sm font-medium ${
                change >= 0 ? 'text-[hsl(var(--profit))]' : 'text-[hsl(var(--loss))]'
              }`}
            >
              {change >= 0 ? '+' : ''}
              {change.toFixed(2)}%
            </span>
          )}
          {changeLabel && (
            <span className="text-xs text-muted-foreground">{changeLabel}</span>
          )}
        </div>
      )}
    </motion.div>
  );
}

interface PerformanceStatsProps {
  stats: {
    total_pnl: number;
    today_pnl: number;
    week_pnl: number;
    month_pnl: number;
    win_rate: number;
    total_trades: number;
  };
  loading?: boolean;
}

export function PerformanceStats({ stats, loading = false }: PerformanceStatsProps) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
      <StatsCard
        title="Total PnL"
        value={stats.total_pnl}
        prefix="$"
        decimals={2}
        color={stats.total_pnl >= 0 ? 'profit' : 'loss'}
        loading={loading}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        }
      />

      <StatsCard
        title="Today"
        value={stats.today_pnl}
        prefix="$"
        decimals={2}
        color={stats.today_pnl >= 0 ? 'profit' : 'loss'}
        loading={loading}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
        }
      />

      <StatsCard
        title="This Week"
        value={stats.week_pnl}
        prefix="$"
        decimals={2}
        color={stats.week_pnl >= 0 ? 'profit' : 'loss'}
        loading={loading}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
        }
      />

      <StatsCard
        title="This Month"
        value={stats.month_pnl}
        prefix="$"
        decimals={2}
        color={stats.month_pnl >= 0 ? 'profit' : 'loss'}
        loading={loading}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
          </svg>
        }
      />

      <StatsCard
        title="Win Rate"
        value={stats.win_rate}
        suffix="%"
        decimals={1}
        color={stats.win_rate >= 50 ? 'profit' : 'loss'}
        loading={loading}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        }
      />

      <StatsCard
        title="Total Trades"
        value={stats.total_trades}
        decimals={0}
        color="primary"
        loading={loading}
        icon={
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
          </svg>
        }
      />
    </div>
  );
}

// Skeleton loader for cards
export function StatsCardSkeleton() {
  return (
    <div className="rounded-xl border border-border bg-card/50 p-4">
      <div className="animate-pulse space-y-3">
        <div className="h-4 w-24 bg-muted rounded" />
        <div className="h-8 w-32 bg-muted rounded" />
        <div className="h-3 w-20 bg-muted rounded" />
      </div>
    </div>
  );
}
