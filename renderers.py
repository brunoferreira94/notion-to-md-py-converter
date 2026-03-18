import hashlib
from pathlib import Path

import requests
from typing import Any, Literal, Optional

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except Exception:
    sync_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_AVAILABLE = False


def render_with_playwright(
    url: str,
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "domcontentloaded",
    timeout: int = 60000,
    user_agent: Optional[str] = None,
    headful: bool = False,
    wait_selectors: list | None = None,
    screenshot_path: str | None = None,
    expand_toggles: bool = False,
    extract_selectables: bool = True,
    max_scroll_steps: int = 220,
    scroll_wait_ms: int = 250,
) -> str:
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("playwright não disponível")
    assert sync_playwright is not None
    if wait_selectors is None:
        wait_selectors = [
            "div.notion-page-content",
            "div.notion-page",
            "div.notion-text-block",
            "div.notion-selectable",
            "main",
            "article",
        ]

    with sync_playwright() as p:
        browser = _launch_browser(p, headful)
        context = _create_context(browser, user_agent)
        page = context.new_page()
        _setup_page_headers(page)

        found = _navigate_and_wait(page, url, wait_until, timeout, wait_selectors)
        _scroll_to_top(page)

        chunks: "dict[str, str]" = {}
        _collect_page_chunks(page, chunks)

        if expand_toggles:
            _try_expand_toggles(page)

        _scroll_and_collect(page, chunks, max_scroll_steps, scroll_wait_ms, expand_toggles)
        _final_scroll_and_expand(page, expand_toggles)

        content = _build_content(page, chunks, extract_selectables)
        _save_screenshot_if_needed(page, content, screenshot_path)
        content = _normalize_if_needed(content)

        context.close()
        browser.close()
        if not found:
            print('Aviso: nenhum seletor conhecido foi encontrado — a página pode estar bloqueando conteúdo para bots.')
        return content


def _launch_browser(p: Any, headful: bool) -> Any:
    return p.chromium.launch(
        headless=not headful,
        args=["--disable-features=IsolateOrigins,site-per-process", "--disable-blink-features=AutomationControlled"],
    )


def _create_context(browser: Any, user_agent: Optional[str]) -> Any:
    return browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=user_agent if user_agent else None,
    )


def _setup_page_headers(page: Any) -> None:
    page.set_extra_http_headers({
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
    })


def _navigate_and_wait(page: Any, url: str, wait_until: str, timeout: int, wait_selectors: list) -> bool:
    found = False
    try:
        page.goto(url, wait_until=wait_until, timeout=timeout)
    except Exception as e:
        print("Playwright.goto error:", e)

    for sel in wait_selectors:
        try:
            print(f"Esperando seletor: {sel} (timeout 20s)")
            page.wait_for_selector(sel, timeout=20000)
            found = True
            print("Seletor encontrado:", sel)
            break
        except Exception:
            continue
    return found


def _scroll_to_top(page: Any) -> None:
    try:
        page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass


def _collect_page_chunks(page: Any, chunks: dict) -> None:
    try:
        items = page.evaluate(
            "() => {\n    const root = document.querySelector('div.notion-page-content') || document.body;\n    const els = Array.from(root.querySelectorAll('div.notion-selectable'));\n    return els.map((el) => {\n        const id = el.getAttribute('data-block-id') || el.id || null;\n        return { id, html: el.outerHTML };\n    });\n}"
        )
        for it in items:
            html = it.get("html") or ""
            if not html:
                continue
            block_id = it.get("id")
            key = f"id:{block_id}" if block_id else "h:" + hashlib.sha1(html.encode("utf-8", errors="ignore")).hexdigest()
            if key not in chunks:
                chunks[key] = html
    except Exception:
        pass


def _try_expand_toggles(page: Any) -> bool:
    try:
        from notion_utils import click_expandables
        clicked = click_expandables(page)
        if clicked:
            page.wait_for_timeout(200)
        return bool(clicked)
    except Exception:
        return False


def _scroll_and_collect(page: Any, chunks: dict, max_scroll_steps: int, scroll_wait_ms: int, expand_toggles: bool) -> None:
    last_height = 0
    stable_rounds = 0
    for _ in range(max_scroll_steps):
        if expand_toggles:
            _try_expand_toggles(page)
        _collect_page_chunks(page, chunks)
        try:
            page.evaluate("() => window.scrollBy(0, Math.floor(window.innerHeight * 0.85))")
        except Exception:
            break
        page.wait_for_timeout(scroll_wait_ms)
        h = _get_scroll_height(page)
        if h is not None:
            if h == last_height:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_height = h
            if stable_rounds >= 8:
                break


def _get_scroll_height(page: Any) -> Optional[int]:
    try:
        return page.evaluate("() => document.body.scrollHeight")
    except Exception:
        return None


def _final_scroll_and_expand(page: Any, expand_toggles: bool) -> None:
    try:
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(400)
    except Exception:
        pass
    if expand_toggles:
        _try_expand_toggles(page)
        page.wait_for_timeout(200)


def _build_content(page: Any, chunks: dict, extract_selectables: bool) -> str:
    if extract_selectables and chunks:
        return "<div class=\"notion-export\">\n" + "\n".join(chunks.values()) + "\n</div>"
    return page.content()


def _save_screenshot_if_needed(page: Any, content: str, screenshot_path: Optional[str]) -> None:
    if not screenshot_path:
        return
    try:
        out_base = Path(screenshot_path)
        out_base.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out_base.with_suffix('.png')), full_page=True)
        out_base.with_suffix('.html').write_text(content, encoding='utf-8')
    except Exception as e:
        print('Falha ao salvar screenshot/html de diagnóstico:', e)


def _normalize_if_needed(content: str) -> str:
    try:
        from notion_utils import normalize_notion_code_blocks
        import settings as _settings
        if getattr(_settings, 'NORMALIZE_NOTION_CODE_BLOCKS', True):
            try:
                return normalize_notion_code_blocks(content)
            except Exception:
                pass
    except Exception:
        pass
    return content


def fetch_html_requests(url: str, timeout: int = 10) -> str:
    headers = {"User-Agent": "notion-md-converter/1.0"}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text
