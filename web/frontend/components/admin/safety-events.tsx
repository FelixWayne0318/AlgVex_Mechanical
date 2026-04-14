"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { AlertTriangle, Shield, Clock, XCircle } from "lucide-react";

interface SafetyEvent {
  type: string;
  reason: string;
  quantity: number;
  side: string;
  sl_price?: number;
  current_price?: number;
  sl_pct?: number;
  submitted?: boolean;
  attempts?: number;
  timestamp: string;
}

interface SafetyEventsProps {
  events?: SafetyEvent[];
  count?: number;
}

export function SafetyEvents({ events, count }: SafetyEventsProps) {
  const [expandedReasons, setExpandedReasons] = useState<Set<number>>(new Set());
  const [showAll, setShowAll] = useState(false);
  const allEvents = events?.length ? events : [];
  const displayEvents = showAll ? allEvents : allEvents.slice(0, 10);

  if (!displayEvents.length) {
    return (
      <div className="flex flex-col items-center justify-center py-6 text-muted-foreground">
        <Shield className="h-8 w-8 mb-3 opacity-40" />
        <p className="text-sm font-medium">No safety events</p>
        <p className="text-xs mt-1">Emergency SL and market close events appear here</p>
      </div>
    );
  }

  return (
    <div className={`space-y-2 ${showAll ? '' : 'max-h-80'} overflow-y-auto pr-2`}>
      <AnimatePresence mode="popLayout">
        {displayEvents.map((event, index) => {
          const isMarketClose = event.type === "emergency_market_close";
          const bgColor = isMarketClose ? "bg-red-500/10" : "bg-orange-500/10";
          const borderColor = isMarketClose ? "border-red-500/30" : "border-orange-500/30";
          const iconColor = isMarketClose ? "text-red-500" : "text-orange-500";
          const Icon = isMarketClose ? XCircle : AlertTriangle;

          return (
            <motion.div
              key={`${event.timestamp}-${index}`}
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 10 }}
              transition={{ duration: 0.2, delay: index * 0.03 }}
              className={`p-3 rounded-lg ${bgColor} border ${borderColor}`}
            >
              <div className="flex items-start gap-2">
                <Icon className={`h-4 w-4 mt-0.5 flex-shrink-0 ${iconColor}`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-xs font-medium ${iconColor}`}>
                      {isMarketClose ? "MARKET CLOSE" : "EMERGENCY SL"}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {event.quantity?.toFixed(4)} BTC {event.side?.toUpperCase()}
                    </span>
                    {event.sl_price && (
                      <span className="text-xs font-mono text-muted-foreground">
                        SL @ ${event.sl_price.toLocaleString()}
                      </span>
                    )}
                    {isMarketClose && event.submitted === false && (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-red-500/20 text-red-500">
                        FAILED
                      </span>
                    )}
                  </div>
                  <p
                    className={`text-xs text-muted-foreground mt-0.5 cursor-pointer hover:text-foreground/70 transition-colors ${expandedReasons.has(index) ? '' : 'line-clamp-2'}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      setExpandedReasons(prev => {
                        const next = new Set(prev);
                        next.has(index) ? next.delete(index) : next.add(index);
                        return next;
                      });
                    }}
                  >
                    {event.reason}
                  </p>
                </div>
                <div className="flex items-center gap-1 text-[10px] text-muted-foreground whitespace-nowrap">
                  <Clock className="h-3 w-3" />
                  {formatTime(event.timestamp)}
                </div>
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
      {!showAll && allEvents.length > 10 && (
        <button
          onClick={() => setShowAll(true)}
          className="w-full py-2 text-xs text-primary hover:text-primary/80 hover:underline transition-colors"
        >
          Show all {count || allEvents.length} events
        </button>
      )}
      {showAll && allEvents.length > 10 && (
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

function formatTime(isoString: string): string {
  if (!isoString) return "N/A";
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return "N/A";
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffH = Math.floor(diffMs / 3600000);
  if (diffH < 24) {
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}
