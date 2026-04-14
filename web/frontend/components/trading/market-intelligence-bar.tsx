'use client';

import useSWR from 'swr';
import {
  Bot,
  Users,
  Percent,
  Activity,
  BarChart3,
  Zap,
  TrendingUp,
  TrendingDown,
} from 'lucide-react';

interface MetricItemProps {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  subValue?: string;
  type?: 'positive' | 'negative' | 'neutral' | 'warning';
  isLoading?: boolean;
}

function MetricItem({ icon, label, value, subValue, type = 'neutral', isLoading = false }: MetricItemProps) {
  const colorClass = {
    positive: 'text-[hsl(var(--profit))]',
    negative: 'text-[hsl(var(--loss))]',
    warning: 'text-yellow-500',
    neutral: 'text-foreground',
  }[type];

  const bgClass = {
    positive: 'bg-[hsl(var(--profit))]/10',
    negative: 'bg-[hsl(var(--loss))]/10',
    warning: 'bg-yellow-500/10',
    neutral: 'bg-primary/10',
  }[type];

  return (
    <div className="flex items-center gap-2 sm:gap-3 px-3 sm:px-4 py-2 rounded-lg hover:bg-muted/30 transition-colors">
      <div className={`p-1.5 rounded-md ${bgClass}`}>
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-[10px] sm:text-xs text-muted-foreground whitespace-nowrap">{label}</p>
        {isLoading ? (
          <div className="w-12 h-4 bg-muted rounded animate-pulse" />
        ) : (
          <div className="flex items-baseline gap-1.5">
            <p className={`text-xs sm:text-sm font-semibold ${colorClass} whitespace-nowrap`}>{value}</p>
            {subValue && (
              <span className="text-[10px] text-muted-foreground hidden sm:inline">{subValue}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Divider() {
  return <div className="w-px h-8 bg-border/50 mx-1 hidden sm:block" />;
}

export function MarketIntelligenceBar() {
  // Fetch bot status
  const { data: status, error: statusError } = useSWR('/api/public/system-status', {
    refreshInterval: 30000,
  });

  // Fetch BTC ticker (for volume)
  const { data: ticker } = useSWR('/api/trading/ticker/BTCUSDT', {
    refreshInterval: 10000,
  });

  // Fetch long/short ratio
  const { data: sentiment } = useSWR('/api/trading/long-short-ratio/BTCUSDT', {
    refreshInterval: 60000,
  });

  // Fetch mark price (includes funding rate)
  const { data: markPrice } = useSWR('/api/trading/mark-price/BTCUSDT', {
    refreshInterval: 30000,
  });

  // Fetch open interest
  const { data: openInterest } = useSWR('/api/trading/open-interest/BTCUSDT', {
    refreshInterval: 60000,
  });

  // Fetch performance stats
  const { data: performance } = useSWR('/api/public/performance?days=7', {
    refreshInterval: 60000,
  });

  // Fetch mechanical state for signal display
  const { data: mechState } = useSWR('/api/public/mechanical/state', {
    refreshInterval: 30000,
  });

  const isLoading = !status && !statusError;

  // Long/Short ratio - values > 1 means more longs
  const rawRatio = sentiment?.data?.[0]?.long_short_ratio || sentiment?.longShortRatio;
  const hasLongShortData = rawRatio !== undefined && rawRatio !== null && rawRatio > 0;
  const longShortRatio = hasLongShortData ? rawRatio : 1;
  const longPercent = hasLongShortData ? (longShortRatio / (longShortRatio + 1)) * 100 : 50;

  // Funding rate (typically shown as percentage)
  const fundingRate = markPrice?.funding_rate
    ? markPrice.funding_rate * 100
    : markPrice?.lastFundingRate
    ? parseFloat(markPrice.lastFundingRate) * 100
    : 0;

  // Open Interest
  const oiValue = openInterest?.value || 0;
  const oiChange = openInterest?.change_24h || 0;
  const formatOI = (value: number) => {
    if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
    if (value >= 1e6) return `$${(value / 1e6).toFixed(0)}M`;
    return `$${value.toLocaleString()}`;
  };

  // 24h Volume
  const volume24h = ticker?.quote_volume_24h || 0;
  const formatVolume = (value: number) => {
    if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
    if (value >= 1e6) return `$${(value / 1e6).toFixed(0)}M`;
    return `$${value.toLocaleString()}`;
  };

  // Win rate from performance
  const winRate = performance?.win_rate || 0;
  const totalTrades = performance?.total_trades || 0;

  // Latest mechanical signal
  const signal = mechState?.signal || 'HOLD';
  const confidence = mechState?.signal_tier || 'HOLD';
  const getSignalType = (s: string): 'positive' | 'negative' | 'neutral' => {
    if (s === 'BUY' || s === 'LONG') return 'positive';
    if (s === 'SELL' || s === 'SHORT') return 'negative';
    return 'neutral';
  };

  return (
    <div className="w-full overflow-x-auto scrollbar-hide">
      <div className="flex items-center justify-center gap-1 sm:gap-2 min-w-max px-4 py-1">
        {/* Bot Status - First Item */}
        <MetricItem
          icon={
            <Bot className={`h-3 w-3 sm:h-4 sm:w-4 ${status?.trading_active ? 'text-[hsl(var(--profit))]' : 'text-muted-foreground'}`} />
          }
          label="Bot Status"
          value={status?.trading_active ? 'Active' : 'Offline'}
          type={status?.trading_active ? 'positive' : 'neutral'}
          isLoading={isLoading}
        />

        <Divider />

        {/* Long/Short Ratio */}
        <MetricItem
          icon={
            <Users className={`h-3 w-3 sm:h-4 sm:w-4 ${hasLongShortData ? (longPercent > 50 ? 'text-[hsl(var(--profit))]' : 'text-[hsl(var(--loss))]') : 'text-muted-foreground'}`} />
          }
          label="Long/Short"
          value={hasLongShortData ? `${longPercent.toFixed(0)}% L` : '--'}
          subValue={hasLongShortData ? `(${parseFloat(String(longShortRatio)).toFixed(2)})` : undefined}
          type={hasLongShortData ? (longPercent > 55 ? 'positive' : longPercent < 45 ? 'negative' : 'neutral') : 'neutral'}
        />

        <Divider />

        {/* Funding Rate */}
        <MetricItem
          icon={
            <Percent className={`h-3 w-3 sm:h-4 sm:w-4 ${fundingRate >= 0 ? 'text-[hsl(var(--profit))]' : 'text-[hsl(var(--loss))]'}`} />
          }
          label="Funding"
          value={`${fundingRate >= 0 ? '+' : ''}${fundingRate.toFixed(4)}%`}
          subValue={fundingRate > 0.01 ? 'Longs pay' : fundingRate < -0.01 ? 'Shorts pay' : undefined}
          type={Math.abs(fundingRate) > 0.05 ? 'warning' : fundingRate >= 0 ? 'positive' : 'negative'}
        />

        <Divider />

        {/* Open Interest */}
        <MetricItem
          icon={
            <BarChart3 className={`h-3 w-3 sm:h-4 sm:w-4 ${oiChange >= 0 ? 'text-[hsl(var(--profit))]' : 'text-[hsl(var(--loss))]'}`} />
          }
          label="Open Interest"
          value={oiValue > 0 ? formatOI(oiValue) : '--'}
          subValue={oiChange !== 0 ? `${oiChange >= 0 ? '+' : ''}${oiChange.toFixed(1)}%` : undefined}
          type={oiChange > 3 ? 'positive' : oiChange < -3 ? 'negative' : 'neutral'}
          isLoading={!openInterest}
        />

        <Divider />

        {/* 24h Volume */}
        <MetricItem
          icon={
            <Activity className="h-3 w-3 sm:h-4 sm:w-4 text-blue-500" />
          }
          label="24h Volume"
          value={volume24h > 0 ? formatVolume(volume24h) : '--'}
          type="neutral"
          isLoading={!ticker}
        />

        <Divider />

        {/* Win Rate */}
        <MetricItem
          icon={
            totalTrades > 0 ? (
              winRate >= 50 ? (
                <TrendingUp className="h-3 w-3 sm:h-4 sm:w-4 text-[hsl(var(--profit))]" />
              ) : (
                <TrendingDown className="h-3 w-3 sm:h-4 sm:w-4 text-[hsl(var(--loss))]" />
              )
            ) : (
              <Activity className="h-3 w-3 sm:h-4 sm:w-4 text-muted-foreground" />
            )
          }
          label="7D Win Rate"
          value={totalTrades > 0 ? `${winRate.toFixed(0)}%` : '--'}
          subValue={totalTrades > 0 ? `${totalTrades} trades` : undefined}
          type={winRate >= 55 ? 'positive' : winRate >= 45 ? 'neutral' : winRate > 0 ? 'negative' : 'neutral'}
          isLoading={!performance}
        />

        <Divider />

        {/* Mechanical Signal */}
        <MetricItem
          icon={
            <Zap className={`h-3 w-3 sm:h-4 sm:w-4 ${
              getSignalType(signal) === 'positive' ? 'text-[hsl(var(--profit))]' :
              getSignalType(signal) === 'negative' ? 'text-[hsl(var(--loss))]' : 'text-foreground'
            }`} />
          }
          label="Signal"
          value={signal}
          subValue={mechState?.net_raw != null ? `raw: ${mechState.net_raw >= 0 ? '+' : ''}${mechState.net_raw.toFixed(2)}` : confidence}
          type={getSignalType(signal)}
          isLoading={!mechState}
        />
      </div>
    </div>
  );
}
