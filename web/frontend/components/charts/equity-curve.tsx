'use client';

import { useEffect, useRef, useState } from 'react';
import { createChart, IChartApi, ISeriesApi, LineData, Time } from 'lightweight-charts';

interface EquityCurveData {
  date: string;
  pnl: number;
  cumulative: number;
}

interface EquityCurveProps {
  data?: EquityCurveData[];
  height?: number;
  showVolume?: boolean;
}

export function EquityCurve({ data = [], height = 300, showVolume = true }: EquityCurveProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const lineSeriesRef = useRef<ISeriesApi<'Area'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Create chart
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
        vertLine: {
          color: 'hsl(173, 80%, 50%)',
          width: 1,
          style: 2,
        },
        horzLine: {
          color: 'hsl(173, 80%, 50%)',
          width: 1,
          style: 2,
        },
      },
      rightPriceScale: {
        borderColor: 'hsl(217, 33%, 17%)',
      },
      timeScale: {
        borderColor: 'hsl(217, 33%, 17%)',
        timeVisible: true,
        secondsVisible: false,
      },
      width: chartContainerRef.current.clientWidth,
      height: height,
    });

    chartRef.current = chart;

    // Create area series for equity curve
    const lineSeries = chart.addAreaSeries({
      lineColor: 'hsl(173, 80%, 50%)',
      topColor: 'hsla(173, 80%, 50%, 0.4)',
      bottomColor: 'hsla(173, 80%, 50%, 0.05)',
      lineWidth: 2,
      priceFormat: {
        type: 'price',
        precision: 2,
        minMove: 0.01,
      },
    });
    lineSeriesRef.current = lineSeries;

    // Create histogram series for daily PnL
    if (showVolume) {
      const volumeSeries = chart.addHistogramSeries({
        color: 'hsl(173, 80%, 50%)',
        priceFormat: {
          type: 'price',
          precision: 2,
          minMove: 0.01,
        },
        priceScaleId: 'volume',
      });
      volumeSeriesRef.current = volumeSeries;

      chart.priceScale('volume').applyOptions({
        scaleMargins: {
          top: 0.8,
          bottom: 0,
        },
      });
    }

    // Handle resize
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
  }, [height, showVolume]);

  // Update data
  useEffect(() => {
    if (!lineSeriesRef.current || data.length === 0) return;

    const lineData: LineData<Time>[] = data.map((d) => ({
      time: d.date as Time,
      value: d.cumulative,
    }));

    lineSeriesRef.current.setData(lineData);

    if (showVolume && volumeSeriesRef.current) {
      const volumeData = data.map((d) => ({
        time: d.date as Time,
        value: Math.abs(d.pnl),
        color: d.pnl >= 0 ? 'hsla(160, 84%, 39%, 0.5)' : 'hsla(0, 72%, 51%, 0.5)',
      }));
      volumeSeriesRef.current.setData(volumeData);
    }

    // Fit content
    if (chartRef.current) {
      chartRef.current.timeScale().fitContent();
    }
  }, [data, showVolume]);

  if (data.length === 0) {
    return (
      <div
        className="flex items-center justify-center bg-card/50 rounded-xl border border-border"
        style={{ height }}
      >
        <div className="text-center text-muted-foreground">
          <svg className="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
          </svg>
          <p>No trading data available</p>
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
