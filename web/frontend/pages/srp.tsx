"use client";

import { useState } from "react";
import { useRouter } from "next/router";
import Head from "next/head";
import useSWR from "swr";

import { Header } from "@/components/layout/header";
import { Footer } from "@/components/layout/footer";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BacktestEquityChart } from "@/components/charts/backtest-equity-chart";
import { useTranslation, type Locale } from "@/lib/i18n";
import {
  Activity, TrendingUp, BarChart3, Settings, Play,
  DollarSign, Target, Shield, Layers, RefreshCw, CheckCircle, XCircle,
  Wifi, WifiOff, Info, ChevronDown, ChevronUp, LineChart, List,
} from "lucide-react";

export default function SRPPage() {
  const router = useRouter();
  const locale = (router.locale || "en") as Locale;
  const { t } = useTranslation(locale);
  const [isRunning, setIsRunning] = useState(false);
  const [showParams, setShowParams] = useState(false);
  const [activeTab, setActiveTab] = useState<"summary" | "equity" | "trades">("summary");
  const [showAllTrades, setShowAllTrades] = useState(false);

  // Backtest parameters
  const [bp, setBp] = useState({
    days: 456,
    balance: 1500,
    srp_pct: "",
    dca_spacing: "",
    dca_mult: "",
    max_dca_count: "",
    tp_pct: "",
    sl_pct: "",
  });

  // Detailed result with equity curve + trades
  const [detailed, setDetailed] = useState<any>(null);

  const { data: params } = useSWR("/api/public/srp/parameters", { refreshInterval: 60000 });
  const { data: state } = useSWR("/api/public/srp/state", { refreshInterval: 10000 });
  const { data: backtest, mutate: mutateBacktest } = useSWR("/api/public/srp/backtest", { refreshInterval: 300000 });
  const { data: service } = useSWR("/api/public/srp/service-status", { refreshInterval: 15000 });
  const { data: walkforward } = useSWR("/api/public/srp/walkforward", { refreshInterval: 300000 });
  const { data: parity } = useSWR("/api/public/srp/parity", { refreshInterval: 300000 });

  const srp = params?.srp || {};

  const runBacktest = async () => {
    setIsRunning(true);
    try {
      const q = new URLSearchParams();
      q.set("days", String(bp.days));
      q.set("balance", String(bp.balance));
      if (bp.srp_pct) q.set("srp_pct", bp.srp_pct);
      if (bp.dca_spacing) q.set("dca_spacing", bp.dca_spacing);
      if (bp.dca_mult) q.set("dca_mult", bp.dca_mult);
      if (bp.max_dca_count) q.set("max_dca_count", bp.max_dca_count);
      if (bp.tp_pct) q.set("tp_pct", bp.tp_pct);
      if (bp.sl_pct) q.set("sl_pct", bp.sl_pct);
      q.set("include_equity_curve", "true");
      q.set("include_trades", "true");

      const res = await fetch(`/api/admin/srp/backtest/run?${q.toString()}`, { method: "POST" });
      const data = await res.json();
      setDetailed(data);
      mutateBacktest(data);
    } catch (e) {
      console.error("Backtest failed:", e);
    } finally {
      setIsRunning(false);
    }
  };

  const resetParams = () => {
    setBp({ days: 456, balance: 1500, srp_pct: "", dca_spacing: "", dca_mult: "", max_dca_count: "", tp_pct: "", sl_pct: "" });
  };

  const zh = locale === "zh";
  const fmtPct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
  const fmtUsd = (v: number) => `$${v >= 0 ? "+" : ""}${v.toFixed(2)}`;

  // Use detailed result if available, otherwise cached
  const bt = detailed || backtest;

  return (
    <>
      <Head><title>SRP v5.0 | AlgVex</title></Head>

      <div className="min-h-screen flex flex-col bg-background">
        <Header locale={locale} t={t} />

        <main className="flex-1 container mx-auto px-4 py-8 pt-24 space-y-6">
          {/* Title + Service Status */}
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold">SRP v5.0</h1>
              <p className="text-muted-foreground mt-1">
                {zh ? "VWMA + RSI-MFI 通道 · DCA 加仓 · Virtual DCA · 复利仓位" : "VWMA + RSI-MFI Channel · DCA Averaging · Virtual DCA · Compound Sizing"}
              </p>
            </div>
            <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-sm ${service?.running ? "bg-green-500/10 text-green-500" : "bg-red-500/10 text-red-500"}`}>
              {service?.running ? <Wifi className="h-4 w-4" /> : <WifiOff className="h-4 w-4" />}
              {service?.running ? "Running" : "Stopped"}
            </div>
          </div>

          {/* Parameters */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Settings className="h-5 w-5" />
                {zh ? "策略参数 (Pine v5.0)" : "Strategy Parameters (Pine v5.0)"}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
                <ParamItem label="SRP Band" value={`${srp.srp_pct || 1.0}%`} />
                <ParamItem label="VWMA" value={srp.vwma_length || 14} />
                <ParamItem label="RSI-MFI <" value={srp.rsi_mfi_below || 55} />
                <ParamItem label="TP" value={`${(srp.mintp || 0.025) * 100}%`} />
                <ParamItem label="SL" value={`${(srp.max_portfolio_loss_pct || 0.06) * 100}%`} />
                <ParamItem label="DCA Spacing" value={`${srp.dca_min_change_pct || 3.0}%`} />
                <ParamItem label="DCA Mult" value={`${srp.dca_multiplier || 1.5}×`} />
                <ParamItem label="DCA Count" value={srp.max_dca_count || 4} />
                <ParamItem label={zh ? "仓位模式" : "Sizing"} value={`${srp.base_order_pct || 10}% ${zh ? "复利" : "Compound"}`} />
                <ParamItem label={zh ? "方向" : "Direction"} value={zh ? "仅开多" : "Long Only"} />
                <ParamItem label="Timeframe" value={srp.timeframe || "30m"} />
                <ParamItem label="DCA Type" value="Volume ×" />
              </div>
            </CardContent>
          </Card>

          {/* Position Status */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Layers className="h-5 w-5" />
                {zh ? "当前仓位" : "Position Status"}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {state?.has_position ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <StatBox label={zh ? "方向" : "Side"} value={zh ? "多仓" : "LONG"} icon={<TrendingUp className="h-4 w-4 text-profit" />} />
                    <StatBox label={zh ? "均价" : "Avg"} value={`$${state.avg_price?.toFixed(2)}`} icon={<Target className="h-4 w-4" />} />
                    <StatBox label={zh ? "数量" : "Qty"} value={`${state.total_quantity?.toFixed(6)} BTC`} icon={<DollarSign className="h-4 w-4" />} />
                    <StatBox label="DCA" value={`${state.dca_count} / ${(srp.max_dca_count || 4) + 1}`} icon={<Layers className="h-4 w-4" />} />
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <StatBox label="Virtual Avg" value={state.v_avg > 0 ? `$${state.v_avg?.toFixed(1)}` : "—"} icon={<Activity className="h-4 w-4 text-cyan-400" />} />
                    <StatBox label="TP Target" value={state.tp_target > 0 ? `$${state.tp_target?.toFixed(1)}` : "—"} icon={<Target className="h-4 w-4 text-green-400" />} />
                    <StatBox label="Deal Base" value={`$${state.deal_base?.toFixed(0)}`} icon={<DollarSign className="h-4 w-4" />} />
                    <StatBox label={`Deal #${state.dealcount || 0}`} value={state.saved_at ? new Date(state.saved_at).toLocaleTimeString() : "—"} icon={<Activity className="h-4 w-4" />} />
                  </div>
                  {state.dca_entries?.length > 0 && (
                    <div className="mt-3">
                      <h4 className="text-sm font-medium mb-2">{zh ? "DCA 入场记录" : "DCA Entries"}</h4>
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead><tr className="border-b text-muted-foreground">
                            <th className="text-left py-2">Layer</th>
                            <th className="text-right py-2">{zh ? "价格" : "Price"}</th>
                            <th className="text-right py-2">{zh ? "数量" : "Qty"}</th>
                          </tr></thead>
                          <tbody>
                            {state.dca_entries.map((e: any, i: number) => (
                              <tr key={i} className="border-b border-border/50">
                                <td className="py-1.5">{e.label || `DCA#${i}`}</td>
                                <td className="text-right">${e.price?.toFixed(2)}</td>
                                <td className="text-right">{e.quantity?.toFixed(6)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-center py-8 text-muted-foreground">
                  <Shield className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  {zh ? "当前无持仓 — 等待入场信号" : "No open position — waiting for entry signal"}
                  {state?.dealcount > 0 && (
                    <p className="text-xs mt-2">{zh ? `历史总 Deal 数: ${state.dealcount}` : `Total deals: ${state.dealcount}`}</p>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          {/* ================================================================ */}
          {/* Backtest (NT BacktestEngine) — Enhanced */}
          {/* ================================================================ */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between flex-wrap gap-2">
                <CardTitle className="flex items-center gap-2">
                  <BarChart3 className="h-5 w-5" />
                  {zh ? "回测 (NautilusTrader)" : "Backtest (NautilusTrader Engine)"}
                  {bt?.file_date && (
                    <span className="text-xs text-muted-foreground font-normal ml-2">
                      {new Date(bt.file_date).toLocaleString()}
                    </span>
                  )}
                </CardTitle>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={() => setShowParams(!showParams)}>
                    <Settings className="h-4 w-4 mr-1" />
                    {zh ? "参数" : "Params"}
                    {showParams ? <ChevronUp className="h-3 w-3 ml-1" /> : <ChevronDown className="h-3 w-3 ml-1" />}
                  </Button>
                  <Button size="sm" onClick={runBacktest} disabled={isRunning}>
                    {isRunning ? <RefreshCw className="h-4 w-4 animate-spin mr-1" /> : <Play className="h-4 w-4 mr-1" />}
                    {isRunning ? (zh ? "运行中..." : "Running...") : (zh ? "运行回测" : "Run Backtest")}
                  </Button>
                </div>
              </div>

              {/* Collapsible Parameter Form */}
              {showParams && (
                <div className="mt-4 p-4 rounded-lg bg-muted/20 border border-border/50">
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <InputField label={zh ? "天数" : "Days"} value={bp.days} type="select"
                      options={[{ v: 30, l: "30D" }, { v: 90, l: "90D" }, { v: 180, l: "180D" }, { v: 365, l: "365D" }, { v: 456, l: "456D" }]}
                      onChange={(v) => setBp({ ...bp, days: Number(v) })} />
                    <InputField label={zh ? "初始资金" : "Balance"} value={bp.balance} type="number" placeholder="1500"
                      onChange={(v) => setBp({ ...bp, balance: Number(v) || 1500 })} />
                    <InputField label="SRP Band %" value={bp.srp_pct} type="text" placeholder={String(srp.srp_pct || 1.0)}
                      onChange={(v) => setBp({ ...bp, srp_pct: v })} />
                    <InputField label="DCA Spacing %" value={bp.dca_spacing} type="text" placeholder={String(srp.dca_min_change_pct || 3.0)}
                      onChange={(v) => setBp({ ...bp, dca_spacing: v })} />
                    <InputField label="DCA Mult" value={bp.dca_mult} type="text" placeholder={String(srp.dca_multiplier || 1.5)}
                      onChange={(v) => setBp({ ...bp, dca_mult: v })} />
                    <InputField label="DCA Count" value={bp.max_dca_count} type="text" placeholder={String(srp.max_dca_count || 4)}
                      onChange={(v) => setBp({ ...bp, max_dca_count: v })} />
                    <InputField label="TP %" value={bp.tp_pct} type="text" placeholder={String((srp.mintp || 0.025) * 100)}
                      onChange={(v) => setBp({ ...bp, tp_pct: v })} />
                    <InputField label="SL %" value={bp.sl_pct} type="text" placeholder={String((srp.max_portfolio_loss_pct || 0.06) * 100)}
                      onChange={(v) => setBp({ ...bp, sl_pct: v })} />
                  </div>
                  <div className="mt-3 flex justify-end">
                    <Button variant="ghost" size="sm" onClick={resetParams}>
                      <RefreshCw className="h-3 w-3 mr-1" />
                      {zh ? "恢复默认" : "Reset Defaults"}
                    </Button>
                  </div>
                </div>
              )}
            </CardHeader>

            <CardContent>
              {bt?.error ? (
                <div className="text-center py-8 text-muted-foreground">
                  <XCircle className="h-8 w-8 mx-auto mb-2 text-loss" />
                  <p>{bt.error}</p>
                </div>
              ) : bt?.adjusted_return_pct !== undefined ? (
                <div className="space-y-4">
                  {/* Tab Navigation */}
                  <div className="flex gap-1 border-b border-border">
                    <TabBtn active={activeTab === "summary"} onClick={() => setActiveTab("summary")}
                      icon={<BarChart3 className="h-4 w-4" />} label={zh ? "概览" : "Summary"} />
                    <TabBtn active={activeTab === "equity"} onClick={() => setActiveTab("equity")}
                      icon={<LineChart className="h-4 w-4" />} label={zh ? "权益曲线" : "Equity Curve"} />
                    <TabBtn active={activeTab === "trades"} onClick={() => setActiveTab("trades")}
                      icon={<List className="h-4 w-4" />} label={zh ? "交易记录" : "Trades"} />
                  </div>

                  {/* Summary Tab */}
                  {activeTab === "summary" && (
                    <div className="space-y-4">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <BigStat label="Net PnL" value={fmtUsd(bt.adjusted_pnl)} positive={bt.adjusted_pnl >= 0} />
                        <BigStat label="Return" value={fmtPct(bt.adjusted_return_pct)} positive={bt.adjusted_return_pct >= 0} />
                        <BigStat label="Win Rate" value={`${bt.win_rate_pct}%`} positive={bt.win_rate_pct >= 50} />
                        <BigStat label="Profit Factor" value={typeof bt.profit_factor === "string" ? bt.profit_factor : bt.profit_factor?.toFixed(2)} positive={(bt.profit_factor || 0) >= 1} />
                      </div>
                      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
                        <ParamItem label={zh ? "交易数" : "Trades"} value={`${bt.wins || 0}W / ${bt.losses || 0}L`} />
                        <ParamItem label="True MDD" value={`${bt.true_mdd_pct}%`} />
                        <ParamItem label="Float MDD" value={`${bt.max_unrealized_dd_pct}%`} />
                        <ParamItem label="Sharpe" value={bt.sharpe_ratio} />
                        <ParamItem label="Funding" value={fmtUsd(bt.funding_cost || 0)} />
                        <ParamItem label="Buy & Hold" value={fmtPct(bt.buy_hold_return_pct || 0)} />
                      </div>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <ParamItem label={zh ? "毛利润" : "Gross Profit"} value={fmtUsd(bt.gross_profit)} />
                        <ParamItem label={zh ? "毛亏损" : "Gross Loss"} value={`$${bt.gross_loss?.toFixed(2)}`} />
                        <ParamItem label={zh ? "均盈亏" : "Avg PnL"} value={fmtUsd(bt.avg_pnl || 0)} />
                        <ParamItem label={zh ? "K线数" : "Bars"} value={bt.bar_count} />
                      </div>
                      {bt.period && (
                        <div className="text-xs text-muted-foreground text-center">{bt.period}</div>
                      )}
                    </div>
                  )}

                  {/* Equity Curve Tab */}
                  {activeTab === "equity" && (
                    <div>
                      {bt.equity_curve_sampled?.length > 0 ? (
                        <div className="space-y-2">
                          <div className="flex items-center justify-between text-sm text-muted-foreground">
                            <span>{zh ? "权益曲线 + 回撤" : "Equity Curve + Drawdown"}</span>
                            <span>{bt.equity_curve_sampled.length} {zh ? "个数据点" : "data points"}</span>
                          </div>
                          <BacktestEquityChart
                            equityData={bt.equity_curve_sampled}
                            drawdownData={bt.drawdown_curve}
                            height={400}
                          />
                          <div className="grid grid-cols-3 gap-3 mt-2">
                            <ParamItem label={zh ? "起始权益" : "Start"} value={`$${bt.equity_curve_sampled[0]?.v?.toFixed(2)}`} />
                            <ParamItem label={zh ? "最终权益" : "End"} value={`$${bt.equity_curve_sampled[bt.equity_curve_sampled.length - 1]?.v?.toFixed(2)}`} />
                            <ParamItem label={zh ? "最大回撤" : "Max DD"} value={`${bt.true_mdd_pct}%`} />
                          </div>
                        </div>
                      ) : (
                        <div className="text-center py-12 text-muted-foreground">
                          <LineChart className="h-10 w-10 mx-auto mb-3 opacity-50" />
                          <p>{zh ? "运行回测查看权益曲线" : "Run backtest to see equity curve"}</p>
                          <p className="text-xs mt-1">{zh ? "点击上方 \"运行回测\" 按钮" : "Click \"Run Backtest\" above"}</p>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Trades Tab */}
                  {activeTab === "trades" && (
                    <div>
                      {bt.trades?.length > 0 ? (
                        <div className="space-y-3">
                          <div className="flex items-center justify-between text-sm">
                            <span className="text-muted-foreground">
                              {zh ? `共 ${bt.trades.length} 笔交易` : `${bt.trades.length} trades total`}
                              {" · "}
                              <span className="text-green-500">{bt.wins}W</span>
                              {" / "}
                              <span className="text-red-500">{bt.losses}L</span>
                            </span>
                            {bt.trades.length > 20 && (
                              <Button variant="ghost" size="sm" onClick={() => setShowAllTrades(!showAllTrades)}>
                                {showAllTrades
                                  ? (zh ? "仅显示前 20" : "Show first 20")
                                  : (zh ? `显示全部 ${bt.trades.length}` : `Show all ${bt.trades.length}`)}
                              </Button>
                            )}
                          </div>
                          <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                              <thead>
                                <tr className="border-b text-muted-foreground text-xs">
                                  <th className="text-left py-2 px-1">#</th>
                                  <th className="text-right py-2 px-1">{zh ? "入场价" : "Entry"}</th>
                                  <th className="text-right py-2 px-1">{zh ? "出场价" : "Exit"}</th>
                                  <th className="text-right py-2 px-1">{zh ? "数量" : "Qty"}</th>
                                  <th className="text-right py-2 px-1">PnL</th>
                                  <th className="text-right py-2 px-1">PnL %</th>
                                  <th className="text-center py-2 px-1">{zh ? "原因" : "Reason"}</th>
                                  <th className="text-center py-2 px-1">DCA</th>
                                </tr>
                              </thead>
                              <tbody>
                                {(showAllTrades ? bt.trades : bt.trades.slice(0, 20)).map((tr: any) => (
                                  <tr key={tr.id} className="border-b border-border/30 hover:bg-muted/10">
                                    <td className="py-1.5 px-1 text-muted-foreground">{tr.id}</td>
                                    <td className="text-right px-1">${tr.entry_price?.toLocaleString()}</td>
                                    <td className="text-right px-1">${tr.exit_price?.toLocaleString()}</td>
                                    <td className="text-right px-1 text-xs">{tr.qty?.toFixed(5)}</td>
                                    <td className={`text-right px-1 font-medium ${tr.pnl >= 0 ? "text-green-500" : "text-red-500"}`}>
                                      {fmtUsd(tr.pnl)}
                                    </td>
                                    <td className={`text-right px-1 ${tr.pnl_pct >= 0 ? "text-green-500" : "text-red-500"}`}>
                                      {fmtPct(tr.pnl_pct)}
                                    </td>
                                    <td className="text-center px-1">
                                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                                        tr.exit_reason?.includes("TP") ? "bg-green-500/10 text-green-500" :
                                        tr.exit_reason?.includes("SL") ? "bg-red-500/10 text-red-500" :
                                        tr.exit_reason?.includes("Band") ? "bg-yellow-500/10 text-yellow-500" :
                                        "bg-muted text-muted-foreground"
                                      }`}>
                                        {tr.exit_reason || "—"}
                                      </span>
                                    </td>
                                    <td className="text-center px-1 text-xs text-muted-foreground">{tr.dca_count}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      ) : (
                        <div className="text-center py-12 text-muted-foreground">
                          <List className="h-10 w-10 mx-auto mb-3 opacity-50" />
                          <p>{zh ? "运行回测查看交易记录" : "Run backtest to see trade details"}</p>
                          <p className="text-xs mt-1">{zh ? "点击上方 \"运行回测\" 按钮" : "Click \"Run Backtest\" above"}</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-center py-8 text-muted-foreground">
                  <BarChart3 className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  {zh ? "暂无回测数据 — 点击运行回测" : "No backtest data — click Run Backtest"}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Parity Check */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CheckCircle className="h-5 w-5" />
                {zh ? "Pine vs Python Parity" : "Pine vs Python Parity"}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {parity?.error ? (
                <div className="text-center py-6 text-muted-foreground">
                  <Info className="h-6 w-6 mx-auto mb-2 opacity-50" />
                  <p className="text-sm">{parity.error}</p>
                </div>
              ) : parity ? (
                <div className="flex items-center justify-between p-4 rounded-lg bg-muted/20">
                  <div>
                    <div className="text-sm text-muted-foreground">{zh ? "验证结果" : "Verdict"}</div>
                    <div className={`text-lg font-bold ${parity.parity ? "text-green-500" : "text-red-500"}`}>
                      {parity.parity ? "PERFECT PARITY" : "DIFFERENCES FOUND"}
                    </div>
                  </div>
                  <div className="text-sm text-muted-foreground">
                    {parity.days && `${parity.days}D`}
                  </div>
                </div>
              ) : null}
            </CardContent>
          </Card>

          {/* Walk-Forward (read-only) */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Shield className="h-5 w-5" />
                {zh ? "Walk-Forward 验证" : "Walk-Forward Validation"}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {walkforward?.error ? (
                <div className="text-center py-6 text-muted-foreground">
                  <Info className="h-6 w-6 mx-auto mb-2 opacity-50" />
                  <p className="text-sm">{zh ? "暂无 Walk-Forward 数据" : "No walk-forward data"}</p>
                  <p className="text-xs mt-1 font-mono">{walkforward.hint}</p>
                </div>
              ) : walkforward ? (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <ParamItem label="In-Sample SQN" value={walkforward.sqn_in || "—"} />
                  <ParamItem label="Out-Sample SQN" value={walkforward.sqn_out || "—"} />
                  <ParamItem label="WFE" value={walkforward.wfe ? `${walkforward.wfe}%` : "—"} />
                  <ParamItem label="Verdict" value={walkforward.verdict || "—"} />
                </div>
              ) : null}
            </CardContent>
          </Card>

          {/* Strategy Description */}
          <Card>
            <CardHeader>
              <CardTitle>{zh ? "策略说明" : "Strategy Description"}</CardTitle>
            </CardHeader>
            <CardContent className="prose prose-sm dark:prose-invert max-w-none">
              {zh ? (
                <div className="space-y-3 text-sm text-muted-foreground">
                  <p><strong>核心逻辑：</strong>VWMA(14) 中轴 ± 1.0% 构成 SRP 通道。价格触及下轨 + RSI-MFI &lt; 55 → 开多。</p>
                  <p><strong>DCA 加仓：</strong>双条件触发 — 价格从上次入场下跌 &gt; 3% (changeFromLast) 且低于均价 - 3% (nextSO)。加仓量 = 当前持仓 × 1.5，最多 4 层真实 DCA。</p>
                  <p><strong>Virtual DCA：</strong>真实 DCA 用尽后，虚拟加仓拉低虚拟均价。不产生真实订单，只降低 TP 目标价。</p>
                  <p><strong>退出 (三重机制)：</strong></p>
                  <ul className="list-disc ml-4">
                    <li>TP: 价格 &gt; 虚拟均价 × 1.025 (2.5% 止盈)</li>
                    <li>Band: 价格突破上轨 + RSI-MFI &gt; 100 + 真实均价盈利 → 全仓平</li>
                    <li>SL: 真实均价回撤 ≥ 6% → 强制止损</li>
                  </ul>
                  <p><strong>复利：</strong>每次开新 deal 时用当前账户净值的 10% 作为 base。赚了 base 自动放大，亏了自动缩小。</p>
                </div>
              ) : (
                <div className="space-y-3 text-sm text-muted-foreground">
                  <p><strong>Core Logic:</strong> VWMA(14) center ± 1.0% forms the SRP channel. Price hits lower band + RSI-MFI &lt; 55 → open long.</p>
                  <p><strong>DCA:</strong> Dual condition — price drops &gt; 3% from last entry AND below avg - 3%. Size = position × 1.5, max 4 real DCA layers.</p>
                  <p><strong>Virtual DCA:</strong> After real DCA exhausted, virtual averaging lowers virtual avg. No real orders — only reduces TP target.</p>
                  <p><strong>Exit (triple mechanism):</strong></p>
                  <ul className="list-disc ml-4">
                    <li>TP: price &gt; virtual_avg × 1.025 (2.5% take profit)</li>
                    <li>Band: price breaks upper band + RSI-MFI &gt; 100 + real avg in profit → full close</li>
                    <li>SL: real avg drawdown ≥ 6% → forced stop loss</li>
                  </ul>
                  <p><strong>Compound:</strong> Each new deal uses 10% of current equity as base. Wins increase size, losses decrease size.</p>
                </div>
              )}
            </CardContent>
          </Card>
        </main>

        <Footer t={t} />
      </div>
    </>
  );
}

// ====== Helper Components ======

function ParamItem({ label, value }: { label: string; value: any }) {
  return (
    <div className="bg-muted/30 rounded-lg p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-semibold mt-0.5">{String(value)}</div>
    </div>
  );
}

function StatBox({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 bg-muted/20 rounded-lg p-3">
      <div className="shrink-0">{icon}</div>
      <div>
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="font-semibold">{value}</div>
      </div>
    </div>
  );
}

function BigStat({ label, value, positive }: { label: string; value: string; positive: boolean }) {
  return (
    <div className="bg-muted/20 rounded-lg p-4 text-center">
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div className={`text-xl font-bold ${positive ? "text-green-500" : "text-red-500"}`}>{value}</div>
    </div>
  );
}

function TabBtn({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
        active
          ? "border-primary text-primary"
          : "border-transparent text-muted-foreground hover:text-foreground"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

function InputField({
  label, value, type, placeholder, options, onChange,
}: {
  label: string;
  value: any;
  type: "text" | "number" | "select";
  placeholder?: string;
  options?: { v: number; l: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="text-xs text-muted-foreground block mb-1">{label}</label>
      {type === "select" ? (
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full text-sm border rounded px-2 py-1.5 bg-background"
        >
          {options?.map((o) => (
            <option key={o.v} value={o.v}>{o.l}</option>
          ))}
        </select>
      ) : (
        <input
          type={type}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className="w-full text-sm border rounded px-2 py-1.5 bg-background placeholder:text-muted-foreground/50"
        />
      )}
    </div>
  );
}
