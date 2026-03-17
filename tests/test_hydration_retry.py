import types

import convert_from_public
import page_renderer
import notion_utils


def test_hydration_retry(monkeypatch):
    # Ensure Playwright path is used
    monkeypatch.setattr(convert_from_public, 'PLAYWRIGHT_AVAILABLE', True)

    # Counter to track hydrate_cycle calls
    called = {'count': 0}

    def fake_hydrate_cycle(page, *args, **kwargs):
        called['count'] += 1
        # no-op otherwise

    # Patch the page_renderer.hydrate_cycle
    monkeypatch.setattr(page_renderer, 'hydrate_cycle', fake_hydrate_cycle)

    # Ensure notion_utils.detect_placeholders_in_html is the real implementation
    monkeypatch.setattr(notion_utils, 'detect_placeholders_in_html', notion_utils.detect_placeholders_in_html)

    # Build fake Playwright context manager and objects
    class FakePage:
        def __init__(self):
            self._hydrated = False

        def set_extra_http_headers(self, headers):
            pass

        def goto(self, url, wait_until=None, timeout=None):
            # no-op
            return None

        def wait_for_selector(self, sel, timeout=None):
            # Simulate a hydration attempt when a selector is waited for
            try:
                # call patched hydrate_cycle (this will increment the counter)
                page_renderer.hydrate_cycle(self)
            except Exception:
                pass
            # mark hydrated so content() will change
            self._hydrated = True
            return None

        def evaluate(self, *args, **kwargs):
            # Return sensible defaults for various JS snippets used by the renderer
            s = args[0] if args else ''
            try:
                s = str(s)
            except Exception:
                s = ''
            if 'document.body.scrollHeight' in s:
                return 100
            # Returning empty list for selectables
            if 'querySelectorAll' in s and 'notion-selectable' in s:
                return []
            # Generic JS calls return None/0
            return None

        def wait_for_timeout(self, ms):
            # no-op
            return None

        def content(self):
            # Return final content if hydrated, otherwise a placeholder
            if getattr(self, '_hydrated', False):
                return '<div>Conteudo final</div>'
            return '<div>Carregando código</div>'

        def screenshot(self, **kwargs):
            return None

    class FakeContext:
        def __init__(self):
            self._page = FakePage()

        def new_page(self):
            return self._page

        def close(self):
            return None

    class FakeBrowser:
        def new_context(self, **kwargs):
            return FakeContext()

        def close(self):
            return None

    class FakeChromium:
        def launch(self, *args, **kwargs):
            return FakeBrowser()

    class FakeSyncPlaywrightCM:
        def __enter__(self):
            # the object returned as `p` in `with sync_playwright() as p:`
            p = types.SimpleNamespace()
            p.chromium = FakeChromium()
            return p

        def __exit__(self, exc_type, exc, tb):
            return False

    # Patch sync_playwright to return our fake context manager
    monkeypatch.setattr(convert_from_public, 'sync_playwright', lambda: FakeSyncPlaywrightCM())

    # Run the renderer
    html = convert_from_public.render_with_playwright('http://example.com')

    assert 'Conteudo final' in html
    assert called['count'] >= 1
