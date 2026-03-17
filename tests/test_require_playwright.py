import sys
import pytest
import convert_from_public

def test_require_playwright_exits_when_missing(monkeypatch):
    # Simulate Playwright library missing
    monkeypatch.setattr(convert_from_public, 'PLAYWRIGHT_AVAILABLE', False)
    # Provide CLI args requesting Playwright be required
    monkeypatch.setattr(sys, 'argv', ['convert_from_public.py', '--require-playwright', '--page-url', 'http://example.com'])
    with pytest.raises(SystemExit) as exc:
        convert_from_public.main()
    assert exc.value.code == 2
