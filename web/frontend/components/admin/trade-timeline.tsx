"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowUpRight, ArrowDownRight, Clock } from "lucide-react";
import { formatTimeAgo } from "@/lib/utils";

interface Trade {
  id: string;
  symbol: string;
  side: "BUY" | "SELL";
  quantity: number;
  price: number;
  pnl?: number;
  time: string;
}

interface TradeTimelineProps {
  trades?: Trade[];
}

export function TradeTimeline({ trades }: TradeTimelineProps) {
  const [showAll, setShowAll] = useState(false);
  const allTrades = trades?.length ? trades : [];
  const displayTrades = showAll ? allTrades : allTrades.slice(0, 5);

  return (
    <div className={`space-y-3 ${showAll ? '' : 'max-h-80'} overflow-y-auto pr-2`}>
      <AnimatePresence mode="popLayout">
        {displayTrades.map((trade, index) => (
          <motion.div
            key={trade.id}
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 20 }}
            transition={{ duration: 0.2, delay: index * 0.05 }}
            className="flex items-center gap-3 p-3 rounded-lg bg-muted/30 border border-border/50 hover:border-border transition-colors"
          >
            {/* Side indicator */}
            <div
              className={`p-2 rounded-lg ${
                trade.side === "BUY"
                  ? "bg-green-500/10 text-green-500"
                  : "bg-red-500/10 text-red-500"
              }`}
            >
              {trade.side === "BUY" ? (
                <ArrowUpRight className="h-4 w-4" />
              ) : (
                <ArrowDownRight className="h-4 w-4" />
              )}
            </div>

            {/* Trade info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium">{trade.symbol}</span>
                <span
                  className={`text-xs px-1.5 py-0.5 rounded ${
                    trade.side === "BUY"
                      ? "bg-green-500/10 text-green-500"
                      : "bg-red-500/10 text-red-500"
                  }`}
                >
                  {trade.side}
                </span>
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span>{trade.quantity} @ ${trade.price.toLocaleString()}</span>
              </div>
            </div>

            {/* PnL and time */}
            <div className="text-right">
              {trade.pnl !== undefined && (
                <p
                  className={`font-medium ${
                    trade.pnl >= 0 ? "text-green-500" : "text-red-500"
                  }`}
                >
                  {trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(2)}
                </p>
              )}
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <Clock className="h-3 w-3" />
                {formatTimeAgo(trade.time)}
              </div>
            </div>
          </motion.div>
        ))}
      </AnimatePresence>

      {!displayTrades.length && (
        <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
          <Clock className="h-8 w-8 mb-3 opacity-40" />
          <p className="text-sm font-medium">No trades yet</p>
          <p className="text-xs mt-1">Trade history will appear when the bot executes orders</p>
        </div>
      )}
      {!showAll && allTrades.length > 5 && (
        <button
          onClick={() => setShowAll(true)}
          className="w-full py-2 text-xs text-primary hover:text-primary/80 hover:underline transition-colors"
        >
          Show all {allTrades.length} trades
        </button>
      )}
      {showAll && allTrades.length > 5 && (
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


