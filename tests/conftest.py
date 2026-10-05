import os

import pytest

os.environ.setdefault("OPT_OFFLINE", "1")


@pytest.fixture
def tmp_store(tmp_path):
    from engine.core.store import StateStore

    return StateStore(tmp_path / "state")
