# utils/binance_orderbook_client.py

import requests
import logging
import time
from typing import Optional, Dict


class BinanceOrderBookClient:
    """
    Binance 订单簿客户端

    功能:
    - 获取订单簿深度数据 (/fapi/v1/depth)
    - 支持重试和降级
    - 遵循现有客户端模式

    注意: 此接口无需 API Key，是公开数据

    参考:
    - https://binance-docs.github.io/apidocs/futures/en/#order-book
    """

    BASE_URL = "https://fapi.binance.com"

    def __init__(
        self,
        timeout: int = 10,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        logger: logging.Logger = None,
    ):
        """
        初始化订单簿客户端

        Parameters
        ----------
        timeout : int
            请求超时时间 (秒)
        max_retries : int
            最大重试次数
        retry_delay : float
            重试延迟 (秒)
        logger : logging.Logger, optional
            日志记录器
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.logger = logger or logging.getLogger(__name__)

    def get_order_book(
        self,
        symbol: str = "BTCUSDT",
        limit: int = 100,
    ) -> Optional[Dict]:
        """
        获取订单簿深度

        Parameters
        ----------
        symbol : str
            交易对 (如 BTCUSDT)
        limit : int
            深度档位数 (5, 10, 20, 50, 100, 500, 1000)
            默认 100 档

        Returns
        -------
        Optional[Dict]
            订单簿数据字典，失败返回 None

            成功返回:
            {
                "lastUpdateId": 160,
                "E": 1499404346076,        # 消息时间 (ms)
                "T": 1499404346076,        # 撮合引擎时间 (ms)
                "bids": [
                    ["4.00000000", "431.00000000"],  # [价格, 数量]
                    ...
                ],
                "asks": [
                    ["4.00000200", "12.00000000"],
                    ...
                ]
            }
        """
        url = f"{self.BASE_URL}/fapi/v1/depth"
        params = {
            "symbol": symbol,
            "limit": limit,
        }

        # 重试逻辑
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                )

                # 处理限流 (429)
                if response.status_code == 429:
                    if attempt < self.max_retries:
                        wait_time = self.retry_delay * (2 ** attempt)
                        self.logger.warning(
                            f"⚠️ Rate limited (429), waiting {wait_time}s before retry {attempt+1}/{self.max_retries}"
                        )
                        time.sleep(wait_time)
                        continue
                    else:
                        self.logger.error("❌ Rate limited, max retries exceeded")
                        return None

                # 处理成功响应
                if response.status_code == 200:
                    data = response.json()

                    # 验证数据完整性
                    if not self._validate_orderbook(data):
                        self.logger.error("❌ Invalid order book data received")
                        return None

                    return data

                # 处理其他错误
                else:
                    self.logger.warning(
                        f"⚠️ Binance order book API error: {response.status_code}, "
                        f"attempt {attempt+1}/{self.max_retries+1}"
                    )

                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay)
                        continue
                    else:
                        return None

            except requests.exceptions.Timeout:
                self.logger.warning(
                    f"⚠️ Request timeout after {self.timeout}s, "
                    f"attempt {attempt+1}/{self.max_retries+1}"
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue
                else:
                    return None

            except Exception as e:
                self.logger.error(f"❌ Order book fetch error: {e}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue
                else:
                    return None

        return None

    def _validate_orderbook(self, data: Dict) -> bool:
        """
        验证订单簿数据合法性

        检查:
        - bids/asks 非空
        - 价格单调性 (bids 递减，asks 递增)
        - spread > 0

        Parameters
        ----------
        data : Dict
            订单簿数据

        Returns
        -------
        bool
            True 如果数据合法，否则 False
        """
        try:
            # 检查必需字段
            if "bids" not in data or "asks" not in data:
                self.logger.error("Missing bids/asks in order book data")
                return False

            bids = data["bids"]
            asks = data["asks"]

            # 检查非空
            if not bids or not asks:
                self.logger.error("Empty bids or asks in order book")
                return False

            # 检查价格单调性 (bids 递减)
            bid_prices = [float(b[0]) for b in bids]
            if not all(bid_prices[i] >= bid_prices[i+1] for i in range(len(bid_prices)-1)):
                self.logger.error("Bid prices not monotonically decreasing")
                return False

            # 检查价格单调性 (asks 递增)
            ask_prices = [float(a[0]) for a in asks]
            if not all(ask_prices[i] <= ask_prices[i+1] for i in range(len(ask_prices)-1)):
                self.logger.error("Ask prices not monotonically increasing")
                return False

            # 检查 spread > 0 (不允许交叉盘)
            best_bid = bid_prices[0]
            best_ask = ask_prices[0]
            if best_ask <= best_bid:
                self.logger.error(f"Invalid spread: best_bid={best_bid}, best_ask={best_ask}")
                return False

            return True

        except (KeyError, IndexError, ValueError, TypeError) as e:
            self.logger.error(f"Order book validation error: {e}")
            return False
