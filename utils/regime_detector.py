"""
HMM-based Market Regime Detector (4-State).

Replaces ADX threshold binary logic with probabilistic Hidden Markov Model.
States: TRENDING_UP / TRENDING_DOWN / RANGING / HIGH_VOLATILITY

Uses 5 observable features: [log_return, atr_pct, adx, volume_ratio, rsi_normalized]
from the 4H decision layer timeframe.

Reference: docs/upgrade_plan_v2/02_DATA_QUALITY.md §1

Backward compatible: Falls back to ADX threshold logic if HMM is not trained
or if prediction fails. Production code always gets a valid regime string.
"""

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Persistence paths
_PROJECT_ROOT = Path(__file__).parent.parent
_HMM_MODEL_DIR = _PROJECT_ROOT / "data" / "hmm"
_HMM_MODEL_FILE = _HMM_MODEL_DIR / "regime_model.pkl"
_HMM_META_FILE = _HMM_MODEL_DIR / "regime_meta.json"

# State labels (mapped from HMM hidden states after training)
_STATE_LABELS = ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY"]

# Feature names for the observation vector
_FEATURE_NAMES = ["log_return", "atr_pct", "adx", "volume_ratio", "rsi_normalized"]

# ADX fallback thresholds (backward compatible with v39.0-v44.0)
_ADX_THRESHOLD_STRONG = 40
_ADX_THRESHOLD_WEAK = 25


class RegimeDetector:
    """HMM 4-state regime detector with ADX fallback.

    Lifecycle:
    1. fit(features_history) — train on 60+ days of 4H data
    2. predict(current_features) — returns regime + probabilities
    3. Retrain every 7 days via cron or on_timer check

    Fallback: If model not trained or prediction fails, uses ADX threshold
    logic identical to v44.0 (max(adx_1d, adx_4h) thresholds).
    """

    def __init__(self, config: Optional[Dict] = None):
        self._config = config or {}
        hmm_config = self._config.get("hmm", {})

        self._n_states = hmm_config.get("n_states", 4)
        self._lookback_days = hmm_config.get("lookback_days", 60)
        self._retrain_interval_days = hmm_config.get("retrain_interval_days", 7)
        self._hysteresis_cycles = hmm_config.get("hysteresis_cycles", 2)
        self._enabled = hmm_config.get("enabled", False)

        self._model = None
        self._state_mapping: Dict[int, str] = {}  # HMM state index → label
        self._current_regime: Optional[str] = None
        self._regime_counter = 0
        self._last_train_date: Optional[datetime] = None

        # Try loading persisted model
        if self._enabled:
            self._load_model()

    # =========================================================================
    # Training
    # =========================================================================

    def fit(self, features_history: np.ndarray) -> bool:
        """Train HMM on historical 4H feature data.

        Parameters
        ----------
        features_history : np.ndarray, shape (T, 5)
            Columns: [log_return, atr_pct, adx, volume_ratio, rsi_normalized]
            T should be >= 60 days × 6 bars/day = 360 rows

        Returns
        -------
        bool: True if training succeeded
        """
        try:
            from hmmlearn.hmm import GaussianHMM
        except ImportError:
            logger.error("hmmlearn not installed — cannot train HMM")
            return False

        if features_history.shape[0] < 100:
            logger.warning(f"Insufficient data: {features_history.shape[0]} rows (need ≥100)")
            return False

        if features_history.shape[1] != len(_FEATURE_NAMES):
            logger.error(f"Expected {len(_FEATURE_NAMES)} features, got {features_history.shape[1]}")
            return False

        try:
            model = GaussianHMM(
                n_components=self._n_states,
                covariance_type="full",
                n_iter=200,
                random_state=42,
            )
            model.fit(features_history)

            # Label states based on mean characteristics
            self._state_mapping = self._label_states(model, features_history)
            self._model = model
            self._last_train_date = datetime.now(timezone.utc)

            # Persist
            self._save_model()

            logger.info(
                f"HMM trained: {features_history.shape[0]} samples, "
                f"states={self._state_mapping}"
            )
            return True

        except Exception as e:
            logger.error(f"HMM training failed: {e}")
            return False

    def _label_states(self, model, features: np.ndarray) -> Dict[int, str]:
        """Map HMM hidden state indices to semantic labels based on state means.

        Logic:
        - Highest ATR% mean → HIGH_VOLATILITY
        - Highest log_return mean (of remaining) → TRENDING_UP
        - Lowest log_return mean (of remaining) → TRENDING_DOWN
        - Remaining → RANGING
        """
        means = model.means_  # shape (n_states, 5)
        # Feature indices: 0=log_return, 1=atr_pct, 2=adx, 3=volume_ratio, 4=rsi

        assigned = {}
        remaining = list(range(self._n_states))

        # 1. HIGH_VOLATILITY: highest atr_pct
        vol_idx = max(remaining, key=lambda i: means[i][1])
        assigned[vol_idx] = "HIGH_VOLATILITY"
        remaining.remove(vol_idx)

        if not remaining:
            return assigned

        # 2. TRENDING_UP: highest log_return (of remaining)
        up_idx = max(remaining, key=lambda i: means[i][0])
        assigned[up_idx] = "TRENDING_UP"
        remaining.remove(up_idx)

        if not remaining:
            return assigned

        # 3. TRENDING_DOWN: lowest log_return (of remaining)
        down_idx = min(remaining, key=lambda i: means[i][0])
        assigned[down_idx] = "TRENDING_DOWN"
        remaining.remove(down_idx)

        # 4. RANGING: whatever is left
        for idx in remaining:
            assigned[idx] = "RANGING"

        return assigned

    # =========================================================================
    # Prediction
    # =========================================================================

    def predict(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """Predict current regime from feature dict.

        Parameters
        ----------
        features : Dict
            Must contain keys from FEATURE_SCHEMA:
            log_return_4h, atr_pct_4h, adx_4h, volume_ratio_4h, rsi_4h

        Returns
        -------
        Dict with keys:
            regime: str (STRONG_TREND/WEAK_TREND/RANGING or HMM 4-state)
            probabilities: Dict[str, float] (HMM only)
            confidence: float (max probability)
            transition_risk: float (1 - confidence)
            source: str ("hmm" or "adx_fallback")
        """
        # Try HMM first (if enabled and model trained)
        if self._enabled and self._model is not None:
            try:
                obs = self._extract_observation(features)
                if obs is not None:
                    return self._predict_hmm(obs)
            except Exception as e:
                logger.warning(f"HMM prediction failed, falling back to ADX: {e}")

        # Fallback: ADX threshold (v44.0 compatible)
        return self._predict_adx_fallback(features)

    def _extract_observation(self, features: Dict[str, Any]) -> Optional[np.ndarray]:
        """Extract 5-feature observation vector from feature dict."""
        try:
            obs = np.array([[
                float(features.get("log_return_4h", 0.0)),
                float(features.get("atr_pct_4h", 0.0)),
                float(features.get("adx_4h", 0.0)),
                float(features.get("volume_ratio_4h", 0.0)),
                float(features.get("rsi_4h", 50.0)) / 100.0,  # Normalize to [0,1]
            ]])
            # Validate no NaN/inf
            if not np.all(np.isfinite(obs)):
                return None
            return obs
        except (ValueError, TypeError):
            return None

    def _predict_hmm(self, obs: np.ndarray) -> Dict[str, Any]:
        """HMM prediction with hysteresis."""
        probs = self._model.predict_proba(obs)[0]
        state_idx = int(np.argmax(probs))
        candidate = self._state_mapping.get(state_idx, "RANGING")

        # Hysteresis: require N consecutive cycles before switching
        if candidate != self._current_regime:
            self._regime_counter += 1
            if self._regime_counter >= self._hysteresis_cycles:
                self._current_regime = candidate
                self._regime_counter = 0
        else:
            self._regime_counter = 0

        # Build probability dict with semantic labels
        prob_dict = {}
        for idx, prob in enumerate(probs):
            label = self._state_mapping.get(idx, f"STATE_{idx}")
            prob_dict[label] = round(float(prob), 4)

        return {
            "regime": self._current_regime or candidate,
            "probabilities": prob_dict,
            "confidence": round(float(max(probs)), 4),
            "transition_risk": round(float(1.0 - max(probs)), 4),
            "source": "hmm",
        }

    def _predict_adx_fallback(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """ADX threshold fallback — identical to v44.0 logic."""
        adx_1d = float(features.get("adx_1d", 30.0))
        adx_4h = float(features.get("adx_4h", 0.0))
        effective_adx = max(adx_1d, adx_4h)

        if effective_adx >= _ADX_THRESHOLD_STRONG:
            regime = "STRONG_TREND"
        elif effective_adx >= _ADX_THRESHOLD_WEAK:
            regime = "WEAK_TREND"
        else:
            regime = "RANGING"

        return {
            "regime": regime,
            "probabilities": {},
            "confidence": 1.0,
            "transition_risk": 0.0,
            "source": "adx_fallback",
        }

    # =========================================================================
    # Retrain Check
    # =========================================================================

    def needs_retrain(self) -> bool:
        """Check if model should be retrained based on interval."""
        if not self._enabled:
            return False
        if self._last_train_date is None:
            return True
        days_since = (datetime.now(timezone.utc) - self._last_train_date).days
        return days_since >= self._retrain_interval_days

    # =========================================================================
    # Persistence
    # =========================================================================

    def _save_model(self) -> None:
        """Save model + metadata to disk."""
        try:
            import joblib
            _HMM_MODEL_DIR.mkdir(parents=True, exist_ok=True)
            joblib.dump(self._model, _HMM_MODEL_FILE)
            meta = {
                "state_mapping": {str(k): v for k, v in self._state_mapping.items()},
                "last_train_date": self._last_train_date.isoformat() if self._last_train_date else None,
                "n_states": self._n_states,
                "current_regime": self._current_regime,
            }
            with open(_HMM_META_FILE, "w") as f:
                json.dump(meta, f, indent=2)
            logger.info(f"HMM model saved to {_HMM_MODEL_FILE}")
        except Exception as e:
            logger.warning(f"Failed to save HMM model: {e}")

    def _load_model(self) -> None:
        """Load model + metadata from disk."""
        try:
            if not _HMM_MODEL_FILE.exists() or not _HMM_META_FILE.exists():
                logger.info("No persisted HMM model found — will need training")
                return
            import joblib
            self._model = joblib.load(_HMM_MODEL_FILE)
            with open(_HMM_META_FILE) as f:
                meta = json.load(f)
            self._state_mapping = {int(k): v for k, v in meta.get("state_mapping", {}).items()}
            train_date = meta.get("last_train_date")
            if train_date:
                self._last_train_date = datetime.fromisoformat(train_date)
            self._current_regime = meta.get("current_regime")
            logger.info(
                f"HMM model loaded: states={self._state_mapping}, "
                f"trained={self._last_train_date}"
            )
        except Exception as e:
            logger.warning(f"Failed to load HMM model: {e}")
            self._model = None
