'use client';

import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

interface Trade {
  id: string;
  symbol: string;
  time: string;
  time_display: string;
  pnl: number;
  pnl_percent: number;
  side: 'LONG' | 'SHORT';
  is_profit: boolean;
}

interface TradeTimelineProps {
  trades: Trade[];
  maxItems?: number;
}

export function TradeTimeline({ trades, maxItems = 10 }: TradeTimelineProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const displayTrades = trades.slice(0, maxItems);

  if (trades.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <svg className="w-12 h-12 mb-3 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
        </svg>
        <p className="text-sm">No trades yet</p>
      </div>
    );
  }

  return (
    <div className="relative">
      {/* Timeline line */}
      <div className="absolute left-4 top-0 bottom-0 w-px bg-border" />

      <div className="space-y-3">
        <AnimatePresence>
          {displayTrades.map((trade, index) => (
            <motion.div
              key={trade.id}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              transition={{ delay: index * 0.05 }}
              className="relative pl-10"
            >
              {/* Timeline dot */}
              <div
                className={`absolute left-2.5 top-3 w-3 h-3 rounded-full border-2 ${
                  trade.is_profit
                    ? 'bg-[hsl(var(--profit))]/20 border-[hsl(var(--profit))]'
                    : 'bg-[hsl(var(--loss))]/20 border-[hsl(var(--loss))]'
                }`}
              />

              {/* Trade card */}
              <div
                className={`p-3 rounded-lg border transition-all cursor-pointer hover:border-primary/30 ${
                  expandedId === trade.id
                    ? 'bg-card border-primary/50'
                    : 'bg-card/50 border-border/50'
                }`}
                onClick={() => setExpandedId(expandedId === trade.id ? null : trade.id)}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    {/* Direction badge */}
                    <span
                      className={`px-2 py-0.5 text-xs font-medium rounded ${
                        trade.side === 'LONG'
                          ? 'bg-[hsl(var(--profit))]/10 text-[hsl(var(--profit))]'
                          : 'bg-[hsl(var(--loss))]/10 text-[hsl(var(--loss))]'
                      }`}
                    >
                      {trade.side}
                    </span>

                    {/* Symbol */}
                    <span className="font-medium text-foreground">{trade.symbol}</span>

                    {/* Time */}
                    <span className="text-xs text-muted-foreground">{trade.time_display}</span>
                  </div>

                  {/* PnL */}
                  <div className={`font-mono font-semibold ${trade.is_profit ? 'text-[hsl(var(--profit))]' : 'text-[hsl(var(--loss))]'}`}>
                    {trade.is_profit ? '+' : ''}{trade.pnl.toFixed(2)} USDT
                  </div>
                </div>

                {/* Expanded details */}
                <AnimatePresence>
                  {expandedId === trade.id && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      className="overflow-hidden"
                    >
                      <div className="pt-3 mt-3 border-t border-border/50 grid grid-cols-2 gap-3 text-sm">
                        <div>
                          <span className="text-muted-foreground">Trade ID</span>
                          <p className="font-mono text-foreground">{trade.id}</p>
                        </div>
                        <div>
                          <span className="text-muted-foreground">Time</span>
                          <p className="text-foreground">{new Date(trade.time).toLocaleString()}</p>
                        </div>
                        {trade.pnl_percent !== 0 && (
                          <div>
                            <span className="text-muted-foreground">Return</span>
                            <p className={trade.is_profit ? 'text-[hsl(var(--profit))]' : 'text-[hsl(var(--loss))]'}>
                              {trade.is_profit ? '+' : ''}{trade.pnl_percent.toFixed(2)}%
                            </p>
                          </div>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {trades.length > maxItems && (
        <div className="mt-4 text-center">
          <span className="text-sm text-muted-foreground">
            Showing {maxItems} of {trades.length} trades
          </span>
        </div>
      )}
    </div>
  );
}
