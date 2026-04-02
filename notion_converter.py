"""Lógica principal de conversão de Notion → Markdown."""

import base64
import hashlib
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, List, Optional, Tuple, cast, TYPE_CHECKING, Dict, Iterable
from datetime import datetime
from urllib.parse import quote, urljoin, urlparse

if TYPE_CHECKING:
    import bs4  # type: ignore

try:
    from bs4 import BeautifulSoup  # type: ignore
    BS4_AVAILABLE = True
except Exception:
    BeautifulSoup = None  # type: ignore
    BS4_AVAILABLE = False

from converter_config import ConverterConfig
from page_renderer import PageRenderer
from renderers import fetch_html_requests
import settings
from notion_utils import hydrate_dynamic_content, toggle_click_to_open_cycle, normalize_notion_blocks_to_html
from notion_converter_helpers import remove_notion_emojis, get_image_src, resolve_full_url, rel_url_from_saved, filter_sublinks, extract_notion_page_links


def sanitize_filename(s: str) -> str:
    if not s:
        return s
    s = s.strip()
    s = re.sub(r"[\\/*?:\"<>|]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def extract_page_id(page_url: str) -> str:
    if not page_url:
        return ""
    path = urlparse(page_url).path
    parts = path.rstrip("/").split("-")
    if parts:
        return parts[-1].replace("-", "")
    return ""


def _attr_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, list):
        return str(val[0]) if val else ""
    return str(val)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def guess_filename_from_url(url: str) -> str:
    p = url.split("?")[0].rstrip("/")
    name = p.split("/")[-1]
    if not name:
        name = "resource"
    if "." not in name:
        ext = mimetypes.guess_extension(mimetypes.guess_type(url)[0] or "")
        if ext:
            name = name + ext
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    return name


def download_resource(url: str, assets_dir: Path, session=None) -> Optional[str]:
    try:
        if url.startswith("data:"):
            header, data = url.split(",", 1)
            m = re.search(r"data:(.*?);base64", header)
            ext = ""
            if m:
                mime = m.group(1)
                ext = mimetypes.guess_extension(mime) or ""
            data_bytes = base64.b64decode(data)
            name = "embedded" + (ext or ".bin")
            fname = assets_dir / name
            fname.write_bytes(data_bytes)
            return str(fname)

        s = session or __import__("requests")
        r = s.get(url, stream=True, timeout=20, headers={"User-Agent": "notion-md-converter/1.0"})
        r.raise_for_status()

        name = guess_filename_from_url(url)
        fname = assets_dir / name
        i = 1
        orig = fname
        while fname.exists():
            fname = assets_dir / f"{orig.stem}-{i}{orig.suffix}"
            i += 1
        with open(fname, "wb") as fh:
            for chunk in r.iter_content(8192):
                if chunk:
                    fh.write(chunk)
        return str(fname)
    except Exception as e:
        print("Falha ao baixar recurso:", url, e)
        return None


def process_html_assets(html: str, base_url: str, assets_dir: str) -> Tuple[str, List[str]]:
    if not BS4_AVAILABLE:
        return html, []
    assert BeautifulSoup is not None
    try:
        soup = BeautifulSoup(html, settings.HTML_PARSER)
    except Exception:
        return html, []

    assets_path = ensure_dir(Path(assets_dir))
    session = __import__("requests").Session()
    downloaded = []

    remove_notion_emojis(soup)
    downloaded.extend(_process_images(soup, base_url, assets_path, session))
    downloaded.extend(_process_backgrounds(soup, base_url, assets_path, session))
    downloaded.extend(_process_links(soup, base_url, assets_path, session))

    return str(soup), downloaded


def _process_images(soup: Any, base_url: str, assets_path: Path, session: Any) -> List[str]:
    downloaded = []
    for img in soup.find_all("img"):
        src = get_image_src(img)
        if not src:
            continue
        src = resolve_full_url(src, base_url)
        saved = download_resource(src, assets_path, session)
        if saved:
            rel_url = rel_url_from_saved(saved, assets_path)
            img["src"] = rel_url
            downloaded.append(saved)
    return downloaded


def _process_backgrounds(soup: Any, base_url: str, assets_path: Path, session: Any) -> List[str]:
    downloaded = []
    for el in soup.find_all(style=re.compile(r"background(-image)?:")):
        style = _attr_str(el.get("style"))
        m = re.search(r"url(['\"])?(.*?)\1?\)", style)
        if m:
            src = m.group(2)
            src = resolve_full_url(src, base_url)
            saved = download_resource(src, assets_path, session)
            if saved:
                rel_url = rel_url_from_saved(saved, assets_path)
                new_style = re.sub(r"url(['\"])?(.*?)\1?\)", f"url('{rel_url}')", style)
                el["style"] = new_style
                downloaded.append(saved)
    return downloaded


def _process_links(soup: Any, base_url: str, assets_path: Path, session: Any) -> List[str]:
    downloaded = []
    _notion_re = re.compile(
        r'^https?://(?:(?:www\.)?notion\.so|[\w-]+\.notion\.site)/', re.IGNORECASE
    )
    for a in soup.find_all("a"):
        href = _attr_str(a.get("href"))
        if not href:
            continue
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        full = resolve_full_url(href, base_url)
        if _notion_re.match(full):
            continue  # Notion page links handled by --subpages-as-files
        saved = download_resource(full, assets_path, session)
        if saved:
            rel_url = rel_url_from_saved(saved, assets_path)
            a["href"] = rel_url
            downloaded.append(saved)
    return downloaded


_CONTENT_SELECTORS = [
    "div.notion-page-content",
    "div.notion-collection-view-body",
    "[class*='notion-collection-view']",
    "main",
    "article",
]


def normalize_html_for_markdown(html: str) -> str:
    if not BS4_AVAILABLE:
        return html
    assert BeautifulSoup is not None
    try:
        soup = BeautifulSoup(html, settings.HTML_PARSER)
    except Exception:
        return html

    # Always strip non-content tags to prevent JS/CSS leaking into Markdown
    for tag in soup.find_all(["script", "style", "noscript", "link", "meta"]):
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

    _process_notion_images(soup)
    return str(soup)


def _is_notion_emoji(img: Any) -> bool:
    classes = img.get("class") or []
    if isinstance(classes, str):
        classes = [classes]
    src = _attr_str(img.get("src")).strip()
    is_data_gif_placeholder = src.startswith("data:image/gif")
    is_notion_emoji_host = "notion-emojis" in src
    return "notion-emoji" in classes or is_notion_emoji_host or is_data_gif_placeholder


def _process_notion_images(soup: Any) -> None:
    for img in soup.find_all("img"):
        if _is_notion_emoji(img):
            alt = _attr_str(img.get("alt")).strip()
            first_token = alt.split(" ")[0].strip() if alt else ""
            if first_token and any(ord(ch) > 127 for ch in first_token):
                img.replace_with(first_token)
            else:
                img.decompose()


def html_to_markdown(html: str) -> str:
    try:
        from markdownify import markdownify as mdify

        return mdify(html, heading_style="ATX")
    except Exception:
        try:
            import html2text

            return html2text.html2text(html)
        except Exception:
            pass

    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def extract_title_from_html(html: str) -> Optional[str]:
    # If BS4 isn't available, we cannot parse a title — return None to match prior fallback behavior.
    if not BS4_AVAILABLE:
        return None
    assert BeautifulSoup is not None
    try:
        soup = BeautifulSoup(html, settings.HTML_PARSER)
    except Exception:
        return None

    title_tag = soup.find("title")
    if title_tag and title_tag.text.strip():
        return title_tag.text.strip()

    for h in ["h1", "h2", "h3"]:
        htag = soup.find(h)
        if htag and htag.text.strip():
            return htag.text.strip()

    og = soup.find("meta", property="og:title")
    if og:
        content = _attr_str(og.get("content")).strip()
        if content:
            return content

    text = soup.get_text(separator="\n")
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s

    return None


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
        output_folder = self._get_output_folder(title)
        out_name = self._get_output_name(title)
        out_path = self._resolve_out_path(out_name, output_folder)
        assets_dir = self._resolve_assets_dir(out_path, output_folder)
        return out_path, output_folder, assets_dir

    def _get_output_folder(self, title: str) -> Optional[Path]:
        if not settings.EXPORT_BASE_DIR:
            return None
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = sanitize_filename(title)[:160] if title else (extract_page_id(self.config.page_url) or "notion_page")
        folder_name = f"{base} - {ts}"
        return ensure_dir(Path(settings.EXPORT_BASE_DIR) / folder_name)

    def _get_output_name(self, title: str) -> str:
        if self.config.output:
            return self.config.output
        safe = sanitize_filename(title)[:160] if title else ""
        if safe:
            return f"{safe}.md"
        pid = extract_page_id(self.config.page_url) or "notion_page"
        return f"{pid}.md"

    def _resolve_out_path(self, out_name: str, output_folder: Optional[Path]) -> Path:
        if output_folder and not Path(out_name).is_absolute():
            return output_folder / Path(out_name).name
        return Path(out_name)

    def _resolve_assets_dir(self, out_path: Path, output_folder: Optional[Path]) -> Optional[Path]:
        if self.config.download_assets:
            return self._get_assets_dir_with_download(out_path, output_folder)
        return self._get_assets_dir_fallback(out_path, output_folder)

    def _get_assets_dir_with_download(self, out_path: Path, output_folder: Optional[Path]) -> Optional[Path]:
        if not output_folder:
            if self.config.assets_dir and urlparse(self.config.assets_dir).scheme in ("", "file"):
                return Path(self.config.assets_dir)
            return Path(f"{out_path.stem}_assets")
        if not self.config.assets_dir:
            return output_folder / f"{out_path.stem}_assets"
        scheme = urlparse(self.config.assets_dir).scheme
        if scheme in ("", "file"):
            assets_dir = Path(self.config.assets_dir)
            return output_folder / assets_dir if not assets_dir.is_absolute() else assets_dir
        return None

    def _get_assets_dir_fallback(self, out_path: Path, output_folder: Optional[Path]) -> Optional[Path]:
        has_assets_dir = getattr(self.config, 'assets_dir', None)
        if has_assets_dir and urlparse(has_assets_dir).scheme not in ("", "file"):
            return None
        if output_folder is not None:
            return output_folder / f"{out_path.stem}_assets"
        return Path(f"{out_path.stem}_assets")

    def _download_assets(self, html: str, base_url: str, assets_dir: Path) -> Tuple[str, List[str]]:
        ensure_dir(assets_dir)
        print("Baixando assets para:", assets_dir)
        html, downloaded = process_html_assets(html, base_url, str(assets_dir))
        print("Arquivos baixados:", len(downloaded))
        return html, downloaded

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
        print(f"Sub-documentos Notion encontrados: {len(sublinks)}")
        if not sublinks:
            return md

        # pasta de saída: mesmo diretório do arquivo pai
        base_dir = output_folder if output_folder else out_path.parent

        for sub_url, _link_text in sublinks:
            print(f"  → Convertendo sub-documento: {sub_url}")
            try:
                sub_html = self.renderer.render(
                    sub_url,
                    screenshot_path=None,
                    extract_selectables=(not self.config.no_extract_selectables),
                )
            except Exception as e:
                print(f"    Falha ao renderizar sub-documento {sub_url}: {e}")
                continue

            sub_title = extract_title_from_html(sub_html) or ""
            if not sub_title:
                sub_title = extract_page_id(sub_url) or "sub_document"
            safe_title = sanitize_filename(sub_title)[:160] or "sub_document"

            sub_folder = ensure_dir(base_dir / safe_title)
            sub_md_path = sub_folder / f"{safe_title}.md"

            sub_assets_dir: Optional[Path] = None
            if self.config.download_assets:
                sub_assets_dir = sub_folder / f"{safe_title}_assets"
                ensure_dir(sub_assets_dir)
                sub_html, downloaded = process_html_assets(sub_html, sub_url, str(sub_assets_dir))
                print(f"    Assets do sub-documento baixados: {len(downloaded)}")

            sub_html = normalize_html_for_markdown(sub_html)
            sub_md = html_to_markdown(sub_html)

            sub_md_path.parent.mkdir(parents=True, exist_ok=True)
            with open(sub_md_path, "w", encoding="utf-8") as fh:
                fh.write(sub_md)
            print(f"    Salvo: {sub_md_path}")

            # Caminho relativo do md parente até o sub-arquivo (posix, com quote)
            rel = sub_md_path.relative_to(out_path.parent)
            rel_posix = str(rel).replace("\\", "/")
            quoted_rel = quote(rel_posix, safe="/")

            # Substituir a URL Notion pelo caminho local no markdown parente
            md = md.replace(f"]({sub_url})", f"]({quoted_rel})")

        return md

    def _append_subpages(self, html: str, md: str, assets_dir: Optional[Path]) -> str:
        print("Procurando subpáginas internas para anexar...")
        sublinks: List[str] = []
        base_id = extract_page_id(self.config.page_url)
        if base_id:
            import re

            matches = re.findall(r'href="(https?://[^\"]*%s(?:-[0-9]+)?)"' % re.escape(base_id), html)

            sublinks = filter_sublinks(matches, self.config.page_url)
        print("Subpages found:", len(sublinks))

        for idx, link in enumerate(sublinks, start=1):
            try:
                sub_html = self.renderer.render(
                    link,
                    screenshot_path=(self.config.screenshot + f"-sub{idx}" if self.config.screenshot else None),        
                    extract_selectables=(not self.config.no_extract_selectables),
                )

                if self.config.download_assets and assets_dir is not None:
                    sub_assets_dir = assets_dir / f"subpage_{idx}"
                    ensure_dir(sub_assets_dir)
                    sub_html, downloaded = process_html_assets(sub_html, link, str(sub_assets_dir))
                    print("Subpage assets downloaded:", len(downloaded))

                sub_html = normalize_html_for_markdown(sub_html)
                sub_md = html_to_markdown(sub_html)
                md += "\n\n---\n\n" + f"## Subpage: {link}\n\n" + sub_md
            except Exception as e:
                print('Falha ao buscar subpage:', link, e)
        return md

    def run(self) -> None:
        raw_html = self.renderer.render(
            self.config.page_url,
            screenshot_path=self.config.screenshot,
            extract_selectables=(not self.config.no_extract_selectables),
        )
        title = extract_title_from_html(raw_html) or ''
        html = raw_html

        out_path, output_folder, assets_dir = self._resolve_output_paths(title)

        if output_folder and self.config.screenshot:
            # Only treat screenshot as a local filesystem path when it has no URL scheme or is a 'file' URL.
            # This avoids accidentally treating S3/HTTP URLs as local paths.
            scheme = urlparse(self.config.screenshot).scheme
            if scheme in ("", "file") and not Path(self.config.screenshot).is_absolute():
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
