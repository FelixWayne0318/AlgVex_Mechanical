"use client";

import { useMechanicalHistory, SignalHistoryEntry } from "@/hooks/useMechanical";

function SignalBadge({ signal, tier }: { signal: string; tier: string }) {
  const color = signal === "LONG"
    ? "bg-green-500/10 text-green-400 border-green-500/20"
    : signal === "SHORT"
    ? "bg-red-500/10 text-red-400 border-red-500/20"
    : "bg-slate-500/10 text-slate-400 border-slate-500/20";

  return (
    <span className={`px-2 py-0.5 rounded-md border text-[10px] font-semibold ${color}`}>
      {signal === "HOLD" ? "HOLD" : `${signal} ${tier}`}
    </span>
  );
}

function DirDot({ direction }: { direction: string }) {
  const c = direction === "BULLISH" ? "text-green-400" : direction === "BEARISH" ? "text-red-400" : "text-slate-500";
  return <span className={`text-[10px] font-mono ${c}`}>{direction?.charAt(0) || "-"}</span>;
}

export function MechanicalSignalHistory({ limit = 20 }: { limit?: number }) {
  const { history, isLoading } = useMechanicalHistory(limit);

  if (isLoading) {
    return (
      <div className="glass-card rounded-2xl p-5">
        <div className="text-sm font-semibold mb-3">Signal History</div>
        <div className="text-xs text-muted-foreground">Loading...</div>
      </div>
    );
  }

  const entries = history || [];

  return (
    <div className="glass-card rounded-2xl p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-semibold">Signal History</span>
        <span className="text-[10px] text-muted-foreground">{entries.length} signals</span>
      </div>

      {entries.length === 0 ? (
        <div className="text-xs text-muted-foreground">No signals yet</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground text-[10px] uppercase tracking-wider border-b border-border/30">
                <th className="text-left pb-2 pr-3">Time</th>
                <th className="text-left pb-2 pr-3">Signal</th>
                <th className="text-right pb-2 pr-3">net_raw</th>
                <th className="text-center pb-2 pr-1">S</th>
                <th className="text-center pb-2 pr-1">D</th>
                <th className="text-center pb-2">F</th>
                <th className="text-right pb-2">Zones</th>
              </tr>
            </thead>
            <tbody>
              {entries.slice().reverse().slice(0, limit).map((entry, i) => {
                const ts = entry.timestamp || "";
                const timeStr = ts.includes("_")
                  ? `${ts.slice(9, 11)}:${ts.slice(11, 13)}`
                  : ts.slice(11, 16);

                return (
                  <tr key={i} className="border-b border-border/10 hover:bg-muted/10">
                    <td className="py-1.5 pr-3 text-muted-foreground font-mono">{timeStr}</td>
                    <td className="py-1.5 pr-3"><SignalBadge signal={entry.signal} tier={entry.tier} /></td>
                    <td className={`py-1.5 pr-3 text-right font-mono ${entry.net_raw > 0 ? "text-green-400" : entry.net_raw < 0 ? "text-red-400" : ""}`}>
                      {entry.net_raw >= 0 ? "+" : ""}{entry.net_raw.toFixed(3)}
                    </td>
                    <td className="py-1.5 pr-1 text-center"><DirDot direction={entry.structure_dir} /></td>
                    <td className="py-1.5 pr-1 text-center"><DirDot direction={entry.divergence_dir} /></td>
                    <td className="py-1.5 text-center"><DirDot direction={entry.order_flow_dir} /></td>
                    <td className="py-1.5 text-right text-muted-foreground">{entry.zone_count}/4</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
