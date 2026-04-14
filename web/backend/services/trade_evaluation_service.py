"""
Trade Evaluation Service

Provides access to trade quality metrics from the trading system.

Data Source: data/trading_memory.json
- Written by: MultiAgentAnalyzer.record_outcome() (mechanical mode)
- Contains: trade evaluations with grades, R/R, execution quality, etc.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from core.config import settings

logger = logging.getLogger(__name__)


class TradeEvaluationService:
    """Service for accessing trade evaluation data from decision_memory"""

    def __init__(self):
        self.memory_file = Path(settings.ALGVEX_PATH) / "data" / "trading_memory.json"
        logger.info(f"TradeEvaluationService initialized, memory_file={self.memory_file}")

    def _load_memory(self) -> List[Dict[str, Any]]:
        """
        Load decision_memory from file.

        Returns
        -------
        List[Dict]
            List of trade memory entries with evaluation data
        """
        if not self.memory_file.exists():
            logger.warning(f"Memory file not found: {self.memory_file}")
            return []

        try:
            with open(self.memory_file, 'r') as f:
                data = json.load(f)
                if not isinstance(data, list):
                    logger.warning(f"Memory file is not a list: {type(data)}")
                    return []
                # Filter entries that have evaluation data
                evaluated = [m for m in data if m.get('evaluation')]
                logger.debug(
                    f"Loaded {len(data)} memories, {len(evaluated)} with evaluation "
                    f"from {self.memory_file}"
                )
                return evaluated
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse memory file {self.memory_file}: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to load memory file {self.memory_file}: {e}")
            return []

    def get_evaluation_summary(self, days: Optional[int] = None) -> Dict[str, Any]:
        """
        Get aggregate trade evaluation statistics.

        Parameters
        ----------
        days : int, optional
            Number of days to look back (None = all trades)

        Returns
        -------
        Dict[str, Any]
            Aggregate statistics:
            - total_evaluated: Number of evaluated trades
            - grade_distribution: Dict of grade counts
            - direction_accuracy: Percentage of profitable trades
            - avg_winning_rr: Average R/R for winning trades
            - avg_execution_quality: Average execution quality (0-2)
            - exit_type_distribution: Dict of exit type counts
            - confidence_accuracy: Dict of confidence level win rates
            - avg_hold_duration_min: Average hold time in minutes
        """
        memories = self._load_memory()

        # Filter by date if specified
        if days is not None:
            cutoff = datetime.now() - timedelta(days=days)
            memories = [
                m for m in memories
                if self._parse_timestamp(m.get('timestamp')) >= cutoff
            ]

        if not memories:
            return self._empty_summary()

        evals = [m['evaluation'] for m in memories]
        total = len(evals)

        # Grade distribution
        grade_counts = {}
        for e in evals:
            g = e.get('grade', '?')
            grade_counts[g] = grade_counts.get(g, 0) + 1

        # Direction accuracy (win rate)
        correct = sum(1 for e in evals if e.get('direction_correct'))
        direction_accuracy = round(correct / total * 100, 1) if total > 0 else 0.0

        # Average R/R for winning trades
        profitable_rrs = [e.get('actual_rr', 0) for e in evals if e.get('direction_correct')]
        avg_winning_rr = round(sum(profitable_rrs) / len(profitable_rrs), 2) if profitable_rrs else 0.0

        # Average execution quality
        exec_quals = [e.get('execution_quality', 0) for e in evals if e.get('execution_quality', 0) > 0]
        avg_exec_quality = round(sum(exec_quals) / len(exec_quals), 2) if exec_quals else 0.0

        # Exit type distribution
        exit_types = {}
        for e in evals:
            et = e.get('exit_type', 'UNKNOWN')
            exit_types[et] = exit_types.get(et, 0) + 1

        # Confidence accuracy (win rate per confidence level)
        # v5.12: Skip entries without explicit confidence field instead of
        # defaulting to MEDIUM, which would skew MEDIUM accuracy stats
        confidence_stats = {}
        for e in evals:
            conf = e.get('confidence')
            if not conf:
                continue  # v5.12: Do not assume MEDIUM for missing confidence
            conf = conf.upper()
            if conf not in confidence_stats:
                confidence_stats[conf] = {'total': 0, 'wins': 0}
            confidence_stats[conf]['total'] += 1
            if e.get('direction_correct'):
                confidence_stats[conf]['wins'] += 1

        for conf, stats in confidence_stats.items():
            stats['accuracy'] = round(stats['wins'] / stats['total'] * 100, 1) if stats['total'] > 0 else 0.0

        # Average hold duration
        durations = [e.get('hold_duration_min', 0) for e in evals if e.get('hold_duration_min', 0) > 0]
        avg_hold_min = round(sum(durations) / len(durations)) if durations else 0

        # Grade quality score (A+ = 5, A = 4, B = 3, C = 2, D = 1, D- = 0.5, F = 0)
        grade_scores = {'A+': 5, 'A': 4, 'B': 3, 'C': 2, 'D': 1, 'D-': 0.5, 'F': 0}
        total_score = sum(grade_scores.get(e.get('grade', 'F'), 0) for e in evals)
        avg_grade_score = round(total_score / total, 2) if total > 0 else 0.0

        # v11.5: SL/TP optimization stats
        mae_vals = [e.get('mae_pct', 0) for e in evals if e.get('mae_pct')]
        mfe_vals = [e.get('mfe_pct', 0) for e in evals if e.get('mfe_pct')]
        ct_count = sum(1 for e in evals if e.get('is_counter_trend'))
        avg_mae = round(sum(mae_vals) / len(mae_vals), 2) if mae_vals else 0.0
        avg_mfe = round(sum(mfe_vals) / len(mfe_vals), 2) if mfe_vals else 0.0

        result = {
            'total_evaluated': total,
            'grade_distribution': grade_counts,
            'direction_accuracy': direction_accuracy,
            'avg_winning_rr': avg_winning_rr,
            'avg_execution_quality': avg_exec_quality,
            'avg_grade_score': avg_grade_score,
            'exit_type_distribution': exit_types,
            'confidence_accuracy': confidence_stats,
            'avg_hold_duration_min': avg_hold_min,
            'last_updated': datetime.now().isoformat(),
        }

        # v11.5: Only include SL/TP stats if data exists
        if mae_vals or mfe_vals:
            result['avg_mae_pct'] = avg_mae
            result['avg_mfe_pct'] = avg_mfe
            result['counter_trend_count'] = ct_count
            result['counter_trend_pct'] = round(ct_count / total * 100, 1) if total > 0 else 0.0

        # v12.0: Reflection coverage stats
        reflections = [m for m in memories if m.get('reflection')]
        result['reflection_count'] = len(reflections)
        result['reflection_coverage_pct'] = round(len(reflections) / total * 100, 1) if total > 0 else 0.0

        return result

    def get_recent_trades(self, limit: int = 20, include_details: bool = False) -> List[Dict[str, Any]]:
        """
        Get recent trade evaluations.

        Parameters
        ----------
        limit : int
            Maximum number of trades to return
        include_details : bool
            If True, include full evaluation data (admin only)
            If False, exclude sensitive fields (public)

        Returns
        -------
        List[Dict]
            List of trade evaluations, newest first
        """
        memories = self._load_memory()

        # Sort by timestamp (newest first)
        memories.sort(key=lambda m: m.get('timestamp', ''), reverse=True)

        # Limit results
        memories = memories[:limit]

        if include_details:
            # Admin view - return full data including v12.0 reflection fields
            result = []
            for m in memories:
                entry = {
                    **m['evaluation'],
                    'pnl': m.get('pnl', 0),
                    'conditions': m.get('conditions', ''),
                    'lesson': m.get('lesson', ''),
                    'timestamp': m.get('timestamp', ''),
                }
                # v12.0: Include reflection fields
                if m.get('reflection'):
                    entry['reflection'] = m['reflection']
                if m.get('winning_side'):
                    entry['winning_side'] = m['winning_side']
                if m.get('original_lesson'):
                    entry['original_lesson'] = m['original_lesson']
                result.append(entry)
            return result
        else:
            # Public view - sanitize sensitive data
            result = []
            for m in memories:
                ev = m['evaluation']
                trade = {
                    'grade': ev.get('grade', '?'),
                    'planned_rr': ev.get('planned_rr', 0),
                    'actual_rr': ev.get('actual_rr', 0),
                    'execution_quality': ev.get('execution_quality', 0),
                    'exit_type': ev.get('exit_type', 'UNKNOWN'),
                    'confidence': ev.get('confidence', 'MEDIUM'),
                    'hold_duration_min': ev.get('hold_duration_min', 0),
                    'direction_correct': ev.get('direction_correct', False),
                    'timestamp': m.get('timestamp', ''),
                }
                # v11.5: Include SL/TP optimization fields if present
                if ev.get('is_counter_trend'):
                    trade['is_counter_trend'] = True
                if ev.get('mae_pct'):
                    trade['mae_pct'] = ev['mae_pct']
                if ev.get('mfe_pct'):
                    trade['mfe_pct'] = ev['mfe_pct']
                if ev.get('sl_atr_multiplier'):
                    trade['sl_atr_multiplier'] = ev['sl_atr_multiplier']
                if ev.get('trend_direction'):
                    trade['trend_direction'] = ev['trend_direction']
                # v12.0: Include reflection and winning side for public display
                reflection = m.get('reflection', '')
                if reflection:
                    trade['reflection_snippet'] = reflection
                if m.get('winning_side'):
                    trade['winning_side'] = m['winning_side']
                result.append(trade)
            return result

    def export_data(self, format: str = 'json', days: Optional[int] = None) -> Dict[str, Any]:
        """
        Export trade evaluation data for analysis.

        Parameters
        ----------
        format : str
            'json' or 'csv'
        days : int, optional
            Number of days to export (None = all)

        Returns
        -------
        Dict
            Export data with metadata
        """
        memories = self._load_memory()

        # Filter by date if specified
        if days is not None:
            cutoff = datetime.now() - timedelta(days=days)
            memories = [
                m for m in memories
                if self._parse_timestamp(m.get('timestamp')) >= cutoff
            ]

        if format == 'csv':
            # Convert to CSV-friendly flat structure
            csv_data = []
            for m in memories:
                eval_data = m.get('evaluation', {})
                row = {
                    'timestamp': m.get('timestamp', ''),
                    'decision': m.get('decision', ''),
                    'pnl': m.get('pnl', 0),
                    'grade': eval_data.get('grade', '?'),
                    'direction_correct': eval_data.get('direction_correct', False),
                    'entry_price': eval_data.get('entry_price', 0),
                    'exit_price': eval_data.get('exit_price', 0),
                    'planned_sl': eval_data.get('planned_sl', 0),
                    'planned_tp': eval_data.get('planned_tp', 0),
                    'planned_rr': eval_data.get('planned_rr', 0),
                    'actual_rr': eval_data.get('actual_rr', 0),
                    'execution_quality': eval_data.get('execution_quality', 0),
                    'exit_type': eval_data.get('exit_type', ''),
                    'confidence': eval_data.get('confidence', ''),
                    'position_size_pct': eval_data.get('position_size_pct', 0),
                    'hold_duration_min': eval_data.get('hold_duration_min', 0),
                    # v11.5: SL/TP optimization fields
                    'atr_value': eval_data.get('atr_value', ''),
                    'sl_atr_multiplier': eval_data.get('sl_atr_multiplier', ''),
                    'is_counter_trend': eval_data.get('is_counter_trend', False),
                    'risk_appetite': eval_data.get('risk_appetite', ''),
                    'trend_direction': eval_data.get('trend_direction', ''),
                    'adx': eval_data.get('adx', ''),
                    'mae_pct': eval_data.get('mae_pct', ''),
                    'mfe_pct': eval_data.get('mfe_pct', ''),
                    'conditions': m.get('conditions', ''),
                    'lesson': m.get('lesson', ''),
                    # v12.0: Reflection fields
                    'reflection': m.get('reflection', ''),
                    'winning_side': m.get('winning_side', ''),
                }
                csv_data.append(row)

            return {
                'format': 'csv',
                'data': csv_data,
                'count': len(csv_data),
                'exported_at': datetime.now().isoformat(),
            }
        else:
            # JSON format - return full structure
            return {
                'format': 'json',
                'data': memories,
                'count': len(memories),
                'exported_at': datetime.now().isoformat(),
            }

    def get_performance_attribution(self, days: Optional[int] = None) -> Dict[str, Any]:
        """
        v14.0: Performance attribution — break down PnL by exit type, confidence,
        and trend/counter-trend direction.

        Returns
        -------
        Dict with keys: by_exit_type, by_confidence, by_trend, by_grade
        """
        memories = self._load_memory()

        if days is not None:
            cutoff = datetime.now() - timedelta(days=days)
            memories = [
                m for m in memories
                if self._parse_timestamp(m.get('timestamp')) >= cutoff
            ]

        if not memories:
            return {
                'by_exit_type': {},
                'by_confidence': {},
                'by_trend': {},
                'by_grade': {},
                'total_trades': 0,
            }

        # Helper to build attribution bucket
        def _bucket(key_fn):
            buckets = {}
            for m in memories:
                ev = m.get('evaluation', {})
                pnl = m.get('pnl', 0)
                try:
                    pnl = float(pnl)
                except (ValueError, TypeError):
                    pnl = 0
                key = key_fn(m, ev)
                if key not in buckets:
                    buckets[key] = {'count': 0, 'total_pnl': 0, 'wins': 0, 'losses': 0}
                buckets[key]['count'] += 1
                buckets[key]['total_pnl'] = round(buckets[key]['total_pnl'] + pnl, 2)
                if pnl > 0:
                    buckets[key]['wins'] += 1
                elif pnl < 0:
                    buckets[key]['losses'] += 1
            # Add win rate
            for b in buckets.values():
                b['win_rate'] = round(b['wins'] / b['count'] * 100, 1) if b['count'] > 0 else 0
                b['avg_pnl'] = round(b['total_pnl'] / b['count'], 2) if b['count'] > 0 else 0
            return buckets

        by_exit_type = _bucket(lambda m, ev: ev.get('exit_type', 'UNKNOWN'))
        by_confidence = _bucket(lambda m, ev: ev.get('confidence', 'UNKNOWN'))
        by_trend = _bucket(
            lambda m, ev: 'Counter-Trend' if ev.get('is_counter_trend') else 'Trend-Following'
        )
        by_grade = _bucket(lambda m, ev: ev.get('grade', '?'))

        return {
            'by_exit_type': by_exit_type,
            'by_confidence': by_confidence,
            'by_trend': by_trend,
            'by_grade': by_grade,
            'total_trades': len(memories),
            'last_updated': datetime.now().isoformat(),
        }

    def _empty_summary(self) -> Dict[str, Any]:
        """Return empty summary structure when no data available"""
        return {
            'total_evaluated': 0,
            'grade_distribution': {},
            'direction_accuracy': 0.0,
            'avg_winning_rr': 0.0,
            'avg_execution_quality': 0.0,
            'avg_grade_score': 0.0,
            'exit_type_distribution': {},
            'confidence_accuracy': {},
            'avg_hold_duration_min': 0,
            'last_updated': datetime.now().isoformat(),
        }

    def _parse_timestamp(self, ts: Optional[str]) -> datetime:
        """Parse ISO timestamp string to naive datetime (always strips timezone).

        This ensures consistent comparison with datetime.now() which is naive.
        Timestamps may come from different sources with or without timezone info:
        - Bot record_outcome: datetime.now().isoformat() → naive
        - E2E test / UTC: datetime.now(timezone.utc).isoformat() → aware (+00:00)
        Mixing aware and naive in comparisons raises TypeError.
        """
        if not ts:
            return datetime.min
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            # Always return naive datetime for consistent comparison
            return dt.replace(tzinfo=None)
        except (ValueError, TypeError):
            pass
        return datetime.min


# Singleton instance
_service_instance = None


def get_trade_evaluation_service() -> TradeEvaluationService:
    """Get singleton instance of TradeEvaluationService"""
    global _service_instance
    if _service_instance is None:
        _service_instance = TradeEvaluationService()
    return _service_instance
