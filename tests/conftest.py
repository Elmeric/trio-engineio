import pytest


@pytest.fixture
def mock_time(monkeypatch):
    def _time():
        return "123.456"

    monkeypatch.setattr("trio_engineio.eio_types.time.time", _time)
