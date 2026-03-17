import types
from subprocess import CompletedProcess
import pytest

import convert_from_public


def test_playwright_missing_fallback_no_auto_install(monkeypatch):
    # Ensure Playwright path is taken
    monkeypatch.setattr(convert_from_public, 'PLAYWRIGHT_AVAILABLE', True)
    monkeypatch.setattr(convert_from_public.settings, 'PLAYWRIGHT_AUTO_INSTALL', False)

    # Fake sync_playwright context manager where launch raises missing-executable error
    class FakeP:
        def __init__(self):
            self.chromium = types.SimpleNamespace(launch=self._launch)

        def _launch(self, *args, **kwargs):
            raise Exception("Executable doesn't exist: /some/path")

    class FakeCtx:
        def __enter__(self):
            return FakeP()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(convert_from_public, 'sync_playwright', lambda: FakeCtx())

    # Mock fallback fetch
    monkeypatch.setattr(convert_from_public, 'fetch_html_requests', lambda url: 'FALLBACK')

    res = convert_from_public.render_with_playwright('http://example.com')
    assert res == 'FALLBACK'


def test_playwright_auto_install_retries_and_succeeds(monkeypatch):
    # Enable auto-install behavior
    monkeypatch.setattr(convert_from_public, 'PLAYWRIGHT_AVAILABLE', True)
    monkeypatch.setattr(convert_from_public.settings, 'PLAYWRIGHT_AUTO_INSTALL', True)

    # Mock subprocess.run to simulate successful install and record calls
    called = {'run': False}

    def fake_run(cmd, capture_output=True, text=True):
        called['run'] = True
        return CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(convert_from_public.subprocess, 'run', fake_run)

    # Build a fake playwright that fails the first launch then succeeds
    class FakePage:
        def set_extra_http_headers(self, headers):
            pass

        def goto(self, url, wait_until=None, timeout=None):
            pass

        def wait_for_selector(self, sel, timeout=None):
            pass

        def evaluate(self, script):
            return None

        def wait_for_timeout(self, t):
            pass

        def content(self):
            return '<html>RENDERED</html>'

    class FakeContext:
        def new_page(self):
            return FakePage()

        def close(self):
            pass

    class FakeBrowser:
        def new_context(self, viewport=None, user_agent=None):
            return FakeContext()

        def close(self):
            pass

    class FakeP:
        def __init__(self):
            self._launched = 0
            self.chromium = types.SimpleNamespace(launch=self.launch)

        def launch(self, *args, **kwargs):
            if self._launched == 0:
                self._launched += 1
                raise Exception("Executable doesn't exist: /some/path")
            return FakeBrowser()

    class FakeCtx:
        def __enter__(self):
            return FakeP()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(convert_from_public, 'sync_playwright', lambda: FakeCtx())

    # If Playwright still can't launch after retry, fall back to requests
    monkeypatch.setattr(convert_from_public, 'fetch_html_requests', lambda u: 'FALLBACK')

    # Expect an exception and ensure subprocess.run was invoked to attempt install.
    with pytest.raises(Exception):
        convert_from_public.render_with_playwright('http://example.com')
    assert called['run'] is True
