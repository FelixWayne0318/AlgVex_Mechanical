'use client';

import { useEffect, useRef } from 'react';
import { createChart, IChartApi, Time } from 'lightweight-charts';

interface DataPoint {
  t: string;
  v: number;
}

interface BacktestEquityChartProps {
  equityData: DataPoint[];
  drawdownData?: DataPoint[];
  height?: number;
}

export function BacktestEquityChart({
  equityData = [],
  drawdownData = [],
  height = 400,
}: BacktestEquityChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current || equityData.length === 0) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { color: 'transparent' },
        textColor: 'hsl(215, 20%, 55%)',
      },
      grid: {
        vertLines: { color: 'hsl(217, 33%, 17%)' },
        horzLines: { color: 'hsl(217, 33%, 17%)' },
      },
      crosshair: {
        mode: 1,
        vertLine: { color: 'hsl(173, 80%, 50%)', width: 1, style: 2 },
        horzLine: { color: 'hsl(173, 80%, 50%)', width: 1, style: 2 },
      },
      rightPriceScale: { borderColor: 'hsl(217, 33%, 17%)' },
      timeScale: {
        borderColor: 'hsl(217, 33%, 17%)',
        timeVisible: false,
      },
      width: chartContainerRef.current.clientWidth,
      height,
    });

    chartRef.current = chart;

    // Equity curve (area series)
    const equitySeries = chart.addAreaSeries({
      lineColor: 'hsl(173, 80%, 50%)',
      topColor: 'hsla(173, 80%, 50%, 0.4)',
      bottomColor: 'hsla(173, 80%, 50%, 0.05)',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    equitySeries.setData(
      equityData.map((d) => ({ time: d.t as Time, value: d.v }))
    );

    // Drawdown histogram (bottom 25%)
    if (drawdownData.length > 0) {
      const ddSeries = chart.addHistogramSeries({
        color: 'hsla(0, 72%, 51%, 0.5)',
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        priceScaleId: 'drawdown',
      });

      chart.priceScale('drawdown').applyOptions({
        scaleMargins: { top: 0.75, bottom: 0 },
      });

      ddSeries.setData(
        drawdownData.map((d) => ({
          time: d.t as Time,
          value: d.v > 0 ? -d.v : 0, // negative for visual clarity
          color: d.v > 2 ? 'hsla(0, 72%, 51%, 0.7)' : 'hsla(0, 72%, 51%, 0.3)',
        }))
      );
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
        });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [equityData, drawdownData, height]);

  if (equityData.length === 0) {
    return (
      <div
        className="flex items-center justify-center bg-card/50 rounded-xl border border-border"
        style={{ height }}
      >
        <div className="text-center text-muted-foreground">
          <svg className="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
          </svg>
          <p>No equity data</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative">
      <div ref={chartContainerRef} className="rounded-xl overflow-hidden" />
    </div>
  );
}
