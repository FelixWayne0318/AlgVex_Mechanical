"use client";

import { useEffect, useRef, useState } from "react";
import { createChart, IChartApi, ISeriesApi, AreaData, Time } from "lightweight-charts";

interface PnLChartProps {
  data: Array<{
    date: string;
    cumulative_pnl: number;
  }>;
}

export function PnLChart({ data }: PnLChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Create chart
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { color: "transparent" },
        textColor: "#a1a1aa",
      },
      grid: {
        vertLines: { color: "rgba(255, 255, 255, 0.05)" },
        horzLines: { color: "rgba(255, 255, 255, 0.05)" },
      },
      width: chartContainerRef.current.clientWidth,
      height: 300,
      rightPriceScale: {
        borderColor: "rgba(255, 255, 255, 0.1)",
      },
      timeScale: {
        borderColor: "rgba(255, 255, 255, 0.1)",
        timeVisible: true,
      },
      crosshair: {
        vertLine: {
          color: "rgba(0, 212, 170, 0.3)",
          width: 1,
          style: 3,
        },
        horzLine: {
          color: "rgba(0, 212, 170, 0.3)",
          width: 1,
          style: 3,
        },
      },
    });

    chartRef.current = chart;

    // Create area series
    const areaSeries = chart.addAreaSeries({
      lineColor: "#00d4aa",
      topColor: "rgba(0, 212, 170, 0.4)",
      bottomColor: "rgba(0, 212, 170, 0.0)",
      lineWidth: 2,
    });

    seriesRef.current = areaSeries;

    // Handle resize
    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
        });
      }
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, []);

  // Update data
  useEffect(() => {
    if (!seriesRef.current || !data.length) return;

    const chartData: AreaData<Time>[] = data.map((item) => ({
      time: item.date as Time,
      value: item.cumulative_pnl,
    }));

    seriesRef.current.setData(chartData);

    // Fit content
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return (
    <div className="chart-container">
      <div ref={chartContainerRef} />
    </div>
  );
}
