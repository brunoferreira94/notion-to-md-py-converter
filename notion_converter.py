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
from notion_utils import hydrate_dynamic_content, toggle_click_to_open_cycle
from notion_converter_helpers import remove_notion_emojis, get_image_src, resolve_full_url, rel_url_from_saved, filter_sublinks


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
    # If BS4 isn't available, skip HTML asset processing — preserve original behavior of returning no downloads.
    if not BS4_AVAILABLE:
        return html, []
    try:
        soup = cast('bs4.BeautifulSoup', BeautifulSoup(html, settings.HTML_PARSER))
    except Exception:
        return html, []

    assets_path = ensure_dir(Path(assets_dir))
    session = __import__("requests").Session()
    downloaded = []

    # Remove notion emoji images (extracted to helper to keep logic consistent)
    remove_notion_emojis(soup)

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

    for el in soup.find_all(style=re.compile(r"background(-image)?:")):
        style = _attr_str(el.get("style"))
        # Use a character class ['\"] instead of the alternation ("|') to satisfy SonarLint
        # while keeping the same semantics: match an optional single or double quote.
        m = re.search(r"url(['\"])?(.*?)\1?\)", style)
        if m:
            src = m.group(2)
            src = resolve_full_url(src, base_url)
            saved = download_resource(src, assets_path, session)
            if saved:
                rel_url = rel_url_from_saved(saved, assets_path)
                # Same change in substitution: use character class for the optional quote
                new_style = re.sub(r"url(['\"])?(.*?)\1?\)", f"url('{rel_url}')", style)
                el["style"] = new_style
                downloaded.append(saved)

    for a in soup.find_all("a"):
        href = _attr_str(a.get("href"))
        if not href:
            continue
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        full = resolve_full_url(href, base_url)
        saved = download_resource(full, assets_path, session)
        if saved:
            rel_url = rel_url_from_saved(saved, assets_path)
            a["href"] = rel_url
            downloaded.append(saved)

    return str(soup), downloaded


def normalize_html_for_markdown(html: str) -> str:
    # If BS4 isn't available, return original HTML unchanged.
    if not BS4_AVAILABLE:
        return html
    try:
        soup = cast('bs4.BeautifulSoup', BeautifulSoup(html, settings.HTML_PARSER))
    except Exception:
        return html

    root = soup.select_one("div.notion-page-content")
    if root is not None:
        soup = cast('bs4.BeautifulSoup', BeautifulSoup(str(root), settings.HTML_PARSER))

    # BeautifulSoup.find_all returns a list-like ResultSet; calling list() was redundant.
    for img in soup.find_all("img"): 
        classes = img.get("class") or []
        if isinstance(classes, str):
            classes = [classes]
        src = _attr_str(img.get("src")).strip()
        is_data_gif_placeholder = src.startswith("data:image/gif")
        is_notion_emoji_host = "notion-emojis" in src
        if "notion-emoji" in classes or is_notion_emoji_host or is_data_gif_placeholder:
            alt = _attr_str(img.get("alt")).strip()
            first_token = alt.split(" ")[0].strip() if alt else ""
            if first_token and any(ord(ch) > 127 for ch in first_token):
                img.replace_with(first_token)
            else:
                img.decompose()
    return str(soup)


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
    try:
        soup = cast('bs4.BeautifulSoup', BeautifulSoup(html, settings.HTML_PARSER))
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
        output_folder: Optional[Path] = None
        if settings.EXPORT_BASE_DIR:
            folder_name = sanitize_filename(title)[:160] if title else (extract_page_id(self.config.page_url) or "notion_page")
            output_folder = ensure_dir(Path(settings.EXPORT_BASE_DIR) / folder_name)

        if self.config.output:
            out_name = self.config.output
        else:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            safe = sanitize_filename(title)[:160] if title else ""
            if safe:
                out_name = f"{safe} - {ts}.md"
            else:
                pid = extract_page_id(self.config.page_url) or "notion_page"
                out_name = f"{pid} - {ts}.md"

        if output_folder and not Path(out_name).is_absolute():
            out_path = output_folder / Path(out_name).name
        else:
            out_path = Path(out_name)

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

        # Ensure assets_dir is calculated even when download_assets is False.
        # Do not create the directory or change download behavior — only compute the Path.
        if assets_dir is None:
            # If user explicitly gave a URL-like assets_dir, keep it None to avoid creating local folders.
            if getattr(self.config, 'assets_dir', None) and urlparse(getattr(self.config, 'assets_dir')).scheme not in ("", "file"):
                pass
            else:
                if output_folder is not None:
                    assets_dir = output_folder / f"{out_path.stem}_assets"
                else:
                    assets_dir = Path(f"{out_path.stem}_assets")

        return out_path, output_folder, assets_dir

    def _download_assets(self, html: str, base_url: str, assets_dir: Path) -> Tuple[str, List[str]]:
        ensure_dir(assets_dir)
        print("Baixando assets para:", assets_dir)
        html, downloaded = process_html_assets(html, base_url, str(assets_dir))
        print("Arquivos baixados:", len(downloaded))
        return html, downloaded

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
        html = self.renderer.render(
            self.config.page_url,
            screenshot_path=self.config.screenshot,
            extract_selectables=(not self.config.no_extract_selectables),
        )
        title = extract_title_from_html(html) or ''

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

        print('Título detectado:', title)
        print('Escrevendo arquivo:', out_path)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(md)

        if assets_dir:
            print('Assets salvos em:', assets_dir)

        print('Concluído')
