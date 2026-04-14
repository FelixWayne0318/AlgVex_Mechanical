"use client";

import { Fragment, useState } from "react";
import { useRecentTrades } from "@/hooks/useTradeEvaluation";
import { GradeCard } from "./GradeCard";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, XCircle, ChevronDown, ChevronRight, Brain } from "lucide-react";

interface TradeTableProps {
  limit?: number;
}

function formatDuration(minutes: number): string {
  if (minutes < 60) {
    return `${Math.round(minutes)}m`;
  }
  const hours = Math.floor(minutes / 60);
  const mins = Math.round(minutes % 60);
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
}

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const exitTypeBadgeColors: Record<string, string> = {
  TAKE_PROFIT: "bg-green-500/10 text-green-500 border-green-500/30",
  STOP_LOSS: "bg-orange-500/10 text-orange-500 border-orange-500/30",
  MANUAL: "bg-blue-500/10 text-blue-500 border-blue-500/30",
  REVERSAL: "bg-purple-500/10 text-purple-500 border-purple-500/30",
};

const exitTypeLabels: Record<string, string> = {
  TAKE_PROFIT: "止盈",
  STOP_LOSS: "止损",
  MANUAL: "手动",
  REVERSAL: "反转",
};

const winningSideLabels: Record<string, string> = {
  bull: "多",
  bear: "空",
  BULL: "多",
  BEAR: "空",
  LONG: "多",
  SHORT: "空",
  long: "多",
  short: "空",
};

export function TradeTable({ limit = 20 }: TradeTableProps) {
  const { trades, isLoading, isError } = useRecentTrades(limit);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  const hasAnyReflection = trades.some((t) => t.reflection_snippet);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>最近交易评估</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground">加载中...</div>
        </CardContent>
      </Card>
    );
  }

  if (isError || trades.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>最近交易评估</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground">暂无交易数据</div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>最近 {trades.length} 笔交易评估</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-24">时间</TableHead>
                <TableHead className="w-16">Grade</TableHead>
                <TableHead className="w-28">R/R (计划→实际)</TableHead>
                <TableHead className="w-20">出场</TableHead>
                <TableHead className="w-24">MAE/MFE</TableHead>
                <TableHead className="w-24">持仓时长</TableHead>
                <TableHead className="w-20 text-center">方向</TableHead>
                <TableHead className="w-20">信心</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {trades.map((trade, idx) => {
                const isExpanded = expandedIdx === idx;
                const hasReflection = !!trade.reflection_snippet;
                return (
                  <Fragment key={idx}>
                    <TableRow
                      className={hasReflection ? "cursor-pointer hover:bg-muted/50" : ""}
                      onClick={() => hasReflection && setExpandedIdx(isExpanded ? null : idx)}
                    >
                      <TableCell className="font-mono text-xs">
                        <div className="flex items-center gap-1">
                          {hasAnyReflection && (
                            hasReflection ? (
                              isExpanded ? (
                                <ChevronDown className="h-3 w-3 text-muted-foreground shrink-0" />
                              ) : (
                                <ChevronRight className="h-3 w-3 text-muted-foreground shrink-0" />
                              )
                            ) : (
                              <span className="w-3 shrink-0" />
                            )
                          )}
                          {formatTimestamp(trade.timestamp)}
                        </div>
                      </TableCell>
                      <TableCell>
                        <GradeCard grade={trade.grade} size="sm" />
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        <div className="flex items-center gap-1">
                          <span className="text-muted-foreground">
                            {trade.planned_rr.toFixed(1)}
                          </span>
                          <span className="text-muted-foreground">→</span>
                          <span className={trade.actual_rr >= 0 ? "text-green-500" : "text-red-500"}>
                            {trade.actual_rr.toFixed(1)}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={exitTypeBadgeColors[trade.exit_type] || ""}
                        >
                          {exitTypeLabels[trade.exit_type] || trade.exit_type}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {trade.mae_pct || trade.mfe_pct ? (
                          <div className="flex items-center gap-1">
                            <span className="text-red-500">{(trade.mae_pct ?? 0).toFixed(1)}</span>
                            <span className="text-muted-foreground">/</span>
                            <span className="text-green-500">{(trade.mfe_pct ?? 0).toFixed(1)}</span>
                          </div>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-sm">
                        {formatDuration(trade.hold_duration_min)}
                      </TableCell>
                      <TableCell className="text-center">
                        {trade.direction_correct ? (
                          <CheckCircle2 className="h-4 w-4 text-green-500 inline" />
                        ) : (
                          <XCircle className="h-4 w-4 text-red-500 inline" />
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant={trade.confidence === "HIGH" ? "default" : "outline"}>
                          {trade.confidence}
                        </Badge>
                      </TableCell>
                    </TableRow>
                    {isExpanded && hasReflection && (
                      <TableRow className="bg-muted/30 hover:bg-muted/30">
                        <TableCell colSpan={8} className="py-3">
                          <div className="flex items-start gap-2 pl-4">
                            <Brain className="h-4 w-4 text-indigo-500 mt-0.5 shrink-0" />
                            <div className="text-sm space-y-1">
                              <p className="text-foreground/90">{trade.reflection_snippet}</p>
                              {trade.winning_side && (
                                <p className="text-xs text-muted-foreground">
                                  胜出分析师: <span className={trade.winning_side.toLowerCase() === "bull" ? "text-green-500" : "text-red-500"}>
                                    {winningSideLabels[trade.winning_side] || trade.winning_side}
                                  </span>
                                </p>
                              )}
                            </div>
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}
