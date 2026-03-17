import time
import importlib
import settings
import page_renderer


class DummyPage:
    def evaluate(self, *args, **kwargs):
        return {'unknown': 0, 'shimmer': 0, 'scrolled': 0}

    def wait_for_timeout(self, ms):
        return


def test_hydration_retry_delay(monkeypatch):
    # Set very small retry delay
    monkeypatch.setattr(settings, 'HYDRATION_RETRY_DELAY_MS', 10)

    slept = []

    def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(time, 'sleep', fake_sleep)

    page_renderer.hydrate_cycle(DummyPage(), max_rounds=2, scroll_steps=1, wait_ms=50, click_toggles=False)

    # Expect at least one call to sleep with ~0.01 seconds
    assert any(abs(s - 0.01) < 0.005 for s in slept)
