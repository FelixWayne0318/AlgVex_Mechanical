"""
Fear & Greed Index Client.

Fetches the Crypto Fear & Greed Index from alternative.me API (free, no API key).
Updated daily. Value range: 0 (Extreme Fear) to 100 (Extreme Greed).

Reference: docs/upgrade_plan_v2/02_DATA_QUALITY.md §2

Integration:
- extract_features() → features["fear_greed_index"] (int 0-100)
- Scoring: <20 or >80 → risk_env +1 (extreme sentiment = risk signal)
- Tags: EXTREME_FEAR, EXTREME_GREED
"""

import logging
from typing import Dict, Any, Optional

import requests
from utils.http_retry import api_retry

logger = logging.getLogger(__name__)


class FearGreedClient:
    """Crypto Fear & Greed Index from alternative.me (free API, daily update)."""

    _URL = "https://api.alternative.me/fng/"

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def fetch(self) -> Optional[Dict[str, Any]]:
        """Fetch current Fear & Greed index.

        Returns
        -------
        Dict or None
            {
                'value': 25,              # 0-100
                'classification': 'Extreme Fear',  # Text label
                'timestamp': '1234567890',
                'is_extreme': True,       # value < 20 or > 80
            }
        """
        try:
            data = self._fetch_with_retry()
            if not data:
                return None

            items = data.get("data", [])
            if not items:
                logger.warning("Fear & Greed API returned empty data")
                return None

            latest = items[0]
            value = int(latest.get("value", 50))
            classification = latest.get("value_classification", "Neutral")

            return {
                "value": value,
                "classification": classification,
                "timestamp": latest.get("timestamp", ""),
                "is_extreme": value < 20 or value > 80,
            }
        except Exception as e:
            logger.warning(f"Fear & Greed fetch failed: {e}")
            return None

    @api_retry
    def _fetch_with_retry(self) -> Optional[Dict]:
        params = {"limit": 1, "format": "json"}
        response = requests.get(self._URL, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
