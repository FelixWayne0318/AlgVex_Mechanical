"use client";

import { useTradeEvaluationSummary, useRecentTrades } from "@/hooks/useTradeEvaluation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { GradeCard } from "./GradeCard";
import { TrendingUp } from "lucide-react";

interface GradePieChartProps {
  limit?: number;
  days?: number;
}

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

export function GradePieChart({ limit = 5, days = 30 }: GradePieChartProps) {
  const { summary, isLoading: summaryLoading } = useTradeEvaluationSummary(days);
  const { trades, isLoading: tradesLoading } = useRecentTrades(limit);

  const isLoading = summaryLoading || tradesLoading;

  if (isLoading) {
    return (
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <TrendingUp className="h-4 w-4" />
            最近交易质量
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground">加载中...</div>
        </CardContent>
      </Card>
    );
  }

  if (!summary || !trades.length) {
    return (
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <TrendingUp className="h-4 w-4" />
            最近交易质量
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground">暂无数据</div>
        </CardContent>
      </Card>
    );
  }

  const gradeData = [
    { grade: "A+", count: summary.grade_distribution["A+"] || 0, color: "bg-emerald-500" },
    { grade: "A", count: summary.grade_distribution.A || 0, color: "bg-green-500" },
    { grade: "B", count: summary.grade_distribution.B || 0, color: "bg-lime-500" },
    { grade: "C", count: summary.grade_distribution.C || 0, color: "bg-yellow-500" },
    { grade: "D", count: summary.grade_distribution.D || 0, color: "bg-orange-500" },
    { grade: "F", count: summary.grade_distribution.F || 0, color: "bg-red-500" },
  ].filter(item => item.count > 0);

  const total = summary.total_evaluated;

  return (
    <Card className="border-border/50">
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <TrendingUp className="h-4 w-4" />
          最近交易质量
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Grade 分布 */}
        <div className="space-y-2">
          <div className="text-xs text-muted-foreground">Grade 分布</div>
          <div className="flex gap-1">
            {gradeData.map(({ grade, count, color }) => {
              const percent = (count / total) * 100;
              return (
                <div
                  key={grade}
                  className={`${color} h-2 rounded-sm transition-all hover:opacity-80`}
                  style={{ width: `${percent}%` }}
                  title={`${grade}: ${count} (${Math.round(percent)}%)`}
                />
              );
            })}
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            {gradeData.map(({ grade, count }) => (
              <div key={grade} className="flex items-center gap-1">
                <GradeCard grade={grade} size="sm" />
                <span className="text-muted-foreground">{count}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 最近 N 笔 */}
        <div className="space-y-2">
          <div className="text-xs text-muted-foreground">最近 {limit} 笔</div>
          <div className="space-y-1.5">
            {trades.slice(0, limit).map((trade, idx) => (
              <div key={idx} className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2">
                  <GradeCard grade={trade.grade} size="sm" />
                  <span className="text-xs text-muted-foreground">
                    {formatTimestamp(trade.timestamp)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-mono ${trade.actual_rr >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                    {trade.actual_rr.toFixed(1)} R/R
                  </span>
                  {trade.exit_type === "STOP_LOSS" && (
                    <span className="text-xs text-orange-500">SL</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
