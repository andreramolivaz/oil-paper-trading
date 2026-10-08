import os

import pytest

os.environ.setdefault("OPT_OFFLINE", "1")


@pytest.fixture
def tmp_store(tmp_path):
    from engine.core.store import StateStore

    return StateStore(tmp_path / "state")


@pytest.fixture(autouse=True)
def _yahoo_not_rate_limited():
    """The Yahoo adapter remembers a rate limit for the whole process: no test may inherit one from another."""
    from engine.data.adapters.yahoo import YahooAdapter

    YahooAdapter._rate_limited_until = 0.0
    yield
    YahooAdapter._rate_limited_until = 0.0
