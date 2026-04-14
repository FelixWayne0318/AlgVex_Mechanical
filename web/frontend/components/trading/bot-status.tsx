'use client';

import { useEffect, useState } from 'react';

interface BotStatusProps {
  status: 'running' | 'paused' | 'stopped' | 'error';
  lastTradeTime?: string;
  nextAnalysisTime?: string;
  timerIntervalSec?: number;
}

export function BotStatus({
  status,
  lastTradeTime,
  nextAnalysisTime,
  timerIntervalSec = 900,
}: BotStatusProps) {
  const [countdown, setCountdown] = useState<string>('--:--');
  const [countdownPercent, setCountdownPercent] = useState(100);

  useEffect(() => {
    if (!nextAnalysisTime || status !== 'running') {
      setCountdown('--:--');
      setCountdownPercent(100);
      return;
    }

    const updateCountdown = () => {
      const now = new Date();
      const next = new Date(nextAnalysisTime);
      const diff = next.getTime() - now.getTime();

      if (diff <= 0) {
        setCountdown('Analyzing...');
        setCountdownPercent(100);
        return;
      }

      const minutes = Math.floor(diff / 60000);
      const seconds = Math.floor((diff % 60000) / 1000);
      setCountdown(`${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`);
      setCountdownPercent(Math.max(0, (diff / (timerIntervalSec * 1000)) * 100));
    };

    updateCountdown();
    const interval = setInterval(updateCountdown, 1000);
    return () => clearInterval(interval);
  }, [nextAnalysisTime, status, timerIntervalSec]);

  const statusConfig = {
    running: {
      label: 'Running',
      dotColor: 'bg-green-500',
      textColor: 'text-green-500',
      bgColor: 'bg-green-500/10',
      borderColor: 'border-green-500/30',
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      ),
    },
    paused: {
      label: 'Paused',
      dotColor: 'bg-yellow-500',
      textColor: 'text-yellow-500',
      bgColor: 'bg-yellow-500/10',
      borderColor: 'border-yellow-500/30',
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      ),
    },
    stopped: {
      label: 'Stopped',
      dotColor: 'bg-gray-400',
      textColor: 'text-muted-foreground',
      bgColor: 'bg-muted',
      borderColor: 'border-border',
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 10a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" />
        </svg>
      ),
    },
    error: {
      label: 'Error',
      dotColor: 'bg-red-500',
      textColor: 'text-red-500',
      bgColor: 'bg-red-500/10',
      borderColor: 'border-red-500/30',
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      ),
    },
  };

  const config = statusConfig[status];
  const strokeDashoffset = 175.93 * (1 - countdownPercent / 100);

  return (
    <div className={`rounded-xl border ${config.borderColor} ${config.bgColor} p-4`}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          {/* Breathing status dot */}
          <div className="relative">
            <div
              className={`w-3 h-3 rounded-full ${config.dotColor} ${status === 'running' ? 'animate-pulse' : ''}`}
            />
            {status === 'running' && (
              <div
                className={`absolute inset-0 rounded-full ${config.dotColor} animate-ping opacity-50`}
              />
            )}
          </div>

          <div className={config.textColor}>{config.icon}</div>

          <div>
            <h4 className="font-semibold text-foreground">Bot Status</h4>
            <p className={`text-sm ${config.textColor}`}>{config.label}</p>
          </div>
        </div>

        {/* Countdown circle */}
        {status === 'running' && (
          <div className="relative w-12 h-12 sm:w-16 sm:h-16">
            <svg className="w-full h-full transform -rotate-90" viewBox="0 0 64 64">
              <circle
                cx="32"
                cy="32"
                r="28"
                stroke="hsl(var(--muted))"
                strokeWidth="4"
                fill="none"
              />
              <circle
                cx="32"
                cy="32"
                r="28"
                stroke="hsl(var(--primary))"
                strokeWidth="4"
                fill="none"
                strokeLinecap="round"
                strokeDasharray={175.93}
                strokeDashoffset={strokeDashoffset}
                className="transition-all duration-1000 ease-linear"
              />
            </svg>
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="text-[10px] sm:text-xs font-mono text-foreground">{countdown}</span>
            </div>
          </div>
        )}
      </div>

      {/* Info row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4 pt-4 border-t border-border/50">
        <div>
          <span className="text-xs text-muted-foreground">Last Trade</span>
          <p className="text-sm font-medium text-foreground">
            {lastTradeTime ? new Date(lastTradeTime).toLocaleString() : 'No trades yet'}
          </p>
        </div>
        <div>
          <span className="text-xs text-muted-foreground">Analysis Interval</span>
          <p className="text-sm font-medium text-foreground">
            {Math.floor(timerIntervalSec / 60)} minutes
          </p>
        </div>
      </div>
    </div>
  );
}

// Compact version for dashboard headers
export function BotStatusBadge({ status }: { status: 'running' | 'paused' | 'stopped' | 'error' }) {
  const config = {
    running: { label: 'Running', color: 'bg-green-500 text-white' },
    paused: { label: 'Paused', color: 'bg-yellow-500 text-white' },
    stopped: { label: 'Stopped', color: 'bg-muted text-muted-foreground' },
    error: { label: 'Error', color: 'bg-red-500 text-white' },
  };

  const { label, color } = config[status];

  return (
    <div className={`inline-flex items-center gap-2 px-3 py-1 rounded-full ${color}`}>
      <div
        className={`w-2 h-2 rounded-full ${status === 'running' ? 'bg-white animate-pulse' : 'bg-current opacity-50'}`}
      />
      <span className="text-sm font-medium">{label}</span>
    </div>
  );
}
