import pytest

from pipeline import gate


@pytest.fixture(autouse=True)
def _inside_posting_window(monkeypatch):
    """Tests run at any time of day: pretend we're inside a posting window unless a test says otherwise."""
    monkeypatch.setattr(gate, "in_window", lambda now=None: True)
    gate._answered.clear()
    gate._labels.clear()
