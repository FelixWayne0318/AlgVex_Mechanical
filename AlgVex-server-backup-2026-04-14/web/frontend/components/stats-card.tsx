"use client";

import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { TrendingUp, TrendingDown, Activity, Target, AlertTriangle, BarChart3 } from "lucide-react";

interface StatsCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  type?: "profit" | "loss" | "neutral" | "status";
  icon?: "trending" | "target" | "alert" | "activity" | "chart";
}

const iconMap = {
  trending: TrendingUp,
  target: Target,
  alert: AlertTriangle,
  activity: Activity,
  chart: BarChart3,
};

export function StatsCard({
  title,
  value,
  subtitle,
  type = "neutral",
  icon = "chart",
}: StatsCardProps) {
  const Icon = iconMap[icon];
  const isProfit = type === "profit";
  const isLoss = type === "loss";
  const isStatus = type === "status";

  return (
    <Card className={cn(
      "stat-card border-border/50 overflow-hidden",
      isProfit && "border-l-2 border-l-[hsl(var(--profit))]",
      isLoss && "border-l-2 border-l-[hsl(var(--loss))]",
    )}>
      <CardContent className="p-6">
        <div className="flex items-start justify-between">
          <div className="space-y-1">
            <p className="text-sm text-muted-foreground">{title}</p>
            <p
              className={cn(
                "text-3xl font-bold tracking-tight",
                isProfit && "text-[hsl(var(--profit))]",
                isLoss && "text-[hsl(var(--loss))]",
              )}
            >
              {value}
            </p>
            {subtitle && (
              <p className="text-xs text-muted-foreground">{subtitle}</p>
            )}
          </div>
          <div
            className={cn(
              "p-2 rounded-lg",
              isProfit && "bg-[hsl(var(--profit))]/10",
              isLoss && "bg-[hsl(var(--loss))]/10",
              type === "neutral" && "bg-muted",
              isStatus && "bg-primary/10",
            )}
          >
            {isProfit ? (
              <TrendingUp className="h-5 w-5 text-[hsl(var(--profit))]" />
            ) : isLoss ? (
              <TrendingDown className="h-5 w-5 text-[hsl(var(--loss))]" />
            ) : (
              <Icon
                className={cn(
                  "h-5 w-5",
                  isStatus ? "text-primary" : "text-muted-foreground"
                )}
              />
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
