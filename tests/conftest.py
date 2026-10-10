import pytest

from pipeline import gate


@pytest.fixture(autouse=True)
def _posting_time_reached(monkeypatch):
    """Tests run at any time of day: pretend each video's posting time has come unless a test says otherwise."""
    monkeypatch.setattr(gate, "due", lambda job_id, now=None: True)
    gate._answered.clear()
    gate._labels.clear()
