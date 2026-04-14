"use client";

import { motion } from "framer-motion";
import { Shield, TrendingDown, Percent, Activity } from "lucide-react";

interface RiskData {
  max_drawdown?: number;
  sharpe_ratio?: number;
  win_rate?: number;
  risk_reward?: number;
  volatility?: number;
  var_95?: number;
}

interface RiskMetricsProps {
  data?: RiskData;
}

export function RiskMetrics({ data }: RiskMetricsProps) {
  const metrics = data || {};

  const items = [
    {
      label: "Max Drawdown",
      value: `${(metrics.max_drawdown || 0).toFixed(2)}%`,
      icon: TrendingDown,
      color: (metrics.max_drawdown || 0) > 10 ? "text-red-500" : "text-yellow-500",
      bgColor: (metrics.max_drawdown || 0) > 10 ? "bg-red-500/10" : "bg-yellow-500/10",
    },
    {
      label: "Sharpe Ratio",
      value: (metrics.sharpe_ratio || 0).toFixed(2),
      icon: Activity,
      color: (metrics.sharpe_ratio || 0) > 1 ? "text-green-500" : "text-yellow-500",
      bgColor: (metrics.sharpe_ratio || 0) > 1 ? "bg-green-500/10" : "bg-yellow-500/10",
    },
    {
      label: "Win Rate",
      value: `${(metrics.win_rate || 0).toFixed(1)}%`,
      icon: Percent,
      color: (metrics.win_rate || 0) > 50 ? "text-green-500" : "text-red-500",
      bgColor: (metrics.win_rate || 0) > 50 ? "bg-green-500/10" : "bg-red-500/10",
    },
    {
      label: "Risk/Reward",
      value: `1:${(metrics.risk_reward || 0).toFixed(1)}`,
      icon: Shield,
      color: (metrics.risk_reward || 0) > 1.5 ? "text-green-500" : "text-yellow-500",
      bgColor: (metrics.risk_reward || 0) > 1.5 ? "bg-green-500/10" : "bg-yellow-500/10",
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-3">
      {items.map((item, index) => {
        const Icon = item.icon;
        return (
          <motion.div
            key={item.label}
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.2, delay: index * 0.05 }}
            className="p-3 rounded-lg bg-muted/30 border border-border/50"
          >
            <div className="flex items-center gap-2 mb-2">
              <div className={`p-1.5 rounded ${item.bgColor}`}>
                <Icon className={`h-3.5 w-3.5 ${item.color}`} />
              </div>
              <span className="text-xs text-muted-foreground">{item.label}</span>
            </div>
            <p className={`text-lg font-semibold ${item.color}`}>{item.value}</p>
          </motion.div>
        );
      })}

      {!data && (
        <div className="col-span-2 flex flex-col items-center justify-center py-4 text-muted-foreground">
          <Shield className="h-6 w-6 mb-2 opacity-40" />
          <p className="text-xs">Risk metrics will appear after trades are executed</p>
        </div>
      )}
    </div>
  );
}

