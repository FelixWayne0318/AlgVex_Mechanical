"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { BarChart3, TrendingUp, TrendingDown, Target, Shield } from "lucide-react";

interface Bucket {
  count: number;
  total_pnl: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_pnl: number;
}

interface AttributionData {
  by_exit_type: Record<string, Bucket>;
  by_confidence: Record<string, Bucket>;
  by_trend: Record<string, Bucket>;
  by_grade: Record<string, Bucket>;
  total_trades: number;
}

interface PerformanceAttributionProps {
  data?: AttributionData | null;
}

type TabId = "exit_type" | "confidence" | "trend" | "grade";

const TAB_CONFIG: { id: TabId; label: string; icon: typeof BarChart3 }[] = [
  { id: "exit_type", label: "Exit Type", icon: Target },
  { id: "confidence", label: "Confidence", icon: Shield },
  { id: "trend", label: "Trend", icon: TrendingUp },
  { id: "grade", label: "Grade", icon: BarChart3 },
];

export function PerformanceAttribution({ data }: PerformanceAttributionProps) {
  const [activeTab, setActiveTab] = useState<TabId>("exit_type");

  if (!data || data.total_trades === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
        <BarChart3 className="h-8 w-8 mb-3 opacity-40" />
        <p className="text-sm font-medium">No attribution data</p>
        <p className="text-xs mt-1">Data appears after trades are evaluated</p>
      </div>
    );
  }

  const bucketMap: Record<TabId, Record<string, Bucket>> = {
    exit_type: data.by_exit_type || {},
    confidence: data.by_confidence || {},
    trend: data.by_trend || {},
    grade: data.by_grade || {},
  };

  const buckets = bucketMap[activeTab];
  const entries = Object.entries(buckets).sort((a, b) => b[1].count - a[1].count);

  // Find max PnL for bar scaling
  const maxAbsPnl = Math.max(...entries.map(([, b]) => Math.abs(b.total_pnl)), 1);

  return (
    <div className="space-y-4">
      {/* Tab Selector */}
      <div className="flex gap-1 p-1 bg-muted/30 rounded-lg overflow-x-auto">
        {TAB_CONFIG.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 flex items-center justify-center gap-1 sm:gap-1.5 px-2 sm:px-3 py-1.5 rounded text-xs font-medium transition-colors whitespace-nowrap ${
                activeTab === tab.id
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className="h-3.5 w-3.5 flex-shrink-0" />
              <span className="hidden sm:inline">{tab.label}</span>
              <span className="sm:hidden">{tab.label.slice(0, 4)}</span>
            </button>
          );
        })}
      </div>

      {/* Attribution Bars */}
      <div className="space-y-2">
        {entries.map(([key, bucket], index) => {
          const pnlPct = maxAbsPnl > 0 ? (bucket.total_pnl / maxAbsPnl) * 100 : 0;
          const isPositive = bucket.total_pnl >= 0;

          return (
            <motion.div
              key={key}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.2, delay: index * 0.05 }}
              className="group"
            >
              {/* Label row */}
              <div className="flex items-center justify-between text-xs mb-1 gap-2">
                <span className="font-medium truncate min-w-0">{key}</span>
                <div className="flex items-center gap-1.5 sm:gap-3 text-muted-foreground flex-shrink-0">
                  <span className="hidden sm:inline">{bucket.count} trades</span>
                  <span className="sm:hidden">{bucket.count}</span>
                  <span>WR {bucket.win_rate}%</span>
                  <span className={`font-mono font-medium ${
                    isPositive ? "text-green-500" : "text-red-500"
                  }`}>
                    {isPositive ? "+" : ""}{bucket.total_pnl.toFixed(0)}
                    <span className="hidden sm:inline">.{Math.abs(bucket.total_pnl % 1 * 100).toFixed(0).padStart(2, '0')} USDT</span>
                  </span>
                </div>
              </div>

              {/* PnL Bar */}
              <div className="h-4 bg-muted/30 rounded overflow-hidden relative">
                <div
                  className={`h-full rounded transition-all duration-500 ${
                    isPositive ? "bg-green-500/40" : "bg-red-500/40"
                  }`}
                  style={{ width: `${Math.min(Math.abs(pnlPct), 100)}%` }}
                />
                {/* Win/Loss segments */}
                <div className="absolute inset-0 flex">
                  {bucket.wins > 0 && (
                    <div
                      className="h-full bg-green-500/20"
                      style={{ width: `${(bucket.wins / bucket.count) * 100}%` }}
                    />
                  )}
                  {bucket.losses > 0 && (
                    <div
                      className="h-full bg-red-500/20"
                      style={{ width: `${(bucket.losses / bucket.count) * 100}%` }}
                    />
                  )}
                </div>
              </div>
            </motion.div>
          );
        })}
      </div>

      {/* Summary */}
      <div className="text-xs text-muted-foreground text-center pt-2 border-t border-border/30">
        {data.total_trades} total trades
      </div>
    </div>
  );
}
