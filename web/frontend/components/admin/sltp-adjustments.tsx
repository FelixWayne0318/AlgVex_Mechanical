"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowRight, Clock, TrendingUp, Target } from "lucide-react";
import { formatTime } from "@/lib/utils";

interface SLTPAdjustment {
  type: string;
  layer_id?: string;
  side?: string;
  old_sl?: number;
  new_sl?: number;
  old_tp?: number;
  new_tp?: number;
  current_price?: number;
  fill_price?: number;
  highest_price?: number;
  atr?: number;
  old_rr?: number;
  new_rr?: number;
  timestamp: string;
}

interface SLTPAdjustmentsProps {
  adjustments?: SLTPAdjustment[];
  count?: number;
}

export function SLTPAdjustments({ adjustments, count }: SLTPAdjustmentsProps) {
  const [showAll, setShowAll] = useState(false);
  const allItems = adjustments?.length ? adjustments : [];
  const items = showAll ? allItems : allItems.slice(0, 15);

  if (!items.length) {
    return (
      <div className="flex flex-col items-center justify-center py-6 text-muted-foreground">
        <Target className="h-8 w-8 mb-3 opacity-40" />
        <p className="text-sm font-medium">No SL/TP adjustments</p>
        <p className="text-xs mt-1">Post-fill adjustments appear here</p>
      </div>
    );
  }

  return (
    <div className={`space-y-1.5 ${showAll ? '' : 'max-h-80'} overflow-y-auto pr-2`}>
      <AnimatePresence mode="popLayout">
        {items.map((adj, index) => {
          const isTPAdjust = adj.type === "post_fill_tp_adjust";

          return (
            <motion.div
              key={`${adj.timestamp}-${index}`}
              initial={{ opacity: 0, x: -5 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.15, delay: index * 0.02 }}
              className="p-2 rounded bg-muted/20 border border-border/30 text-xs"
            >
              {/* Top row: type badge + timestamp */}
              <div className="flex items-center justify-between mb-1">
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium whitespace-nowrap ${
                  isTPAdjust ? "bg-purple-500/10 text-purple-400" :
                  "bg-muted text-muted-foreground"
                }`}>
                  {isTPAdjust ? "TP Adjust" : adj.type}
                </span>
                <div className="flex items-center gap-2">
                  {adj.old_rr != null && adj.new_rr != null && (
                    <span className="text-muted-foreground whitespace-nowrap">
                      R/R {adj.old_rr} → {adj.new_rr}
                    </span>
                  )}
                  <span className="text-muted-foreground whitespace-nowrap flex items-center gap-0.5">
                    <Clock className="h-3 w-3" />
                    {formatTime(adj.timestamp)}
                  </span>
                </div>
              </div>

              {/* Price change row */}
              <div className="flex items-center gap-1 font-mono flex-wrap">
                {adj.old_sl != null && adj.new_sl != null && (
                  <>
                    <span className="text-muted-foreground">SL</span>
                    <span>${adj.old_sl.toLocaleString()}</span>
                    <ArrowRight className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                    <span className={adj.new_sl > adj.old_sl ? "text-green-500" : "text-red-500"}>
                      ${adj.new_sl.toLocaleString()}
                    </span>
                  </>
                )}
                {adj.old_tp != null && adj.new_tp != null && (
                  <>
                    <span className="text-muted-foreground ml-1">TP</span>
                    <span>${adj.old_tp.toLocaleString()}</span>
                    <ArrowRight className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                    <span className="text-purple-400">${adj.new_tp.toLocaleString()}</span>
                  </>
                )}
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
      {!showAll && allItems.length > 15 && (
        <button
          onClick={() => setShowAll(true)}
          className="w-full py-2 text-xs text-primary hover:text-primary/80 hover:underline transition-colors"
        >
          Show all {count || allItems.length} adjustments
        </button>
      )}
      {showAll && allItems.length > 15 && (
        <button
          onClick={() => setShowAll(false)}
          className="w-full py-2 text-xs text-muted-foreground hover:text-foreground/70 hover:underline transition-colors"
        >
          Show less
        </button>
      )}
    </div>
  );
}

