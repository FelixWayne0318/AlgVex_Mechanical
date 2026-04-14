"use client";

import { motion, AnimatePresence } from "framer-motion";
import { Layers, Shield, Target, AlertTriangle, Clock, TrendingUp, TrendingDown } from "lucide-react";
import { formatTime } from "@/lib/utils";

interface LayerOrder {
  entry_price: number;
  quantity: number;
  side: string;
  sl_price: number | null;
  tp_price: number | null;
  confidence: string;
  timestamp: string;
  highest_price: number | null;
  lowest_price: number | null;
  has_sl: boolean;
  has_tp: boolean;
}

interface LayerOrdersProps {
  layers?: Record<string, LayerOrder>;
  count?: number;
}

export function LayerOrders({ layers, count }: LayerOrdersProps) {
  const entries = layers ? Object.entries(layers) : [];

  if (!entries.length) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
        <Layers className="h-8 w-8 mb-3 opacity-40" />
        <p className="text-sm font-medium">No active layers</p>
        <p className="text-xs mt-1">Layers appear when the bot opens positions</p>
      </div>
    );
  }

  return (
    <div className="space-y-3 max-h-96 overflow-y-auto pr-2">
      <AnimatePresence mode="popLayout">
        {entries.map(([layerId, layer], index) => {
          const isLong = layer.side?.toUpperCase().includes("LONG") || layer.side?.toUpperCase() === "BUY";
          const sideColor = isLong ? "text-green-500" : "text-red-500";
          const sideBg = isLong ? "bg-green-500/10" : "bg-red-500/10";
          const sideBorder = isLong ? "border-green-500/30" : "border-red-500/30";
          const SideIcon = isLong ? TrendingUp : TrendingDown;

          // Calculate unrealized P&L direction indicator
          const riskPct = layer.entry_price && layer.sl_price
            ? Math.abs(layer.entry_price - layer.sl_price) / layer.entry_price * 100
            : 0;
          const rewardPct = layer.entry_price && layer.tp_price
            ? Math.abs(layer.tp_price - layer.entry_price) / layer.entry_price * 100
            : 0;
          const rrRatio = riskPct > 0 ? (rewardPct / riskPct).toFixed(1) : "N/A";

          return (
            <motion.div
              key={layerId}
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 10 }}
              transition={{ duration: 0.2, delay: index * 0.05 }}
              className={`p-3 rounded-lg ${sideBg} border ${sideBorder}`}
            >
              {/* Header: Side + Layer ID + Confidence */}
              <div className="flex items-center justify-between mb-2 flex-wrap gap-1">
                <div className="flex items-center gap-1.5 sm:gap-2">
                  <SideIcon className={`h-4 w-4 ${sideColor} flex-shrink-0`} />
                  <span className={`font-medium text-xs sm:text-sm ${sideColor}`}>
                    {isLong ? "LONG" : "SHORT"}
                  </span>
                  <span className="text-[10px] sm:text-xs text-muted-foreground font-mono">
                    #{layerId.slice(-6)}
                  </span>
                  {layer.confidence && (
                    <span className={`text-[10px] sm:text-xs px-1 sm:px-1.5 py-0.5 rounded ${
                      layer.confidence === "HIGH" ? "text-green-500 bg-green-500/10" :
                      layer.confidence === "MEDIUM" ? "text-yellow-500 bg-yellow-500/10" :
                      "text-muted-foreground bg-muted"
                    }`}>
                      {layer.confidence}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1 text-[10px] sm:text-xs text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  {formatTime(layer.timestamp)}
                </div>
              </div>

              {/* Price grid */}
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div>
                  <span className="text-muted-foreground">Entry</span>
                  <p className="font-mono font-medium">${layer.entry_price?.toLocaleString()}</p>
                </div>
                <div>
                  <span className="text-muted-foreground flex items-center gap-1">
                    <Shield className="h-3 w-3" /> SL
                  </span>
                  <p className={`font-mono font-medium ${layer.has_sl ? "" : "text-red-500"}`}>
                    {layer.sl_price ? `$${layer.sl_price.toLocaleString()}` : "NONE"}
                  </p>
                </div>
                <div>
                  <span className="text-muted-foreground flex items-center gap-1">
                    <Target className="h-3 w-3" /> TP
                  </span>
                  <p className={`font-mono font-medium ${layer.has_tp ? "" : "text-yellow-500"}`}>
                    {layer.tp_price ? `$${layer.tp_price.toLocaleString()}` : "NONE"}
                  </p>
                </div>
              </div>

              {/* Footer: Quantity + R/R + Safety indicators */}
              <div className="flex items-center justify-between mt-2 pt-2 border-t border-border/30 flex-wrap gap-1">
                <span className="text-[10px] sm:text-xs text-muted-foreground">
                  Qty: <span className="font-mono">{layer.quantity?.toFixed(4)}</span>
                </span>
                <span className="text-[10px] sm:text-xs text-muted-foreground">
                  R/R: <span className="font-mono">{rrRatio}:1</span>
                </span>
                <div className="flex items-center gap-1.5">
                  {!layer.has_sl && (
                    <span className="flex items-center gap-0.5 text-[10px] sm:text-xs text-red-500">
                      <AlertTriangle className="h-3 w-3" /> No SL
                    </span>
                  )}
                </div>
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

