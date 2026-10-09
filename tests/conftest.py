import os

import pytest
from hypothesis import settings

os.environ.setdefault("OPT_OFFLINE", "1")

# The suite is deterministic: a property test draws the same examples on every run, so a red cross on a commit
# that touched nothing of the sort never comes from the luck of the draw (it did once: a property that was
# wrong about buy-backs passed 150 random examples for hours and then failed two runs in a row). To SEARCH for
# counterexamples, which is what these tests are for when the code under them changes, run them at random:
#   HYPOTHESIS_PROFILE=explore pytest tests/test_broker_orders.py --hypothesis-seed=7
settings.register_profile("deterministic", derandomize=True)
settings.register_profile("explore", derandomize=False)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "deterministic"))


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
