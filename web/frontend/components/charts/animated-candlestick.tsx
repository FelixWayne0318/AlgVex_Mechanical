'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import useSWR from 'swr';

interface Candle {
  id: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  timestamp: number;
}

interface AnimatedCandlestickProps {
  height?: number;
  candleCount?: number;
  showVolume?: boolean;
  title?: string;
  symbol?: string;
  interval?: string;
}

function parseKlineData(klines: any[]): Candle[] {
  if (!klines || !Array.isArray(klines)) return [];

  return klines.map((k, index) => ({
    id: index,
    open: parseFloat(k.open || k[1]),
    high: parseFloat(k.high || k[2]),
    low: parseFloat(k.low || k[3]),
    close: parseFloat(k.close || k[4]),
    volume: parseFloat(k.volume || k[5]),
    timestamp: k.open_time || k[0],
  }));
}

export function AnimatedCandlestick({
  height = 320,
  candleCount = 30,
  showVolume = true,
  title = 'BTC/USDT',
  symbol = 'BTCUSDT',
  interval = '30m',
}: AnimatedCandlestickProps) {
  const [isClient, setIsClient] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const { data: klineData, error } = useSWR(
    isClient ? `/api/trading/klines/${symbol}?interval=${interval}&limit=${candleCount}` : null,
    { refreshInterval: 10000 }
  );

  useEffect(() => {
    setIsClient(true);
  }, []);

  const candles = klineData?.klines ? parseKlineData(klineData.klines) : [];

  const currentPrice = candles.length > 0 ? candles[candles.length - 1].close : 0;
  const firstPrice = candles.length > 0 ? candles[0].open : 0;
  const priceChange = firstPrice > 0 ? ((currentPrice - firstPrice) / firstPrice) * 100 : 0;

  const headerHeight = 72;
  const bottomPadding = 36;
  const chartAreaHeight = height - headerHeight - bottomPadding;
  const candleChartHeight = showVolume ? chartAreaHeight * 0.78 : chartAreaHeight;
  const volumeChartHeight = showVolume ? chartAreaHeight * 0.18 : 0;

  const prices = candles.length > 0 ? candles.flatMap((c) => [c.high, c.low]) : [0];
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const priceRange = maxPrice - minPrice || 1;

  const volumes = candles.length > 0 ? candles.map((c) => c.volume) : [0];
  const maxVolume = Math.max(...volumes);

  const priceToPercent = useCallback(
    (price: number) => {
      return ((maxPrice - price) / priceRange) * 100;
    },
    [maxPrice, priceRange]
  );

  // Loading state
  if (!isClient || candles.length === 0) {
    return (
      <div
        ref={containerRef}
        className="relative"
        style={{ height, minHeight: height }}
      >
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 rounded-full border-2 border-primary/30 border-t-primary animate-spin" />
            <span className="text-sm text-muted-foreground">
              {error ? 'Failed to load data' : 'Loading...'}
            </span>
          </div>
        </div>
      </div>
    );
  }

  const candleWidth = 100 / candles.length;

  return (
    <div
      ref={containerRef}
      className="relative"
      style={{ height, minHeight: height }}
    >
      {/* Header */}
      <div
        className="relative px-4 sm:px-5 py-4 flex items-center justify-between"
        style={{ height: headerHeight }}
      >
        <div className="flex items-center gap-3">
          {/* Bitcoin icon - minimal style matching theme */}
          <div className="w-9 h-9 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center">
            <span className="text-primary font-bold text-xl">₿</span>
          </div>
          <div>
            <h3 className="font-semibold text-foreground text-base tracking-tight">{title}</h3>
            <p className="text-xs text-muted-foreground">Perpetual · {interval}</p>
          </div>
        </div>

        <div className="text-right">
          <p className="font-mono text-xl sm:text-2xl font-bold text-foreground tracking-tight">
            ${currentPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </p>
          <div className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${
            priceChange >= 0
              ? 'bg-primary/15 text-primary'
              : 'bg-[hsl(var(--loss))]/15 text-[hsl(var(--loss))]'
          }`}>
            {priceChange >= 0 ? (
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M7 11l5-5m0 0l5 5m-5-5v12" />
              </svg>
            ) : (
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M17 13l-5 5m0 0l-5-5m5 5V6" />
              </svg>
            )}
            {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(2)}%
          </div>
        </div>
      </div>

      {/* Chart area */}
      <div className="relative px-3" style={{ height: chartAreaHeight }}>
        {/* Price labels */}
        <div
          className="absolute right-2 top-0 flex flex-col justify-between z-10 pointer-events-none"
          style={{ height: candleChartHeight }}
        >
          {[maxPrice, (maxPrice + minPrice) / 2, minPrice].map((price, i) => (
            <span
              key={i}
              className="text-[10px] text-muted-foreground/60 font-mono tabular-nums"
            >
              ${price.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </span>
          ))}
        </div>

        {/* Grid lines */}
        <div className="absolute inset-x-3 top-0" style={{ height: candleChartHeight }}>
          {[0, 25, 50, 75, 100].map((percent) => (
            <div
              key={percent}
              className="absolute w-full border-t border-primary/5"
              style={{ top: `${percent}%` }}
            />
          ))}
        </div>

        {/* Candlesticks - using primary (cyan/teal) color */}
        <div className="absolute inset-x-3 top-0 flex" style={{ height: candleChartHeight }}>
          {candles.map((candle, index) => {
            const isUp = candle.close >= candle.open;
            const isLatest = index === candles.length - 1;

            const highPercent = priceToPercent(candle.high);
            const lowPercent = priceToPercent(candle.low);
            const bodyTopPercent = priceToPercent(Math.max(candle.open, candle.close));
            const bodyBottomPercent = priceToPercent(Math.min(candle.open, candle.close));
            const bodyHeightPercent = Math.max(bodyBottomPercent - bodyTopPercent, 0.5);

            // Use primary color with varying opacity
            const wickOpacity = isLatest ? 0.8 : 0.4;
            const bodyOpacity = isUp ? (isLatest ? 0.9 : 0.7) : (isLatest ? 1 : 0.8);

            return (
              <div
                key={candle.id}
                className="relative"
                style={{ width: `${candleWidth}%`, height: '100%' }}
              >
                {/* Wick - rounded ends */}
                <div
                  className="absolute left-1/2 -translate-x-1/2"
                  style={{
                    top: `${highPercent}%`,
                    height: `${lowPercent - highPercent}%`,
                    width: '2px',
                    borderRadius: '1px',
                    backgroundColor: `hsl(var(--primary) / ${wickOpacity})`,
                  }}
                />
                {/* Body - Rounded Rectangle */}
                <div
                  className="absolute left-1/2 -translate-x-1/2"
                  style={{
                    top: `${bodyTopPercent}%`,
                    height: `${bodyHeightPercent}%`,
                    width: candleCount > 40 ? '55%' : '70%',
                    minHeight: '4px',
                    borderRadius: '4px',
                    backgroundColor: isUp
                      ? `hsl(var(--primary) / ${bodyOpacity * 0.25})`
                      : `hsl(var(--primary) / ${bodyOpacity})`,
                    border: `1.5px solid hsl(var(--primary) / ${bodyOpacity})`,
                    boxShadow: isLatest ? '0 0 10px hsl(var(--primary) / 0.4)' : 'none',
                  }}
                />
              </div>
            );
          })}
        </div>

        {/* Volume bars - using primary color */}
        {showVolume && (
          <div
            className="absolute inset-x-3 bottom-0 flex items-end gap-px"
            style={{ height: volumeChartHeight }}
          >
            {candles.map((candle, index) => {
              const isUp = candle.close >= candle.open;
              const barHeightPercent = maxVolume > 0 ? (candle.volume / maxVolume) * 100 : 0;
              const isLatest = index === candles.length - 1;

              return (
                <div
                  key={candle.id}
                  className="relative flex-1"
                  style={{ height: '100%' }}
                >
                  <div
                    className="absolute bottom-0 left-1/2 -translate-x-1/2"
                    style={{
                      width: '75%',
                      height: `${Math.max(barHeightPercent, 2)}%`,
                      borderRadius: '3px 3px 0 0',
                      backgroundColor: isUp
                        ? `hsl(var(--primary) / ${isLatest ? 0.25 : 0.15})`
                        : `hsl(var(--primary) / ${isLatest ? 0.35 : 0.25})`,
                    }}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="absolute bottom-3 left-4 flex items-center gap-2">
        <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
        <span className="text-xs text-muted-foreground">Live · Real Data</span>
      </div>
    </div>
  );
}

// Hero version - Full width, transparent background
interface HeroAnimatedCandlestickProps {
  symbol?: string;
  interval?: string;
}

export function HeroAnimatedCandlestick({ symbol = 'BTCUSDT', interval = '30m' }: HeroAnimatedCandlestickProps) {
  return (
    <div className="relative w-full">
      {/* Full width transparent container */}
      <div className="relative overflow-hidden">
        <AnimatedCandlestick
          height={360}
          candleCount={40}
          showVolume={true}
          title="BTC/USDT"
          symbol={symbol}
          interval={interval}
        />
      </div>
    </div>
  );
}
