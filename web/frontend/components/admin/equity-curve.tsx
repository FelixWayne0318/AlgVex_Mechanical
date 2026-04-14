"use client";

import { useEffect, useRef } from "react";
import { motion } from "framer-motion";

interface EquityDataPoint {
  time: string;
  value: number;
}

interface EquityCurveProps {
  data?: EquityDataPoint[];
}

export function EquityCurve({ data }: EquityCurveProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    // Dynamic import lightweight-charts only on client
    import("lightweight-charts").then(({ createChart, ColorType }) => {
      if (!containerRef.current) return;

      // Clear previous chart
      if (chartRef.current) {
        chartRef.current.remove();
      }

      const chart = createChart(containerRef.current, {
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: "hsl(var(--muted-foreground))",
        },
        grid: {
          vertLines: { color: "hsl(var(--border) / 0.3)" },
          horzLines: { color: "hsl(var(--border) / 0.3)" },
        },
        width: containerRef.current.clientWidth,
        height: 240,
        rightPriceScale: {
          borderColor: "hsl(var(--border))",
        },
        timeScale: {
          borderColor: "hsl(var(--border))",
          timeVisible: true,
        },
      });

      chartRef.current = chart;

      const areaSeries = chart.addAreaSeries({
        lineColor: "hsl(var(--primary))",
        topColor: "hsl(var(--primary) / 0.4)",
        bottomColor: "hsl(var(--primary) / 0.05)",
        lineWidth: 2,
      });

      // Only render chart when real data is available
      const chartData = data?.length
        ? data.map((d) => ({ time: d.time, value: d.value }))
        : [];

      areaSeries.setData(chartData as any);
      chart.timeScale().fitContent();

      // Handle resize
      const handleResize = () => {
        if (containerRef.current && chartRef.current) {
          chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
        }
      };

      window.addEventListener("resize", handleResize);
      return () => {
        window.removeEventListener("resize", handleResize);
        if (chartRef.current) {
          chartRef.current.remove();
          chartRef.current = null;
        }
      };
    });
  }, [data]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div ref={containerRef} className="w-full" />
      {(!data || !data.length) && (
        <div className="flex flex-col items-center justify-center h-60 text-muted-foreground">
          <svg className="h-8 w-8 mb-3 opacity-40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
          </svg>
          <p className="text-sm font-medium">No equity data yet</p>
          <p className="text-xs mt-1">Chart will populate as trades are executed</p>
        </div>
      )}
    </motion.div>
  );
}

