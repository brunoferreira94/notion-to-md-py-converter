from typing import Optional
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


def aggressive_scroll(page, steps: int = None, wait_ms: int = None) -> None:
    """
    Perform multiple scroll passes to force lazy content to load.

    Parameters default to settings.HYDRATION_SCROLL_STEPS and scroll wait.
    """
    if steps is None:
        steps = settings.HYDRATION_SCROLL_STEPS
    if wait_ms is None:
        # Derive a reasonable wait from total timeout if available
        wait_ms = int(settings.HYDRATION_TIMEOUT_MS / max(1, steps)) if getattr(settings, 'HYDRATION_TIMEOUT_MS', None) else 250

    try:
        if page is None:
            return
        # Prefer evaluate if available
        if hasattr(page, "evaluate"):
            for i in range(max(1, int(steps))):
                try:
                    fraction = (i + 1) / float(max(1, steps))
                    js = f"window.scrollTo(0, document.body.scrollHeight * {fraction});"
                    page.evaluate(js)
                except Exception:
                    # Best-effort: ignore individual failures
                    pass
                time.sleep(max(0, wait_ms) / 1000.0)
        else:
            # Try mouse wheel if provided (some mocks may implement it)
            for _ in range(max(1, int(steps))):
                try:
                    if hasattr(page, 'mouse') and hasattr(page.mouse, 'wheel'):
                        page.mouse.wheel(0, 1000)
                except Exception:
                    pass
                time.sleep(max(0, wait_ms) / 1000.0)
    except Exception:
        return


def detect_and_click_toggles(page) -> int:
    """
    Heuristically detects toggle/expand controls and clicks them.

    Returns the number of clicks performed. This is best-effort and
    swallows exceptions to remain safe when used with lightweight mocks.
    """
    clicks = 0
    try:
        if page is None:
            return 0

        # If the page exposes a Playwright-style `query_selector_all`, use it.
        if hasattr(page, 'query_selector_all'):
            try:
                # Look for common interactive elements
                candidates = page.query_selector_all("button, a, summary, [role='button']")
            except Exception:
                candidates = []

            for el in candidates or []:
                try:
                    text = None
                    if hasattr(el, 'text_content'):
                        try:
                            text = el.text_content()
                        except TypeError:
                            # Some lightweight element proxies expose text_content as property
                            try:
                                text = el.text_content
                            except Exception:
                                text = None

                    # If text matches any placeholder pattern, click it
                    if text:
                        for p in settings.PLACEHOLDER_PATTERNS:
                            if p and p.lower() in text.lower():
                                if hasattr(el, 'click'):
                                    try:
                                        el.click()
                                        clicks += 1
                                    except Exception:
                                        # ignore click errors
                                        pass
                                break
                except Exception:
                    # Ignore errors per element
                    continue

        # If the page supports locator API, attempt a heuristic locator search
        elif hasattr(page, 'locator'):
            try:
                # click summary toggles first
                if hasattr(page, 'locator'):
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

    except Exception:
        return clicks

    return clicks


def hydrate_cycle(page, max_rounds: int = None, scroll_steps: int = None, wait_ms: int = None, click_toggles: bool = None) -> None:
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
        # A small per-action wait; prefer a conservative default
        wait_ms = int(getattr(settings, 'HYDRATION_TIMEOUT_MS', 15000) / max(1, max_rounds)) // 4
    if click_toggles is None:
        click_toggles = getattr(settings, 'HYDRATION_CLICK_TOGGLES', True)

    try:
        if page is None:
            return

        for round_idx in range(max(0, int(max_rounds))):
            # Aggressive scroll passes
            aggressive_scroll(page, steps=scroll_steps, wait_ms=wait_ms)

            # Inject JS nudges
            inject_hydration_js(page)

            # Optionally click toggles
            if click_toggles:
                try:
                    detect_and_click_toggles(page)
                except Exception:
                    pass

            # Small pause between rounds. Prefer explicit HYDRATION_RETRY_DELAY_MS if configured.
            retry_delay_ms = getattr(settings, 'HYDRATION_RETRY_DELAY_MS', None)
            if retry_delay_ms is not None:
                try:
                    delay = int(retry_delay_ms)
                except Exception:
                    delay = wait_ms
            else:
                delay = wait_ms
            time.sleep(max(0, delay) / 1000.0)

    except Exception:
        # Make this helper safe to call with imperfect page mocks
        return


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
