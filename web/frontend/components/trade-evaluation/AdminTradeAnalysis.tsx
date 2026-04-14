"use client";

import { useState } from "react";
import { useAdminSummary, useFullTrades } from "@/hooks/useTradeEvaluation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { GradeCard } from "./GradeCard";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Download, FileJson, FileSpreadsheet, AlertCircle } from "lucide-react";

const EMPTY_STATS = { total: 0, wins: 0, accuracy: 0 };

export function AdminTradeAnalysis() {
  const [days, setDays] = useState(0); // 0 = all time
  const [expandedReflection, setExpandedReflection] = useState<number | null>(null);
  const { summary, isLoading: summaryLoading, isError: summaryError } = useAdminSummary(days);
  const { trades, isLoading: tradesLoading, isError: tradesError } = useFullTrades(100);

  const handleExport = async (format: "json" | "csv") => {
    const url = `/api/public/trade-evaluation/export?format=${format}&days=${days}`;
    const response = await fetch(url);
    if (!response.ok) return;
    const data = await response.json();

    const filename = `trade_evaluation_${Date.now()}.${format}`;
    const content = format === "json"
      ? JSON.stringify(data.data, null, 2)
      : convertToCSV(data.data);

    const blob = new Blob([content], { type: format === "json" ? "application/json" : "text/csv" });
    const downloadUrl = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = filename;
    a.click();
  };

  const convertToCSV = (data: any[]) => {
    if (!data || !data.length) return "";
    const headers = Object.keys(data[0]);
    const rows = data.map(row => headers.map(h => JSON.stringify(row[h] ?? "")).join(","));
    return [headers.join(","), ...rows].join("\n");
  };

  if (summaryLoading || tradesLoading) {
    return <div className="text-sm text-muted-foreground p-4">Loading...</div>;
  }

  if (summaryError || tradesError) {
    return (
      <div className="flex items-center gap-2 p-4 text-sm text-red-400 bg-red-500/10 rounded-lg border border-red-500/20">
        <AlertCircle className="h-4 w-4 flex-shrink-0" />
        <span>Failed to load trade evaluation data. Please check your authentication.</span>
      </div>
    );
  }

  if (!summary || summary.total_evaluated === 0) {
    return <div className="text-sm text-muted-foreground p-4">No evaluation data available yet.</div>;
  }

  return (
    <div className="space-y-6">
      {/* Export Buttons */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <h2 className="text-lg sm:text-2xl font-bold">Trade Quality Analysis</h2>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => handleExport("csv")}>
            <FileSpreadsheet className="h-4 w-4 mr-1 sm:mr-2" />
            CSV
          </Button>
          <Button variant="outline" size="sm" onClick={() => handleExport("json")}>
            <FileJson className="h-4 w-4 mr-1 sm:mr-2" />
            JSON
          </Button>
        </div>
      </div>

      {/* Confidence Accuracy Table */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle>Confidence Level Accuracy</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto -mx-6 px-6">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Level</TableHead>
                <TableHead className="text-right">Trades</TableHead>
                <TableHead className="text-right">Wins</TableHead>
                <TableHead className="text-right">Win Rate</TableHead>
                <TableHead className="text-right">Avg R/R</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {["HIGH", "MEDIUM", "LOW"].map((level) => {
                const stats = summary.confidence_accuracy?.[level as keyof typeof summary.confidence_accuracy] || EMPTY_STATS;
                const levelTrades = trades.filter(t => t.confidence === level && t.direction_correct);
                const avgRR = levelTrades.length > 0
                  ? levelTrades.reduce((acc, t) => acc + (t.actual_rr || 0), 0) / levelTrades.length
                  : 0;

                return (
                  <TableRow key={level}>
                    <TableCell className="font-medium">{level}</TableCell>
                    <TableCell className="text-right">{stats.total}</TableCell>
                    <TableCell className="text-right">{stats.wins}</TableCell>
                    <TableCell className="text-right">
                      <span className={stats.accuracy >= 60 ? "text-green-500" : stats.accuracy >= 50 ? "text-yellow-500" : "text-red-500"}>
                        {stats.accuracy.toFixed(1)}%
                      </span>
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {avgRR.toFixed(2)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          </div>
        </CardContent>
      </Card>

      {/* Exit Type Distribution */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle>Exit Type Distribution</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {Object.entries(summary.exit_type_distribution || {}).map(([type, count]) => {
              const percent = summary.total_evaluated > 0 ? ((count as number) / summary.total_evaluated) * 100 : 0;
              return (
                <div key={type} className="space-y-1">
                  <div className="flex items-center justify-between text-sm">
                    <span>{type}</span>
                    <span className="font-mono">{count as number} ({percent.toFixed(1)}%)</span>
                  </div>
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div
                      className={`h-full ${
                        type === "TAKE_PROFIT" ? "bg-green-500" :
                        type === "STOP_LOSS" ? "bg-orange-500" :
                        type === "MANUAL" ? "bg-blue-500" : "bg-purple-500"
                      }`}
                      style={{ width: `${percent}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* R/R Distribution */}
      {trades.length > 0 && (
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle>Actual R/R Distribution</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {[
                { label: "3.0+", min: 3.0, max: Infinity },
                { label: "2.0-3.0", min: 2.0, max: 3.0 },
                { label: "1.0-2.0", min: 1.0, max: 2.0 },
                { label: "0.0-1.0", min: 0.0, max: 1.0 },
                { label: "Negative", min: -Infinity, max: 0.0 },
              ].map(({ label, min, max }) => {
                const count = trades.filter(t => (t.actual_rr || 0) >= min && (t.actual_rr || 0) < max).length;
                const percent = trades.length > 0 ? (count / trades.length) * 100 : 0;
                return (
                  <div key={label} className="flex items-center gap-2 sm:gap-4">
                    <span className="text-xs sm:text-sm w-16 sm:w-20 flex-shrink-0">{label}</span>
                    <div className="flex-1 h-6 bg-muted rounded overflow-hidden">
                      <div
                        className="h-full bg-primary flex items-center px-2"
                        style={{ width: `${percent}%` }}
                      >
                        {percent > 10 && (
                          <span className="text-xs text-primary-foreground font-mono">
                            {count}
                          </span>
                        )}
                      </div>
                    </div>
                    {percent <= 10 && (
                      <span className="text-xs text-muted-foreground w-8">({count})</span>
                    )}
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Full Trades Table (with prices) */}
      {trades.length > 0 && (
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle>Trade History (Last 100)</CardTitle>
          </CardHeader>
          <CardContent className="px-0 sm:px-6">
            <div className="rounded-md border max-h-[600px] overflow-auto">
              <Table className="min-w-[900px]">
                <TableHeader className="sticky top-0 bg-background z-10">
                  <TableRow>
                    <TableHead className="whitespace-nowrap">Time</TableHead>
                    <TableHead className="whitespace-nowrap">Grade</TableHead>
                    <TableHead className="whitespace-nowrap">Dir</TableHead>
                    <TableHead className="text-right whitespace-nowrap">Entry</TableHead>
                    <TableHead className="text-right whitespace-nowrap">Exit</TableHead>
                    <TableHead className="text-right whitespace-nowrap">SL</TableHead>
                    <TableHead className="text-right whitespace-nowrap">TP</TableHead>
                    <TableHead className="text-right whitespace-nowrap">R/R</TableHead>
                    <TableHead className="text-right whitespace-nowrap">MAE/MFE</TableHead>
                    <TableHead className="text-right whitespace-nowrap">Ctx</TableHead>
                    <TableHead className="text-right whitespace-nowrap">PnL</TableHead>
                    <TableHead className="max-w-[160px]">Lesson</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {trades.slice(0, 100).map((trade, idx) => {
                    const ts = trade.timestamp ? new Date(trade.timestamp) : null;
                    const timeStr = ts && !isNaN(ts.getTime())
                      ? ts.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                      : '--';
                    return (
                      <TableRow key={idx}>
                        <TableCell className="font-mono text-xs">{timeStr}</TableCell>
                        <TableCell>
                          <GradeCard grade={trade.grade || "?"} size="sm" />
                        </TableCell>
                        <TableCell className="font-medium">
                          {trade.decision || "N/A"}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          ${trade.entry_price?.toLocaleString() || "--"}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          ${trade.exit_price?.toLocaleString() || "--"}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-red-500">
                          ${trade.planned_sl?.toLocaleString() || "--"}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-green-500">
                          ${trade.planned_tp?.toLocaleString() || "--"}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {(trade.planned_rr ?? 0).toFixed(1)} → {(trade.actual_rr ?? 0).toFixed(1)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {trade.mae_pct || trade.mfe_pct ? (
                            <>
                              <span className="text-red-500">{(trade.mae_pct ?? 0).toFixed(1)}</span>
                              {" / "}
                              <span className="text-green-500">{(trade.mfe_pct ?? 0).toFixed(1)}</span>
                            </>
                          ) : "—"}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-muted-foreground">
                          <div className="flex flex-col items-end gap-0.5">
                            {trade.sl_atr_multiplier && (
                              <span>{trade.sl_atr_multiplier}×ATR</span>
                            )}
                            <span>
                              {trade.trend_direction && <>{trade.trend_direction?.slice(0, 2)}</>}
                              {trade.adx ? `(${Math.round(trade.adx)})` : ""}
                              {trade.is_counter_trend && <span className="ml-1 text-yellow-500">CT</span>}
                            </span>
                            {trade.risk_appetite && trade.risk_appetite !== "NORMAL" && (
                              <span className={trade.risk_appetite === "AGGRESSIVE" ? "text-red-400" : "text-blue-400"}>
                                {trade.risk_appetite?.slice(0, 3)}
                              </span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className={`text-right font-mono text-xs ${(trade.pnl || 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                          {trade.pnl !== undefined ? (trade.pnl >= 0 ? '+' : '') + trade.pnl.toFixed(2) + '%' : '--'}
                        </TableCell>
                        <TableCell className="max-w-[200px] text-xs text-muted-foreground">
                          {trade.reflection ? (
                            <div
                              className={`cursor-pointer hover:text-foreground/70 transition-colors ${expandedReflection === idx ? 'whitespace-normal break-words' : 'truncate'}`}
                              onClick={() => setExpandedReflection(expandedReflection === idx ? null : idx)}
                            >
                              {trade.winning_side && (
                                <span className={`mr-1 ${trade.winning_side === "BULL" ? "text-green-500" : "text-red-500"}`}>
                                  {trade.winning_side === "BULL" ? "🐂" : "🐻"}
                                </span>
                              )}
                              {trade.reflection}
                            </div>
                          ) : (
                            <span className="text-muted-foreground/50">—</span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
