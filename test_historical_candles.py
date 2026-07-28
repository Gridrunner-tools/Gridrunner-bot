import main


def test_fetch_historical_candles_returns_empty_on_exception():
    original_get = main.requests.get
    try:
        def boom(*args, **kwargs):
            raise RuntimeError("network down")

        main.requests.get = boom
        candles = main.fetch_historical_candles("ETH/USDT", hours=6, timeout_sec=1)
        assert candles == []
        print("PASS test_fetch_historical_candles_returns_empty_on_exception")
    finally:
        main.requests.get = original_get


def test_fetch_historical_candles_uses_timeout_and_caps_to_1440():
    original_get = main.requests.get
    original_time = main.time.time
    captured = {}
    try:
        class FakeResponse:
            def json(self):
                rows = [[0, 0, 0, 0, str(i), 0, 0, 0] for i in range(1500)]
                return {"error": [], "result": {"ETHUSD": rows, "last": 0}}

        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            captured["timeout"] = timeout
            return FakeResponse()

        main.requests.get = fake_get
        main.time.time = lambda: 1000

        candles = main.fetch_historical_candles("ETH/USDT", hours=6, timeout_sec=3)

        assert captured["url"].endswith("/0/public/OHLC")
        assert captured["params"]["pair"] == "ETHUSD"
        assert captured["timeout"] == 3
        assert len(candles) == 1440
        print("PASS test_fetch_historical_candles_uses_timeout_and_caps_to_1440")
    finally:
        main.requests.get = original_get
        main.time.time = original_time


if __name__ == "__main__":
    test_fetch_historical_candles_returns_empty_on_exception()
    test_fetch_historical_candles_uses_timeout_and_caps_to_1440()
    print("\nAll 2 historical candle tests passed.")
