#!/usr/bin/env python3
"""Converter Notion público (Share to web) → Markdown

Funcionalidade:
- Renderiza a página pública com Playwright (recomendado) para capturar conteúdo dinâmico
- Fallback: busca HTML com requests
- Converte HTML para Markdown usando markdownify (ou html2text como fallback)
- Gera nome de saída automaticamente com base no título da página + timestamp, se --output não for fornecido
"""

from datetime import datetime
from urllib.parse import urlparse
from urllib.parse import urljoin
from urllib.parse import quote
from pathlib import Path
import argparse
import os
import re
import base64
import mimetypes
import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Literal, Optional, Tuple, Union

from dotenv import load_dotenv
load_dotenv()

from notion_converter_helpers import extract_notion_page_links

_MSG_FALLBACK_REQUESTS = 'Fazendo fallback para fetch estático (requests).'

# Diretório base para exportação (opcional) - usado para criar uma pasta por título dentro dele.
# Exemplo em `.env`: NOTION_EXPORT_DIR=./out
EXPORT_BASE_DIR = os.getenv('NOTION_EXPORT_DIR')

# Dependências opcionais
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    sync_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_AVAILABLE = False

try:
    from markdownify import markdownify as mdify
    MDIFY_AVAILABLE = True
except Exception:
    mdify = None  # type: ignore[assignment]
    MDIFY_AVAILABLE = False
    try:
        import html2text
        HTML2TEXT_AVAILABLE = True
    except Exception:
        html2text = None  # type: ignore[assignment]
        HTML2TEXT_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except Exception:
    BeautifulSoup = None  # type: ignore[assignment]
    BS4_AVAILABLE = False

import requests
import subprocess
import sys
import settings

REQUIRE_PLAYWRIGHT = False

# detectar placeholders e helpers de hidratação
from notion_utils import detect_placeholders_in_html, normalize_notion_code_blocks, normalize_notion_blocks_to_html
from page_renderer import hydrate_cycle


def sanitize_filename(s: str) -> str:
    if not s:
        return s
    s = s.strip()
    s = re.sub(r'[\\/*?:"<>|]', '', s)
    s = re.sub(r"\s+", ' ', s)
    return s


def extract_page_id(page_url: str) -> str:
    if not page_url:
        return ''
    path = urlparse(page_url).path
    parts = path.rstrip('/').split('-')
    if parts:
        return parts[-1].replace('-', '')
    return ''


def _click_expandables(page) -> int:
        return page.evaluate(
                """() => {
    const root = document.querySelector('div.notion-page-content') || document.body;
    const clickables = new Set();

    // Generic ARIA expanders
    root.querySelectorAll("[aria-expanded='false']").forEach((el) => clickables.add(el));

    // HTML details/summary
    root.querySelectorAll('details:not([open]) > summary').forEach((el) => clickables.add(el));

    // Notion toggle blocks (heuristic)
    root.querySelectorAll('div.notion-toggle, div.notion-toggle-block, div.notion-toggle__content').forEach((wrap) => {
        const btn = wrap.querySelector("[role='button']") || wrap.querySelector('button');
        if (btn) clickables.add(btn);
    });

    // "click to open" blocks often require clicking the row itself
    Array.from(root.querySelectorAll('div, button, a, span')).forEach((el) => {
        const t = (el.innerText || '').toLowerCase();
        if (!t) return;
        if (t.includes('click to open') || t.includes('(click to open)')) {
            clickables.add(el);
            // also try to click a nearby expander
            let p = el;
            for (let i = 0; i < 8 && p; i++) {
                if (p.getAttribute && p.getAttribute('aria-expanded') === 'false') {
                    clickables.add(p);
                    break;
                }
                const b = p.querySelector && (p.querySelector("[aria-expanded='false']") || p.querySelector("[role='button']"));
                if (b) {
                    clickables.add(b);
                    break;
                }
                p = p.parentElement;
            }
        }
    });

    // Click everything once
    let c = 0;
    const arr = Array.from(clickables);
    for (const el of arr) {
        try {
            el.scrollIntoView({ block: 'center', inline: 'nearest' });
        } catch (e) {}
        try {
            el.click();
            c++;
        } catch (e) {
            // fallback: dispatch mouse events
            try {
                el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                c++;
            } catch (e2) {}
        }
    }
    return c;
}"""
        )


def _extract_selectables(page) -> list[dict]:
    return page.evaluate(
        """() => {
    const root = document.querySelector('div.notion-page-content') || document.body;
    const els = Array.from(root.querySelectorAll('div.notion-selectable'));
    return els.map((el) => {
        const id = el.getAttribute('data-block-id') || el.id || null;
        return { id, html: el.outerHTML };
    });
}"""
    )


def _hydrate_dynamic_content(page, max_rounds: int = 14, per_round_limit: int = 60, wait_ms: int = 250) -> None:
    """Força a hidratação de blocos lazy/virtualizados do Notion.

    Em páginas públicas do Notion, alguns blocos aparecem como placeholders:
    - `div.notion-unknown-block`
    - textos shimmer `div.nds-shimmer-text`
    Esses elementos só são substituídos quando entram em viewport. Aqui, nós os
    scrollamos explicitamente para disparar o carregamento.
    """
    prev_unknown = None
    prev_shimmer = None
    stable = 0

    for _ in range(max_rounds):
        try:
            stats = page.evaluate(
                """(limit) => {
    const root = document.querySelector('div.notion-page-content') || document.body;
    const unknown = Array.from(root.querySelectorAll('div.notion-unknown-block'));
    const shimmer = Array.from(root.querySelectorAll('div.nds-shimmer-text'));

    const targets = [];
    for (const el of shimmer) targets.push(el);
    for (const el of unknown) targets.push(el);

    let scrolled = 0;
    for (const el of targets.slice(0, limit)) {
        try {
            el.scrollIntoView({ block: 'center', inline: 'nearest' });
            scrolled++;
        } catch (e) {}
    }
    return { unknown: unknown.length, shimmer: shimmer.length, scrolled };
}""",
                per_round_limit,
            )
        except Exception:
            break

        unknown = stats.get('unknown')
        shimmer = stats.get('shimmer')

        if prev_unknown == unknown and prev_shimmer == shimmer:
            stable += 1
        else:
            stable = 0
            prev_unknown = unknown
            prev_shimmer = shimmer

        # se ficou estável por algumas rodadas, parar (mesmo que não seja zero)
        if stable >= 3:
            break

        try:
            page.wait_for_timeout(wait_ms)
        except Exception:
            break


def _toggle_click_to_open_cycle(page) -> int:
    """Em alguns casos, os toggles com '(click to open)' ficam 'abertos' mas não hidratam.

    Faz um ciclo fechar→abrir para forçar o carregamento do conteúdo.
    """
    try:
        return page.evaluate(
            """() => {
    const root = document.querySelector('div.notion-page-content') || document.body;
    const toggles = Array.from(root.querySelectorAll('div.notion-toggle-block'));
    let c = 0;

    function safeClick(el) {
        try { el.click(); return true; } catch (e) {}
        try {
            el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
            el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
            el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            return true;
        } catch (e2) {}
        return false;
    }

    for (const t of toggles) {
        const txt = (t.innerText || '').toLowerCase();
        if (!txt.includes('click to open') && !txt.includes('(click to open)')) continue;

        const btn = t.querySelector("[role='button'][aria-controls]") || t.querySelector("[role='button']") || t.querySelector('button');
        if (!btn) continue;

        try { btn.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
        const expanded = btn.getAttribute('aria-expanded');
        if (expanded === 'true') {
            if (safeClick(btn)) c++; // fecha
        }
        if (safeClick(btn)) c++; // abre
    }
    return c;
}"""
        )
    except Exception:
        return 0

def _hydrate_text_placeholders(page, phrases: list[str], max_rounds: int = 10, wait_ms: int = 450) -> None:
    """Tenta forçar o carregamento de conteúdo lazy quando há placeholders de texto.

    Ex.: blocos que exibem "Carregando código ..." até a renderização finalizar.
    Estratégia: rolar até as ocorrências e aguardar um pouco, repetindo.
    """
    for _ in range(max_rounds):
        total = 0
        for phrase in phrases:
            loc = page.locator(f"text={phrase}")
            try:
                count = loc.count()
            except Exception:
                continue
            if not count:
                continue
            total += count

            # não iterar demais para evitar lentidão
            for i in range(min(count, 20)):
                try:
                    loc.nth(i).scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass

        if total == 0:
            return

        try:
            page.wait_for_timeout(wait_ms)
        except Exception:
            return


def render_with_playwright(
    url: str,
    wait_until: Literal['commit', 'domcontentloaded', 'load', 'networkidle'] = 'domcontentloaded',
    timeout: int = 60000,
    user_agent: str | None = None,
    headful: bool = False,
    wait_selectors: list | None = None,
    screenshot_path: str | None = None,
    expand_toggles: bool = False,
    extract_selectables: bool = True,
    max_scroll_steps: int = 220,
    scroll_wait_ms: int = 250,
) -> str:
    """Renderiza com Playwright e retorna HTML.

        Estratégia:
        - Aguarda container de conteúdo
        - Rola incrementalmente até o fim para forçar lazy-load
        - Opcionalmente expande toggles durante a rolagem
        - Opcionalmente retorna apenas `notion-page-content > notion-selectable` (mais fiel e sem lixo de navegação)
    """
    if not PLAYWRIGHT_AVAILABLE:
        # Se o usuário requisitou Playwright estrito, falhar imediatamente com instrução de instalação
        if REQUIRE_PLAYWRIGHT:
            print('\nParece que o Playwright não está instalado no ambiente.')
            print('Por favor instale os navegadores executando: python -m playwright install chromium')
            print(r'Ou execute o utilitário local: scripts\install_playwright.py')
            sys.exit(2)
        raise RuntimeError('playwright não disponível')
    assert sync_playwright is not None
    if wait_selectors is None:
        wait_selectors = [
            'div.notion-page-content',
            'div.notion-page',
            'div.notion-text-block',
            'div.notion-selectable',
            'main',
            'article',
        ]

    with sync_playwright() as p:
        def _is_browser_missing_err(msg: str) -> bool:
            if not msg:
                return False
            s = str(msg).lower()
            keywords = [
                "executable doesn't exist",
                "executable does not exist",
                "browsertype.launch",
                "could not find",
                "no executable",
                "no browsers are installed",
                "could not find any",
                "is not installed",
                "not installed",
            ]
            return any(k in s for k in keywords)

        def _attempt_auto_install() -> bool:
            print('Tentativa de instalação automática dos navegadores Playwright iniciada...')
            try:
                script_path = Path(__file__).parent / 'scripts' / 'install_playwright.py'
                if script_path.exists():
                    print(f'Executando utilitário local: {script_path}')
                    proc = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
                else:
                    browsers = settings.PLAYWRIGHT_BROWSERS
                    if isinstance(browsers, list):
                        browsers_list = browsers
                    else:
                        browsers_list = [b.strip() for b in str(browsers).split(',') if b.strip()]
                    cmd = [sys.executable, '-m', 'playwright', 'install'] + browsers_list
                    print('Executando comando:', ' '.join(cmd))
                    proc = subprocess.run(cmd, capture_output=True, text=True)
                print('Instalação - stdout:\n', proc.stdout)
                print('Instalação - stderr:\n', proc.stderr)
                return proc.returncode == 0
            except Exception as ie:
                print('Erro ao executar instalador automático:', ie)
                return False

        try:
            browser = p.chromium.launch(headless=not headful, args=['--disable-features=IsolateOrigins,site-per-process', '--disable-blink-features=AutomationControlled'])
        except Exception as e:
            msg = str(e)
            if _is_browser_missing_err(msg):
                print('Erro ao iniciar Playwright:', msg)
                print('\nParece que os navegadores Playwright não estão instalados.')
                print('Por favor instale executando: python -m playwright install chromium')
                print(r'Ou use o utilitário local: scripts\install_playwright.py')

                # Se usuário requisitou Playwright obrigatório, não tentar auto-install nem fallback
                if REQUIRE_PLAYWRIGHT:
                    print('\nPLAYWRIGHT_REQUIRE está habilitado: abortando em vez de tentar instalação automática ou fallback.')
                    sys.exit(2)

                if settings.PLAYWRIGHT_AUTO_INSTALL:
                    print('PLAYWRIGHT_AUTO_INSTALL está habilitado. Tentando instalação automática...')
                    ok = _attempt_auto_install()
                    if ok:
                        print('Instalação automática concluída. Tentando iniciar o navegador novamente...')
                        try:
                            browser = p.chromium.launch(headless=not headful, args=['--disable-features=IsolateOrigins,site-per-process', '--disable-blink-features=AutomationControlled'])
                        except Exception as e2:
                            print('Falha ao reiniciar Playwright após instalação:', e2)
                            print(_MSG_FALLBACK_REQUESTS)
                            return fetch_html_requests(url)
                    else:
                        print('Instalação automática falhou.')
                        print(_MSG_FALLBACK_REQUESTS)
                        return fetch_html_requests(url)
                else:
                    print('PLAYWRIGHT_AUTO_INSTALL está desabilitado.')
                    print(_MSG_FALLBACK_REQUESTS)
                    return fetch_html_requests(url)
            # não é um erro reconhecido como ausência de navegadores -> propagar
            raise

        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        if user_agent:
            context = browser.new_context(viewport={'width': 1280, 'height': 900}, user_agent=user_agent)
        page = context.new_page()
        # headers
        page.set_extra_http_headers({
            'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7'
        })

        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
        except Exception as e:
            # não falhar imediatamente; tentaremos capturar o que houver
            print('Playwright.goto error:', e)

        # tentar esperar por seletores conhecidos que indicam que o conteúdo foi renderizado
        found = False
        for sel in wait_selectors:
            try:
                print(f'Esperando seletor: {sel} (timeout 20s)')
                page.wait_for_selector(sel, timeout=20000)
                found = True
                print('Seletor encontrado:', sel)
                break
            except Exception:
                continue

        # rolagem incremental para forçar carregamento de todas as seções
        try:
            page.evaluate('() => window.scrollTo(0, 0)')
        except Exception:
            pass

        chunks: "OrderedDict[str, str]" = OrderedDict()

        def add_chunks_from_page():
            try:
                items = _extract_selectables(page) if extract_selectables else []
                for it in items:
                    html = it.get('html') or ''
                    if not html:
                        continue
                    block_id = it.get('id')
                    if block_id:
                        key = f'id:{block_id}'
                    else:
                        key = 'h:' + hashlib.sha1(html.encode('utf-8', errors='ignore')).hexdigest()
                    if key not in chunks:
                        chunks[key] = html
            except Exception:
                return

        if expand_toggles:
            try:
                clicked = _click_expandables(page)
                if clicked:
                    page.wait_for_timeout(200)
            except Exception:
                pass

        last_height = 0
        stable_rounds = 0
        for _ in range(max_scroll_steps):
            if expand_toggles:
                try:
                    _click_expandables(page)
                except Exception:
                    pass

            if extract_selectables:
                add_chunks_from_page()

            try:
                page.evaluate('() => window.scrollBy(0, Math.floor(window.innerHeight * 0.85))')
            except Exception:
                break
            page.wait_for_timeout(scroll_wait_ms)

            try:
                h = page.evaluate('() => document.body.scrollHeight')
            except Exception:
                h = None
            if h is not None:
                if h == last_height:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                    last_height = h
                # se a altura parou de crescer por algumas iterações, provavelmente chegou no fim
                if stable_rounds >= 8:
                    break

        # Garantir captura final no fim da página
        try:
            page.evaluate('() => window.scrollTo(0, document.body.scrollHeight)')
            page.wait_for_timeout(400)
        except Exception:
            pass
        if expand_toggles:
            try:
                _click_expandables(page)
                page.wait_for_timeout(200)
            except Exception:
                pass

        # Forçar hidratação de placeholders (unknown/shimmer), especialmente no fim da página
        if expand_toggles:
            try:
                _toggle_click_to_open_cycle(page)
                page.wait_for_timeout(200)
            except Exception:
                pass
        try:
            _hydrate_dynamic_content(page, max_rounds=14, per_round_limit=80, wait_ms=max(200, int(scroll_wait_ms)))
        except Exception:
            pass

        # Alguns blocos (principalmente código) podem ficar como "Carregando ...".
        # Forçar rolagem até esses placeholders para acionar IntersectionObservers.
        try:
            _hydrate_text_placeholders(
                page,
                phrases=[
                    'Carregando código',
                    'Loading code',
                ],
                max_rounds=10,
                wait_ms=max(300, int(scroll_wait_ms)),
            )
        except Exception:
            pass

        if extract_selectables:
            add_chunks_from_page()

        if extract_selectables and chunks:
            # Recriar um HTML mínimo só com o conteúdo selecionável
            content = '<div class="notion-export">\n' + '\n'.join(chunks.values()) + '\n</div>'
        else:
            content = page.content()

        # Detectar placeholders no HTML e tentar hidratar via hydrate_cycle em loop de retries
        try:
            placeholders = detect_placeholders_in_html(content)
        except Exception:
            placeholders = []
        if placeholders:
            max_retries = getattr(settings, 'HYDRATION_MAX_RETRIES', 3)
            print(f'Detectados placeholders no HTML. Iniciando até {max_retries} tentativas de hidratação...')
            for attempt in range(max_retries):
                print(f'  Tentativa de hidratação {attempt+1}/{max_retries}')
                try:
                    hydrate_cycle(page)
                except Exception as e:
                    print('Erro durante hydrate_cycle():', e)
                try:
                    page.wait_for_timeout(250)
                except Exception:
                    pass
                try:
                    content = page.content()
                except Exception as e:
                    print('Erro ao obter conteúdo da página após hidratação:', e)
                    break
                try:
                    placeholders = detect_placeholders_in_html(content)
                except Exception:
                    placeholders = []
                if not placeholders:
                    print('Placeholders removidos com sucesso.')
                    break
            if placeholders:
                if REQUIRE_PLAYWRIGHT:
                    print('\nNão foi possível hidratar todos os placeholders. Playwright é obrigatório neste modo.')
                    print('Por favor instale os navegadores: python -m playwright install chromium')
                    sys.exit(2)
                else:
                    print('\nAviso: Ainda há placeholders após tentativas de hidratação. Continuando com o conteúdo atual.')

        # dumps opcionais para debug
        if screenshot_path:
            try:
                out_base = Path(screenshot_path)
                out_base.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out_base.with_suffix('.png')), full_page=True)
                out_base.with_suffix('.html').write_text(content, encoding='utf-8')
            except Exception as e:
                print('Falha ao salvar screenshot/html de diagnóstico:', e)

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


def _attr_str(val: Any) -> str:
    """Normaliza atributos do BeautifulSoup em string.

    BeautifulSoup pode retornar `AttributeValueList` (list) para atributos multi-valuados,
    o que quebra chamadas simples como `.strip()`.
    """
    if val is None:
        return ''
    if isinstance(val, list):
        if not val:
            return ''
        return str(val[0])
    return str(val)


def extract_title_from_html(html: str) -> str | None:
    # tenta <title>, depois primeiro heading h1/h2/h3, depois meta og:title
    if BS4_AVAILABLE:
        assert BeautifulSoup is not None
        soup = BeautifulSoup(html, settings.HTML_PARSER)
        title_tag = soup.find('title')
        if title_tag and title_tag.text.strip():
            return title_tag.text.strip()
        for h in ['h1', 'h2', 'h3']:
            htag = soup.find(h)
            if htag and htag.text.strip():
                return htag.text.strip()
        og = soup.find('meta', property='og:title')
        if og:
            content = _attr_str(og.get('content')).strip()
            if content:
                return content
        # fallback: primeira linha de texto
        text = soup.get_text(separator='\n')
        for line in text.splitlines():
            s = line.strip()
            if s:
                return s
        return None
    else:
        m = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if m:
            return re.sub(r'\s+', ' ', m.group(1)).strip()
        m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL)
        if m:
            return re.sub(r'<[^>]+>', '', m.group(1)).strip()
        return None


def ensure_dir(path: Union[str, Path]):
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class ConverterConfig:
    page_url: str
    output: Optional[str] = None
    use_requests: bool = False
    screenshot: Optional[str] = None
    headful: bool = False
    ua: Optional[str] = None
    expand_toggles: bool = False
    max_scroll_steps: int = 220
    scroll_wait_ms: int = 250
    extract_selectables: bool = False
    no_extract_selectables: bool = False
    download_assets: bool = False
    assets_dir: Optional[str] = None
    follow_subpages: bool = False
    subpages_as_files: bool = False


def guess_filename_from_url(url: str) -> str:
    # tenta extrair nome de arquivo do path ou usar um fallback
    p = url.split('?')[0].rstrip('/')
    name = p.split('/')[-1]
    if not name:
        name = 'resource'
    # tentar extensão via mimetype
    if '.' not in name:
        ext = mimetypes.guess_extension(mimetypes.guess_type(url)[0] or '')
        if ext:
            name = name + ext
    # sanitize
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    return name


def download_resource(url: str, assets_dir: Path, session: requests.Session | None = None) -> str | None:
    # retorna o caminho relativo salvo dentro de assets_dir
    try:
        if url.startswith('data:'):
            # data URI
            header, data = url.split(',', 1)
            m = re.search(r'data:(.*?);base64', header)
            ext = ''
            if m:
                mime = m.group(1)
                ext = mimetypes.guess_extension(mime) or ''
            data_bytes = base64.b64decode(data)
            name = 'embedded' + (ext or '.bin')
            fname = assets_dir / name
            fname.write_bytes(data_bytes)
            return str(fname)
        s = session or requests
        r = s.get(url, stream=True, timeout=20, headers={'User-Agent':'notion-md-converter/1.0'})
        r.raise_for_status()
        name = guess_filename_from_url(url)
        fname = assets_dir / name
        # evitar sobrescrever - adicionar sufixo se existir
        i = 1
        orig = fname
        while fname.exists():
            fname = assets_dir / f"{orig.stem}-{i}{orig.suffix}"
            i += 1
        with open(fname, 'wb') as fh:
            for chunk in r.iter_content(8192):
                if chunk:
                    fh.write(chunk)
        return str(fname)
    except Exception as e:
        print('Falha ao baixar recurso:', url, e)
        return None


def process_html_assets(html: str, base_url: str, assets_dir: str) -> tuple[str, list]:
    """Baixa imagens e links de arquivo e reescreve HTML para apontar para arquivos locais.
    Retorna (html_modificado, lista_de_arquivos_baixados)
    """
    if not BS4_AVAILABLE:
        return html, []
    assert BeautifulSoup is not None
    assets_path = ensure_dir(assets_dir)
    session = requests.Session()
    soup = BeautifulSoup(html, settings.HTML_PARSER)
    downloaded = []

    # Emojis/ícones do Notion (spritesheet/data URI, data-gif placeholder e `notion-emojis...`)
    # quebram em muitos viewers. Convertemos para texto usando o `alt`.
    for img in soup.find_all('img'):
        classes = img.get('class') or []
        if isinstance(classes, str):
            classes = [classes]
        src = _attr_str(img.get('src')).strip()
        alt = _attr_str(img.get('alt')).strip()

        is_data_gif_placeholder = src.startswith('data:image/gif')
        is_notion_emoji_host = 'notion-emojis' in src
        is_notion_emoji_class = 'notion-emoji' in classes

        if is_notion_emoji_class or is_notion_emoji_host or is_data_gif_placeholder:
            first_token = alt.split(' ')[0].strip() if alt else ''
            if first_token and any(ord(ch) > 127 for ch in first_token):
                img.replace_with(first_token)
            else:
                img.decompose()

    # imagens <img>
    for img in soup.find_all('img'):
        src = _attr_str(img.get('src') or img.get('data-src') or img.get('data-original-src'))
        if not src:
            # verificar srcset
            srcset = _attr_str(img.get('srcset'))
            if srcset:
                # pegar o primeiro URL do srcset
                src = srcset.split(',')[0].strip().split(' ')[0]
        if not src:
            continue
        src = urljoin(base_url, src)
        saved = download_resource(src, assets_path, session)
        if saved:
            # fazer link relativo
            rel = os.path.relpath(saved, start=assets_path.parent)
            rel_posix = rel.replace('\\','/')
            img['src'] = quote(rel_posix, safe='/')
            downloaded.append(saved)

    # background images em inline style
    for el in soup.find_all(style=re.compile(r'background(-image)?:')):
        style = _attr_str(el.get('style'))
        m = re.search(r"url([\"']?(.*?)[\"']?)", style)
        if m:
            src = m.group(2)
            src = urljoin(base_url, src)
            saved = download_resource(src, assets_path, session)
            if saved:
                rel = os.path.relpath(saved, start=assets_path.parent)
                # substituir url(...) por caminho relativo
                rel_posix = rel.replace('\\','/')
                rel_url = quote(rel_posix, safe='/')
                new_style = re.sub(r"url([\"']?(.*?)[\"']?)", f"url('{rel_url}')", style)
                el['style'] = new_style
                downloaded.append(saved)

    # links para arquivos (possíveis attachments)
    _notion_re = re.compile(
        r'^https?://(?:(?:www\.)?notion\.so|[\w-]+\.notion\.site)/', re.IGNORECASE
    )
    for a in soup.find_all('a'):
        href = _attr_str(a.get('href'))
        if not href:
            continue
        if href.startswith('#') or href.startswith('mailto:') or href.startswith('javascript:'):
            continue
        # para links externos ou arquivos, baixar
        full = urljoin(base_url, href)
        if _notion_re.match(full):
            continue  # Notion page links handled by --subpages-as-files
        saved = download_resource(full, assets_path, session)
        if saved:
            rel = os.path.relpath(saved, start=assets_path.parent)
            rel_posix = rel.replace('\\','/')
            a['href'] = quote(rel_posix, safe='/')
            downloaded.append(saved)

    return str(soup), downloaded


_CONTENT_SELECTORS = [
    'div.notion-page-content',
    'div.notion-collection-view-body',
    "[class*='notion-collection-view']",
    'main',
    'article',
]


def normalize_html_for_markdown(html: str) -> str:
    if not BS4_AVAILABLE:
        return html
    assert BeautifulSoup is not None
    soup = BeautifulSoup(html, settings.HTML_PARSER)

    # Always strip non-content tags to prevent JS/CSS leaking into Markdown
    for tag in soup.find_all(['script', 'style', 'noscript', 'link', 'meta']):
        tag.decompose()

    # Try multiple selectors — Notion database views don't use notion-page-content
    root = None
    for selector in _CONTENT_SELECTORS:
        root = soup.select_one(selector)
        if root is not None:
            break

    if root is not None:
        soup = BeautifulSoup(str(root), settings.HTML_PARSER)

    # Convert Notion block divs to semantic HTML before markdownify
    normalised = normalize_notion_blocks_to_html(str(soup))
    soup = BeautifulSoup(normalised, settings.HTML_PARSER)

    for img in soup.find_all('img'):
        classes = img.get('class') or []
        if isinstance(classes, str):
            classes = [classes]
        src = _attr_str(img.get('src')).strip()
        is_data_gif_placeholder = src.startswith('data:image/gif')
        is_notion_emoji_host = 'notion-emojis' in src
        if 'notion-emoji' in classes or is_notion_emoji_host or is_data_gif_placeholder:
            alt = _attr_str(img.get('alt')).strip()
            first_token = alt.split(' ')[0].strip() if alt else ''
            if first_token and any(ord(ch) > 127 for ch in first_token):
                img.replace_with(first_token)
            else:
                img.decompose()

    # Optionally normalize spanned Notion code blocks into <pre><code>
    if getattr(settings, 'NORMALIZE_NOTION_CODE_BLOCKS', True):
        try:
            return normalize_notion_code_blocks(str(soup))
        except Exception:
            return str(soup)
    else:
        return str(soup)



def html_to_markdown(html: str) -> str:
    if MDIFY_AVAILABLE:
        assert mdify is not None
        return mdify(html, heading_style='ATX')
    if 'HTML2TEXT_AVAILABLE' in globals() and globals().get('HTML2TEXT_AVAILABLE'):
        assert html2text is not None
        return html2text.html2text(html)
    # fallback simples: remove tags na medida do possível
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    return text


class PageRenderer:
    """Abstração de renderização de página Notion.

    Permite alternar entre Playwright (renderização JS completa) e uma chamada HTTP simples.
    """

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
        if not self.use_requests and PLAYWRIGHT_AVAILABLE:
            return render_with_playwright(
                url,
                user_agent=self.ua,
                headful=self.headful,
                wait_until='domcontentloaded',
                timeout=60000,
                screenshot_path=screenshot_path,
                expand_toggles=self.expand_toggles,
                extract_selectables=extract_selectables,
                max_scroll_steps=self.max_scroll_steps,
                scroll_wait_ms=self.scroll_wait_ms,
            )
        return fetch_html_requests(url)


class NotionMarkdownConverter:
    def __init__(self, config: ConverterConfig):
        self.config = config
        self.renderer = PageRenderer(
            use_requests=config.use_requests,
            headful=config.headful,
            ua=config.ua,
            expand_toggles=config.expand_toggles,
            max_scroll_steps=config.max_scroll_steps,
            scroll_wait_ms=config.scroll_wait_ms,
        )

    def _resolve_output_paths(self, title: str) -> Tuple[Path, Optional[Path], Optional[Path]]:
        """Retorna (out_path, output_folder, assets_dir)."""
        # base de saída (pasta por título)
        output_folder: Optional[Path] = None
        if EXPORT_BASE_DIR:
            folder_name = sanitize_filename(title)[:160] if title else (extract_page_id(self.config.page_url) or 'notion_page')
            output_folder = ensure_dir(Path(EXPORT_BASE_DIR) / folder_name)

        # nome do arquivo de saída
        if self.config.output:
            out_name = self.config.output
        else:
            ts = datetime.now().strftime('%Y%m%d-%H%M%S')
            safe = sanitize_filename(title)[:160] if title else ''
            if safe:
                out_name = f"{safe} - {ts}.md"
            else:
                pid = extract_page_id(self.config.page_url) or 'notion_page'
                out_name = f"{pid} - {ts}.md"

        # montar caminho final
        if output_folder and not Path(out_name).is_absolute():
            out_path = output_folder / Path(out_name).name
        else:
            out_path = Path(out_name)

        # assets
        assets_dir: Optional[Path] = None
        if self.config.download_assets:
            if output_folder:
                if self.config.assets_dir:
                    # If assets_dir looks like a URL (has a non-file scheme), do not treat it as a local path.
                    scheme = urlparse(self.config.assets_dir).scheme
                    if scheme in ("", "file"):
                        assets_dir = Path(self.config.assets_dir)
                        if not assets_dir.is_absolute():
                            assets_dir = output_folder / assets_dir
                    else:
                        # URL-like assets_dir (e.g., s3:// or https://) — avoid creating local Path
                        assets_dir = None
                else:
                    assets_dir = output_folder / f"{out_path.stem}_assets"
            else:
                # No output_folder: only use config.assets_dir if it is a local path
                if self.config.assets_dir and urlparse(self.config.assets_dir).scheme in ("", "file"):
                    assets_dir = Path(self.config.assets_dir)
                else:
                    assets_dir = Path(f"{out_path.stem}_assets")

        return out_path, output_folder, assets_dir

    def _render_html(self) -> str:
        try:
            return self.renderer.render(
                self.config.page_url,
                screenshot_path=self.config.screenshot,
                extract_selectables=(not self.config.no_extract_selectables),
            )
        except Exception as e:
            print('Erro ao buscar/renderizar HTML:', e)
            raise

    def _download_assets(self, html: str, base_url: str, assets_dir: Path) -> tuple[str, list]:
        ensure_dir(assets_dir)
        print('Baixando assets para:', assets_dir)
        html, downloaded = process_html_assets(html, base_url, str(assets_dir))
        print('Arquivos baixados:', len(downloaded))
        return html, downloaded

    def _render_sub(self, url: str) -> str:
        if PLAYWRIGHT_AVAILABLE and not self.config.use_requests:
            return render_with_playwright(
                url,
                user_agent=self.config.ua,
                headful=self.config.headful,
                wait_until='domcontentloaded',
                timeout=60000,
                screenshot_path=None,
                expand_toggles=self.config.expand_toggles,
                extract_selectables=(not self.config.no_extract_selectables),
                max_scroll_steps=self.config.max_scroll_steps,
                scroll_wait_ms=self.config.scroll_wait_ms,
            )
        return fetch_html_requests(url)

    def _download_linked_pages_as_files(
        self,
        html: str,
        md: str,
        out_path: Path,
        output_folder: Optional[Path],
    ) -> str:
        """Converte cada página Notion linkada em um arquivo .md separado em subdiretório.

        Para cada link para outra página Notion encontrado em *html*:
        - Renderiza / faz download da página sub-documento
        - Extrai o título da página
        - Cria ``<output_dir>/<titulo_sanitizado>/<titulo_sanitizado>.md``
        - Opcionalmente baixa os assets para ``<titulo_sanitizado>_assets/``
        - Substitui a URL Notion no markdown parente pelo caminho relativo local

        Apenas 1 nível (sem recursão). Falhas por link são logadas e seguem adiante.
        """
        sublinks = extract_notion_page_links(html, self.config.page_url, base_url=self.config.page_url)
        print(f'Sub-documentos Notion encontrados: {len(sublinks)}')
        if not sublinks:
            return md

        base_dir = output_folder if output_folder else out_path.parent

        for sub_url, _link_text in sublinks:
            print(f'  → Convertendo sub-documento: {sub_url}')
            try:
                sub_html = self._render_sub(sub_url)
            except Exception as e:
                print(f'    Falha ao renderizar sub-documento {sub_url}: {e}')
                continue

            sub_title = extract_title_from_html(sub_html) or ''
            if not sub_title:
                sub_title = extract_page_id(sub_url) or 'sub_document'
            safe_title = sanitize_filename(sub_title)[:160] or 'sub_document'

            sub_folder = ensure_dir(base_dir / safe_title)
            sub_md_path = sub_folder / f'{safe_title}.md'

            if self.config.download_assets:
                sub_assets_dir = sub_folder / f'{safe_title}_assets'
                ensure_dir(sub_assets_dir)
                sub_html, downloaded = process_html_assets(sub_html, sub_url, str(sub_assets_dir))
                print(f'    Assets do sub-documento baixados: {len(downloaded)}')

            sub_html = normalize_html_for_markdown(sub_html)
            sub_md = html_to_markdown(sub_html)

            sub_md_path.parent.mkdir(parents=True, exist_ok=True)
            with open(sub_md_path, 'w', encoding='utf-8') as fh:
                fh.write(sub_md)
            print(f'    Salvo: {sub_md_path}')

            rel = sub_md_path.relative_to(out_path.parent)
            rel_posix = str(rel).replace('\\', '/')
            quoted_rel = quote(rel_posix, safe='/')

            md = md.replace(f']({sub_url})', f']({quoted_rel})')

        return md

    def _append_subpages(self, html: str, md: str, assets_dir: Optional[Path]) -> str:
        print('Procurando subpáginas internas para anexar...')
        sublinks: List[str] = []
        base_id = extract_page_id(self.config.page_url)
        if base_id:
            import re
            matches = re.findall(r'href="(https?://[^"]*%s(?:-[0-9]+)?)"' % re.escape(base_id), html)
            for m in matches:
                if m not in sublinks and m != self.config.page_url:
                    sublinks.append(m)
        print('Subpages found:', len(sublinks))

        for idx, link in enumerate(sublinks, start=1):
            try:
                if PLAYWRIGHT_AVAILABLE and not self.config.use_requests:
                    sub_html = render_with_playwright(
                        link,
                        user_agent=self.config.ua,
                        headful=self.config.headful,
                        wait_until='domcontentloaded',
                        timeout=60000,
                        screenshot_path=(self.config.screenshot + f'-sub{idx}' if self.config.screenshot else None),
                        expand_toggles=self.config.expand_toggles,
                        extract_selectables=(not self.config.no_extract_selectables),
                        max_scroll_steps=self.config.max_scroll_steps,
                        scroll_wait_ms=self.config.scroll_wait_ms,
                    )
                else:
                    sub_html = fetch_html_requests(link)

                if self.config.download_assets and assets_dir is not None:
                    sub_assets_dir = assets_dir / f"subpage_{idx}"
                    ensure_dir(sub_assets_dir)
                    sub_html, downloaded = process_html_assets(sub_html, link, str(sub_assets_dir))
                    print('Subpage assets downloaded:', len(downloaded))

                sub_html = normalize_html_for_markdown(sub_html)
                sub_md = html_to_markdown(sub_html)
                md += '\n\n---\n\n' + f"## Subpage: {link}\n\n" + sub_md
            except Exception as e:
                print('Falha ao buscar subpage:', link, e)
        return md

    def run(self) -> None:
        raw_html = self._render_html()
        title = extract_title_from_html(raw_html) or ''
        html = raw_html

        out_path, output_folder, assets_dir = self._resolve_output_paths(title)

        if output_folder and self.config.screenshot:
            # Only treat screenshot as a local filesystem path when it has no URL scheme or is a 'file' URL.
            scheme = urlparse(self.config.screenshot).scheme
            if scheme in ('', 'file') and not Path(self.config.screenshot).is_absolute():
                self.config.screenshot = str(output_folder / self.config.screenshot)
            # else: keep screenshot as URL-like string

        if self.config.download_assets and assets_dir:
            html, _ = self._download_assets(html, self.config.page_url, assets_dir)

        html = normalize_html_for_markdown(html)
        md = html_to_markdown(html)

        if self.config.follow_subpages:
            md = self._append_subpages(html, md, assets_dir)

        if self.config.subpages_as_files:
            md = self._download_linked_pages_as_files(raw_html, md, out_path, output_folder)

        print('Título detectado:', title)
        print('Escrevendo arquivo:', out_path)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(md)

        if assets_dir:
            print('Assets salvos em:', assets_dir)

        print('Concluído')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--page-url', help='URL pública da página Notion (Share to web)', default=os.getenv('NOTION_PAGE_URL'))
    parser.add_argument('--output', help='Arquivo de saída (.md). Se omitido, será gerado automaticamente', default=None)
    parser.add_argument('--use-requests', action='store_true', help='Forçar uso de requests (não usa Playwright)')
    parser.add_argument('--require-playwright', action='store_true', help='Exigir Playwright: falhar se Playwright ou navegadores não estiverem instalados (sem fallback)')
    parser.add_argument('--screenshot', help='Salvar screenshot+HTML para diagnóstico (caminho base, ex: out/snap)')
    parser.add_argument('--headful', action='store_true', help='Executar Playwright em headful (útil para depuração)')
    parser.add_argument('--ua', help='User-Agent a usar (opcional)')
    parser.add_argument('--expand-toggles', action='store_true', help='Expandir blocos/toggles colapsados antes de capturar o HTML (recomendado)')
    parser.add_argument('--max-scroll-steps', type=int, default=220, help='Máximo de passos de rolagem para forçar carregar todo o conteúdo')
    parser.add_argument('--scroll-wait-ms', type=int, default=250, help='Tempo de espera (ms) entre passos de rolagem')
    parser.add_argument('--extract-selectables', action='store_true', help='(deprecated) Mantido por compatibilidade; a extração já é padrão')
    parser.add_argument('--no-extract-selectables', action='store_true', help='Desativar extração de notion-selectable e usar page.content() completo')
    parser.add_argument('--download-assets', action='store_true', help='Baixar imagens e arquivos referenciados pela página e ajustar links locais')
    parser.add_argument('--assets-dir', help='Diretório para salvar os assets (sobrescreve o padrão gerado)')
    parser.add_argument('--follow-subpages', action='store_true', help='Seguir links de subpáginas internas e anexar o conteúdo ao final do Markdown')
    parser.add_argument('--subpages-as-files', action='store_true', help='Baixar cada página Notion linkada como um arquivo .md separado em subpasta e reescrever os links no documento pai')
    args = parser.parse_args()

    if args.require_playwright:
        global REQUIRE_PLAYWRIGHT
        REQUIRE_PLAYWRIGHT = True
        if not PLAYWRIGHT_AVAILABLE:
            print('\nParece que o Playwright não está instalado no ambiente.')
            print('Por favor instale os navegadores executando: python -m playwright install chromium')
            print(r'Ou execute o utilitário local: scripts\install_playwright.py')
            # reset flag before exiting to avoid leaking state to other callers/tests
            REQUIRE_PLAYWRIGHT = False
            sys.exit(2)

    if not args.page_url:
        parser.error('Informe --page-url ou configure NOTION_PAGE_URL no ambiente')

    config = ConverterConfig(
        page_url=args.page_url,
        output=args.output,
        use_requests=args.use_requests,
        screenshot=args.screenshot,
        headful=args.headful,
        ua=args.ua,
        expand_toggles=args.expand_toggles,
        max_scroll_steps=args.max_scroll_steps,
        scroll_wait_ms=args.scroll_wait_ms,
        extract_selectables=args.extract_selectables,
        no_extract_selectables=args.no_extract_selectables,
        download_assets=args.download_assets,
        assets_dir=args.assets_dir,
        follow_subpages=args.follow_subpages,
        subpages_as_files=args.subpages_as_files,
    )

    converter = NotionMarkdownConverter(config)
    converter.run()


if __name__ == '__main__':
    main()
