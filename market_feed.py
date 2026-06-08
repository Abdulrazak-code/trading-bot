r"""
Shared Upstox WebSocket V3 feed for all bots under D:\Bots.
Streams real-time LTP via MarketDataStreamerV3 in a daemon thread.
Usage:
    from market_feed import UpstoxFeed
    feed = UpstoxFeed(config.UPSTOX_ACCESS_TOKEN)
    feed.start(["NSE_EQ|INFY", "NSE_EQ|TCS"])
    price = feed.get_price("NSE_EQ|INFY")  # float | None
"""
import threading
import upstox_client


class UpstoxFeed:
    """Real-time LTP feed via Upstox WebSocket V3."""

    def __init__(self, access_token: str):
        self._token = access_token
        self._prices: dict = {}
        self._lock = threading.Lock()
        self._streamer = None
        self._started = False

    def start(self, instrument_keys: list):
        """Start WebSocket stream in a background daemon thread."""
        if not instrument_keys or self._started:
            return
        self._started = True
        self._launch(list(set(instrument_keys)))

    def _launch(self, instrument_keys: list):
        api_client = upstox_client.ApiClient()
        api_client.configuration.access_token = self._token

        self._streamer = upstox_client.MarketDataStreamerV3(
            api_client=api_client,
            instrumentKeys=instrument_keys,
            mode="ltpc",
        )
        self._streamer.auto_reconnect(enable=True, interval=5, retry_count=100)
        self._streamer.on("message", self._on_message)
        self._streamer.on("error",   self._on_error)

        t = threading.Thread(target=self._streamer.connect, daemon=True, name="UpstoxFeed")
        t.start()

    def _on_message(self, message: dict):
        feeds = message.get("feeds", {})
        with self._lock:
            for ikey, feed in feeds.items():
                ltp = (feed.get("ltpc") or {}).get("ltp")
                if ltp is not None:
                    self._prices[ikey] = float(ltp)

    def _on_error(self, error):
        pass  # SDK auto-reconnects

    def get_price(self, instrument_key: str) -> float | None:
        with self._lock:
            return self._prices.get(instrument_key)

    def subscribe(self, instrument_keys: list):
        """Add more instruments to an already-running stream."""
        if not self._streamer or not instrument_keys:
            return
        try:
            self._streamer.subscribe(instrument_keys, "ltpc")
        except Exception:
            pass
