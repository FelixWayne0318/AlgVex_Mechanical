"use client";

import { useMechanicalState } from "@/hooks/useMechanical";
import { TrendingUp, TrendingDown, Minus, Zap, Shield, GitBranch, Waves } from "lucide-react";

function DimensionBar({ label, score, direction, icon: Icon }: {
  label: string;
  score: number;
  direction: string;
  icon: React.ElementType;
}) {
  const pct = Math.min(score * 10, 100);
  const color = direction === "BULLISH"
    ? "bg-green-500" : direction === "BEARISH"
    ? "bg-red-500" : "bg-slate-500";
  const textColor = direction === "BULLISH"
    ? "text-green-400" : direction === "BEARISH"
    ? "text-red-400" : "text-slate-400";

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Icon className={`h-3.5 w-3.5 ${textColor}`} />
          <span className="text-xs text-muted-foreground">{label}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`text-xs font-semibold ${textColor}`}>{direction}</span>
          <span className="text-xs text-muted-foreground font-mono">{score}/10</span>
        </div>
      </div>
      <div className="h-1.5 bg-muted/50 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function ZoneDot({ active, label }: { active: boolean; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <div className={`h-2 w-2 rounded-full ${active ? "bg-green-500 shadow-[0_0_6px_hsl(158,64%,52%/0.6)]" : "bg-slate-600"}`} />
      <span className={`text-[10px] ${active ? "text-green-400" : "text-muted-foreground"}`}>{label}</span>
    </div>
  );
}

export function MechanicalScoreCard() {
  const { state, isLoading } = useMechanicalState();

  if (isLoading || !state || state.status === "no_data") {
    return (
      <div className="glass-card rounded-2xl p-5">
        <div className="text-sm text-muted-foreground">Prism 评分</div>
        <div className="text-xs text-muted-foreground mt-2">Waiting for data...</div>
      </div>
    );
  }

  const { net_raw, signal, signal_tier, structure, divergence, order_flow, zone_conditions, zone_count, regime, thresholds } = state;
  const signalColor = signal === "LONG" ? "text-green-400" : signal === "SHORT" ? "text-red-400" : "text-slate-400";
  const signalBg = signal === "LONG" ? "bg-green-500/10 border-green-500/20" : signal === "SHORT" ? "bg-red-500/10 border-red-500/20" : "bg-slate-500/10 border-slate-500/20";

  // net_raw gauge position (map -1..1 to 0..100)
  const gaugePos = ((net_raw + 1) / 2) * 100;

  return (
    <div className="glass-card rounded-2xl p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Zap className="h-4 w-4 text-primary" />
          <span className="text-sm font-semibold">Prism</span>
        </div>
        <div className={`px-2.5 py-1 rounded-lg border text-xs font-bold ${signalBg} ${signalColor}`}>
          {signal} {signal_tier !== "HOLD" && `(${signal_tier})`}
        </div>
      </div>

      {/* net_raw gauge */}
      <div className="space-y-1">
        <div className="flex justify-between text-[10px] text-muted-foreground">
          <span>SHORT</span>
          <span className="font-mono font-semibold text-foreground">{net_raw >= 0 ? "+" : ""}{net_raw.toFixed(3)}</span>
          <span>LONG</span>
        </div>
        <div className="relative h-2 bg-muted/50 rounded-full overflow-hidden">
          {/* Threshold markers */}
          <div className="absolute h-full w-px bg-slate-500/50" style={{ left: `${((thresholds.low + 1) / 2) * 100}%` }} />
          <div className="absolute h-full w-px bg-slate-500/50" style={{ left: `${((-thresholds.low + 1) / 2) * 100}%` }} />
          <div className="absolute h-full w-px bg-primary/40" style={{ left: `${((thresholds.med + 1) / 2) * 100}%` }} />
          <div className="absolute h-full w-px bg-primary/40" style={{ left: `${((-thresholds.med + 1) / 2) * 100}%` }} />
          {/* Indicator */}
          <div
            className="absolute top-0 h-full w-1 bg-foreground rounded-full transition-all duration-300"
            style={{ left: `${gaugePos}%`, transform: "translateX(-50%)" }}
          />
          {/* Color fill */}
          {net_raw > 0 ? (
            <div className="absolute top-0 left-1/2 h-full bg-green-500/30 rounded-r-full" style={{ width: `${gaugePos - 50}%` }} />
          ) : (
            <div className="absolute top-0 h-full bg-red-500/30 rounded-l-full" style={{ left: `${gaugePos}%`, width: `${50 - gaugePos}%` }} />
          )}
        </div>
      </div>

      {/* 3 Dimensions */}
      <div className="space-y-3">
        <DimensionBar label="Structure" score={structure?.score ?? 0} direction={structure?.direction ?? "N/A"} icon={Shield} />
        <DimensionBar label="Divergence" score={divergence?.score ?? 0} direction={divergence?.direction ?? "N/A"} icon={GitBranch} />
        <DimensionBar label="Order Flow" score={order_flow?.score ?? 0} direction={order_flow?.direction ?? "N/A"} icon={Waves} />
      </div>

      {/* Zone conditions */}
      <div className="pt-2 border-t border-border/30">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] text-muted-foreground uppercase tracking-wider">Zone Conditions</span>
          <span className="text-[10px] text-muted-foreground">{zone_count}/4</span>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          <ZoneDot active={zone_conditions?.extension_4h} label={`Ext 4H ${zone_conditions?.extension_4h_regime || ""}`} />
          <ZoneDot active={zone_conditions?.rsi_oversold} label={`RSI ${zone_conditions?.rsi_30m?.toFixed(0) || "-"}`} />
          <ZoneDot active={zone_conditions?.cvd_accumulation} label="CVD Accum" />
          <ZoneDot active={zone_conditions?.sr_proximity} label={`S/R ${zone_conditions?.sr_distance_atr?.toFixed(1) || "-"} ATR`} />
        </div>
      </div>

      {/* Regime badge */}
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-muted-foreground">Regime: <span className="text-foreground font-medium">{regime}</span></span>
        <span className="text-muted-foreground">Price: <span className="text-foreground font-mono">${state.price?.toLocaleString()}</span></span>
      </div>
    </div>
  );
}
