"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTradeEvaluationSummary } from "@/hooks/useTradeEvaluation";
import { Award, TrendingUp, Target, ShieldCheck, Brain, BookOpen } from "lucide-react";
import { Progress } from "@/components/ui/progress";

interface TradeQualityCardProps {
  days?: number;
}

export function TradeQualityCard({ days = 30 }: TradeQualityCardProps) {
  const { summary, isLoading, isError } = useTradeEvaluationSummary(days);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Award className="h-5 w-5" />
            交易质量评分
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground">加载中...</div>
        </CardContent>
      </Card>
    );
  }

  if (isError || !summary || summary.total_evaluated === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Award className="h-5 w-5" />
            交易质量评分
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground">暂无评估数据</div>
        </CardContent>
      </Card>
    );
  }

  const gradeAB = (summary.grade_distribution["A+"] || 0) + (summary.grade_distribution.A || 0) + (summary.grade_distribution.B || 0);
  const gradeABPercent = Math.round((gradeAB / summary.total_evaluated) * 100);

  const stopLossCount = summary.exit_type_distribution.STOP_LOSS || 0;
  const totalLosses = (summary.grade_distribution.D || 0) + (summary.grade_distribution.F || 0);
  const stopLossDiscipline = totalLosses > 0 ? Math.round(((totalLosses - (summary.grade_distribution.F || 0)) / totalLosses) * 100) : 100;

  return (
    <Card className="border-border/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-lg">
          <Award className="h-5 w-5 text-primary" />
          交易质量评分
          <span className="text-sm text-muted-foreground font-normal">
            (最近 {days} 天)
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Grade A/B 占比 */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <div className="flex items-center gap-2">
              <Award className="h-4 w-4 text-emerald-500" />
              <span>Grade A/B</span>
            </div>
            <span className="font-semibold">
              {gradeABPercent}% ({gradeAB}/{summary.total_evaluated} 笔)
            </span>
          </div>
          <Progress value={gradeABPercent} className="h-2" />
        </div>

        {/* 平均 R/R */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm">
            <TrendingUp className="h-4 w-4 text-blue-500" />
            <span>平均 R/R</span>
          </div>
          <span className="font-semibold">
            {summary.avg_winning_rr.toFixed(2)}:1
          </span>
        </div>

        {/* 执行质量 */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm">
            <Target className="h-4 w-4 text-purple-500" />
            <span>执行质量</span>
          </div>
          <span className="font-semibold">
            {Math.round(summary.avg_execution_quality * 100)}%
          </span>
        </div>

        {/* 止损纪律 */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm">
            <ShieldCheck className="h-4 w-4 text-orange-500" />
            <span>止损纪律</span>
          </div>
          <span className="font-semibold">{stopLossDiscipline}%</span>
        </div>

        {/* v11.5: MAE/MFE stats */}
        {(summary.avg_mae_pct !== undefined || summary.avg_mfe_pct !== undefined) && (
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm">
              <TrendingUp className="h-4 w-4 text-cyan-500" />
              <span>MAE / MFE</span>
            </div>
            <span className="font-semibold text-sm">
              <span className="text-red-500">{(summary.avg_mae_pct ?? 0).toFixed(1)}%</span>
              {" / "}
              <span className="text-green-500">{(summary.avg_mfe_pct ?? 0).toFixed(1)}%</span>
            </span>
          </div>
        )}

        {/* Trading memory stats */}
        {summary.reflection_count !== undefined && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm">
                <Brain className="h-4 w-4 text-indigo-500" />
                <span>交易记忆</span>
              </div>
              <span className="font-semibold">
                {summary.total_evaluated} 笔
              </span>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm">
                <BookOpen className="h-4 w-4 text-amber-500" />
                <span>反思覆盖率</span>
              </div>
              <span className="font-semibold">
                {summary.reflection_coverage_pct ?? 0}%
                <span className="text-xs text-muted-foreground ml-1">
                  ({summary.reflection_count}/{summary.total_evaluated})
                </span>
              </span>
            </div>
          </div>
        )}

        {/* 底部统计 */}
        <div className="pt-2 border-t border-border/50">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>胜率: {Math.round(summary.direction_accuracy)}%</span>
            <span>平均持仓: {Math.round(summary.avg_hold_duration_min / 60)}h</span>
            {summary.counter_trend_pct !== undefined && summary.counter_trend_pct > 0 && (
              <span>逆势: {Math.round(summary.counter_trend_pct)}%</span>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
