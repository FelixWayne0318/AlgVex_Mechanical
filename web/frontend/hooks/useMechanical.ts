import useSWR from 'swr';

// Types for mechanical state
export interface DimensionScore {
  score: number;
  direction: string;
  raw?: number;
}

export interface ZoneConditions {
  extension_4h: boolean;
  extension_4h_regime: string;
  rsi_oversold: boolean;
  rsi_30m: number;
  rsi_4h: number;
  cvd_accumulation: boolean;
  cvd_30m: string;
  cvd_4h: string;
  sr_proximity: boolean;
  sr_distance_atr: number;
  sr_strength: string;
}

export interface MechanicalState {
  status: string;
  timestamp: string;
  price: number;
  net_raw: number;
  signal: string;
  signal_tier: string;
  structure: DimensionScore;
  divergence: DimensionScore;
  order_flow: DimensionScore;
  regime: string;
  trend_context: string;
  zone_conditions: ZoneConditions;
  zone_count: number;
  direction_lock: { LONG: number; SHORT: number };
  thresholds: { high: number; med: number; low: number };
}

export interface SignalHistoryEntry {
  timestamp: string;
  price: number;
  net_raw: number;
  signal: string;
  tier: string;
  structure_dir: string;
  structure_score: number;
  divergence_dir: string;
  divergence_score: number;
  order_flow_dir: string;
  order_flow_score: number;
  regime: string;
  zone_count: number;
}

export interface ScoreTimeseriesEntry {
  timestamp: string;
  net_raw: number;
  structure: number;
  divergence: number;
  order_flow: number;
  price: number;
}

export function useMechanicalState() {
  const { data, error, isLoading } = useSWR<MechanicalState>(
    '/api/public/mechanical/state',
    { refreshInterval: 10000 }
  );
  return { state: data, error, isLoading };
}

export function useMechanicalHistory(limit: number = 50) {
  const { data, error, isLoading } = useSWR<SignalHistoryEntry[]>(
    `/api/public/mechanical/signal-history?limit=${limit}`,
    { refreshInterval: 30000 }
  );
  return { history: data, error, isLoading };
}

export function useMechanicalTimeSeries(hours: number = 24) {
  const { data, error, isLoading } = useSWR<ScoreTimeseriesEntry[]>(
    `/api/public/mechanical/score-timeseries?hours=${hours}`,
    { refreshInterval: 60000 }
  );
  return { series: data, error, isLoading };
}
