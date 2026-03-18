from typing import Any, Optional
import time

import renderers
import settings


# Hydration helpers for Playwright rendering.
# These are lightweight, defensive wrappers that attempt to perform
# scrolls, heuristic clicks and JS injections to "hydrate" lazy-loaded
# Notion blocks. Implementations are intentionally conservative so they
# no-op when the provided `page` object is a mock or lacks Playwright
# methods. Detailed orchestration/retries are handled elsewhere.


def inject_hydration_js(page) -> None:
    """
    Injects a small JS snippet into the page that dispatches a few
    events (resize/scroll) and touches the DOM to try to trigger
    observers and lazy-loaders.

    This function is defensive: if `page` doesn't support `evaluate`,
    it simply returns without raising.
    """
    try:
        if page is None:
            return
        if hasattr(page, "evaluate"):
            js = "(function(){ window.dispatchEvent(new Event('resize')); window.dispatchEvent(new Event('scroll')); try{ document.body && document.body.offsetHeight; }catch(e){} return true; })();"
            try:
                page.evaluate(js)
            except Exception:
                # Some Playwright wrappers require a callable + args; try a lambda-style call
                try:
                    page.evaluate("() => " + js)
                except Exception:
                    pass
    except Exception:
        # Swallow all errors to keep helper safe for mocks
        return


def aggressive_scroll(page: Any, steps: Optional[int] = None, wait_ms: Optional[int] = None) -> None:
    if steps is None:
        steps = settings.HYDRATION_SCROLL_STEPS
    if wait_ms is None:
        wait_ms = int(settings.HYDRATION_TIMEOUT_MS / max(1, steps)) if getattr(settings, 'HYDRATION_TIMEOUT_MS', None) else 250

    try:
        if page is None:
            return
        if hasattr(page, "evaluate"):
            _scroll_via_evaluate(page, steps, wait_ms)
        else:
            _scroll_via_mouse(page, steps, wait_ms)
    except Exception:
        return


def _scroll_via_evaluate(page: Any, steps: int, wait_ms: int) -> None:
    for i in range(max(1, steps)):
        try:
            fraction = (i + 1) / float(max(1, steps))
            js = f"window.scrollTo(0, document.body.scrollHeight * {fraction});"
            page.evaluate(js)
        except Exception:
            pass
        time.sleep(max(0, wait_ms) / 1000.0)


def _scroll_via_mouse(page: Any, steps: int, wait_ms: int) -> None:
    for _ in range(max(1, steps)):
        try:
            if hasattr(page, 'mouse') and hasattr(page.mouse, 'wheel'):
                page.mouse.wheel(0, 1000)
        except Exception:
            pass
        time.sleep(max(0, wait_ms) / 1000.0)


def detect_and_click_toggles(page) -> int:
    clicks = 0
    try:
        if page is None:
            return 0
        if hasattr(page, 'query_selector_all'):
            clicks = _click_via_query_selector(page)
        elif hasattr(page, 'locator'):
            clicks = _click_via_locator(page)
    except Exception:
        return clicks
    return clicks


def _get_element_text(el: Any) -> Optional[str]:
    if not hasattr(el, 'text_content'):
        return None
    try:
        return el.text_content()
    except TypeError:
        try:
            return el.text_content
        except Exception:
            return None


def _click_via_query_selector(page: Any) -> int:
    clicks = 0
    try:
        candidates = page.query_selector_all("button, a, summary, [role='button']")
    except Exception:
        return 0
    for el in candidates or []:
        try:
            text = _get_element_text(el)
            if text and _is_placeholder_matching(text) and hasattr(el, 'click'):
                try:
                    el.click()
                    clicks += 1
                except Exception:
                    pass
                break
        except Exception:
            continue
    return clicks


def _is_placeholder_matching(text: str) -> bool:
    for p in settings.PLACEHOLDER_PATTERNS:
        if p and p.lower() in text.lower():
            return True
    return False


def _click_via_locator(page: Any) -> int:
    clicks = 0
    try:
        summaries = page.locator('summary')
        try:
            count = summaries.count()
        except Exception:
            count = None
        if count:
            for i in range(count):
                try:
                    summaries.nth(i).click()
                    clicks += 1
                except Exception:
                    pass
    except Exception:
        pass
    return clicks


def hydrate_cycle(page: Any, max_rounds: Optional[int] = None, scroll_steps: Optional[int] = None, wait_ms: Optional[int] = None, click_toggles: Optional[bool] = None) -> None:
    """
    Execute a hydration cycle performing repeated scrolls, optional heuristic
    clicks on toggles and small JS injections/waits to coax lazy content to load.

    Parameters default to settings.HYDRATION_* values when not provided.
    This function does not return HTML; it only attempts to force the page to
    load dynamic content.
    """
    if max_rounds is None:
        max_rounds = getattr(settings, 'HYDRATION_MAX_RETRIES', 2)
    if scroll_steps is None:
        scroll_steps = getattr(settings, 'HYDRATION_SCROLL_STEPS', 5)
    if wait_ms is None:
        wait_ms = int(getattr(settings, 'HYDRATION_TIMEOUT_MS', 15000) / max(1, max_rounds or 1)) // 4
    if click_toggles is None:
        click_toggles = getattr(settings, 'HYDRATION_CLICK_TOGGLES', True)

    try:
        if page is None:
            return
        for _ in range(max(0, int(max_rounds or 0))):
            _execute_hydration_round(page, scroll_steps, wait_ms, click_toggles)
            time.sleep(max(0, _get_retry_delay(wait_ms)) / 1000.0)
    except Exception:
        return


def _get_retry_delay(default_delay: int) -> int:
    retry_delay_ms = getattr(settings, 'HYDRATION_RETRY_DELAY_MS', None)
    if retry_delay_ms is not None:
        try:
            return int(retry_delay_ms)
        except Exception:
            return default_delay
    return default_delay


def _execute_hydration_round(page: Any, scroll_steps: int, wait_ms: int, click_toggles: bool) -> None:
    aggressive_scroll(page, steps=scroll_steps, wait_ms=wait_ms)
    inject_hydration_js(page)
    if click_toggles:
        try:
            detect_and_click_toggles(page)
        except Exception:
            pass


class PageRenderer:
    def __init__(
        self,
        use_requests: bool = False,
        headful: bool = False,
        ua: Optional[str] = None,
        expand_toggles: bool = False,
        max_scroll_steps: int = 220,
        scroll_wait_ms: int = 250,
    ):
        self.use_requests = use_requests
        self.headful = headful
        self.ua = ua
        self.expand_toggles = expand_toggles
        self.max_scroll_steps = max_scroll_steps
        self.scroll_wait_ms = scroll_wait_ms

    def render(self, url: str, screenshot_path: Optional[str] = None, extract_selectables: bool = True) -> str:
        if not self.use_requests:
            return renderers.render_with_playwright(
                url,
                user_agent=self.ua,
                headful=self.headful,
                wait_until="domcontentloaded",
                timeout=60000,
                screenshot_path=screenshot_path,
                expand_toggles=self.expand_toggles,
                extract_selectables=extract_selectables,
                max_scroll_steps=self.max_scroll_steps,
                scroll_wait_ms=self.scroll_wait_ms,
            )
        return renderers.fetch_html_requests(url)
