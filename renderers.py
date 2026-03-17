import hashlib
from pathlib import Path

import requests
from typing import Literal, Optional

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
    """Renderiza a página com Playwright e retorna o HTML."""
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
        browser = p.chromium.launch(
            headless=not headful,
            args=["--disable-features=IsolateOrigins,site-per-process", "--disable-blink-features=AutomationControlled"],
        )
        # Consolidate context creation to avoid duplicate calls and reduce cognitive overhead.
        context_args = {"viewport": {"width": 1280, "height": 900}}
        if user_agent:
            context_args["user_agent"] = user_agent
        context = browser.new_context(**context_args)
        page = context.new_page()
        page.set_extra_http_headers({
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
        })

        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
        except Exception as e:
            print("Playwright.goto error:", e)

        found = False
        for sel in wait_selectors:
            try:
                print(f"Esperando seletor: {sel} (timeout 20s)")
                page.wait_for_selector(sel, timeout=20000)
                found = True
                print("Seletor encontrado:", sel)
                break
            except Exception:
                continue

        try:
            page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:
            pass

        chunks: "dict[str, str]" = {}

        def add_chunks_from_page():
            try:
                items = page.evaluate(
                    "() => {\n    const root = document.querySelector('div.notion-page-content') || document.body;\n    const els = Array.from(root.querySelectorAll('div.notion-selectable'));\n    return els.map((el) => {\n        const id = el.getAttribute('data-block-id') || el.id || null;\n        return { id, html: el.outerHTML };\n    });\n}"
                )
                for it in items:
                    html = it.get("html") or ""
                    if not html:
                        continue
                    block_id = it.get("id")
                    if block_id:
                        key = f"id:{block_id}"
                    else:
                        key = "h:" + hashlib.sha1(html.encode("utf-8", errors="ignore")).hexdigest()
                    if key not in chunks:
                        chunks[key] = html
            except Exception:
                return

        if expand_toggles:
            try:
                from notion_utils import click_expandables

                clicked = click_expandables(page)
                if clicked:
                    page.wait_for_timeout(200)
            except Exception:
                pass

        last_height = 0
        stable_rounds = 0
        for _ in range(max_scroll_steps):
            if expand_toggles:
                try:
                    from notion_utils import click_expandables

                    click_expandables(page)
                except Exception:
                    pass

            add_chunks_from_page()

            try:
                page.evaluate("() => window.scrollBy(0, Math.floor(window.innerHeight * 0.85))")
            except Exception:
                break
            page.wait_for_timeout(scroll_wait_ms)

            try:
                h = page.evaluate("() => document.body.scrollHeight")
            except Exception:
                h = None
            if h is not None:
                if h == last_height:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                    last_height = h
                if stable_rounds >= 8:
                    break

        try:
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(400)
        except Exception:
            pass

        if expand_toggles:
            try:
                from notion_utils import click_expandables

                click_expandables(page)
                page.wait_for_timeout(200)
            except Exception:
                pass

        if extract_selectables and chunks:
            content = "<div class=\"notion-export\">\n" + "\n".join(chunks.values()) + "\n</div>"
        else:
            content = page.content()

        if screenshot_path:
            try:
                out_base = Path(screenshot_path)
                out_base.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out_base.with_suffix('.png')), full_page=True)
                out_base.with_suffix('.html').write_text(content, encoding='utf-8')
            except Exception as e:
                print('Falha ao salvar screenshot/html de diagnóstico:', e)

        # Optionally normalize spanned Notion code blocks
        try:
            from notion_utils import normalize_notion_code_blocks
            import settings as _settings
            if getattr(_settings, 'NORMALIZE_NOTION_CODE_BLOCKS', True):
                try:
                    content = normalize_notion_code_blocks(content)
                except Exception:
                    pass
        except Exception:
            pass

        context.close()
        browser.close()
        if not found:
            print('Aviso: nenhum seletor conhecido foi encontrado — a página pode estar bloqueando conteúdo para bots.')
        return content


def fetch_html_requests(url: str, timeout: int = 10) -> str:
    headers = {"User-Agent": "notion-md-converter/1.0"}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text
