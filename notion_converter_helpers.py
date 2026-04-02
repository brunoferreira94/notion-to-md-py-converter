"""Helper functions extracted from notion_converter.py

Contains small, behavior-preserving utilities used by the main converter.
"""
from typing import List, Tuple, TYPE_CHECKING, cast, Any, Dict
import os
from pathlib import Path
from urllib.parse import urljoin, quote
import re

if TYPE_CHECKING:
    import bs4  # type: ignore
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None  # type: ignore


def remove_notion_emojis(soup: "bs4.BeautifulSoup | None") -> None:
    """Remove or replace Notion emoji img tags in a BeautifulSoup tree in-place."""
    if BeautifulSoup is None:
        return
    # Narrow type for the type-checker/runtime hints
    soup = cast('bs4.BeautifulSoup', soup)
    for img in soup.find_all("img"):
        classes = img.get("class") or []
        if isinstance(classes, str):
            classes = [classes]
        src = (img.get("src") or "")
        src = str(src)
        alt = (img.get("alt") or "")
        alt = str(alt).strip()

        is_data_gif_placeholder = src.startswith("data:image/gif")
        is_notion_emoji_host = "notion-emojis" in src
        is_notion_emoji_class = "notion-emoji" in classes

        if is_notion_emoji_class or is_notion_emoji_host or is_data_gif_placeholder:
            first_token = alt.split(" ")[0].strip() if alt else ""
            if first_token and any(ord(ch) > 127 for ch in first_token):
                img.replace_with(first_token)
            else:
                img.decompose()


def get_image_src(img: "bs4.element.Tag | Any") -> str:
    """Return a best-effort image source from an <img/> tag (may be empty)."""
    # For type-checkers, narrow to a Tag when bs4 is present
    try:
        img = cast('bs4.element.Tag', img)
    except Exception:
        pass
    src = img.get("src") or img.get("data-src") or img.get("data-original-src")
    if not src:
        srcset = img.get("srcset") or ""
        if srcset:
            src = str(srcset).split(",")[0].strip().split(" ")[0]
    return str(src or "")


def resolve_full_url(src: str, base_url: str) -> str:
    """Resolve a possibly relative src against base_url using urljoin."""
    if not src:
        return ""
    return urljoin(base_url, src)


def rel_url_from_saved(saved_path: str, assets_path: Path) -> str:
    """Compute a quoted, posix-style relative URL for a saved asset.

    saved_path: full filesystem path to saved file
    assets_path: Path to the assets directory (the same variable used in the converter)
    """
    rel = os.path.relpath(saved_path, start=assets_path.parent)
    rel_posix = rel.replace("\\", "/")
    return quote(rel_posix, safe="/")


def filter_sublinks(items: List[str], page_url: str) -> List[str]:
    """Filter duplicates and skip the base page URL, preserving order."""
    seen = set()
    out: List[str] = []
    for it in items:
        if it == page_url or it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


_NOTION_HOST_RE = re.compile(
    r'^https?://(?:(?:www\.)?notion\.so|[\w-]+\.notion\.site)/',
    re.IGNORECASE,
)
_PAGE_ID_RE = re.compile(r'[0-9a-f]{32}', re.IGNORECASE)


def _notion_page_id(url: str) -> str:
    """Extract the trailing 32-hex-char page ID from a Notion URL, or return ''."""
    from urllib.parse import urlparse as _urlparse
    path = _urlparse(url).path.rstrip("/")
    parts = path.split("-")
    if parts:
        cand = parts[-1].replace("-", "")
        if re.fullmatch(r'[0-9a-f]{32}', cand, re.IGNORECASE):
            return cand.lower()
    m = _PAGE_ID_RE.search(path)
    return m.group(0).lower() if m else ""


def extract_notion_page_links(
    html: str,
    exclude_url: str,
    base_url: str = "",
) -> List[Tuple[str, str]]:
    """Extract hrefs pointing to other Notion pages found in *html*.

    Returns a list of ``(href, link_text)`` pairs, deduplicated by page ID and
    excluding any link that resolves to the same page as *exclude_url*.

    Accepts full Notion URLs (``https://notion.so/...``,
    ``https://<workspace>.notion.site/...``) as well as relative paths
    (``/Page-Title-32hexid``) which are resolved against *base_url* when
    provided.
    """
    if BeautifulSoup is None:
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []

    exclude_id = _notion_page_id(exclude_url)
    seen: set = set()
    results: List[Tuple[str, str]] = []

    for a in soup.find_all("a", href=True):
        raw = str(a.get("href") or "").strip()
        if not raw:
            continue

        # Resolve relative paths against base_url when available
        if base_url and not raw.startswith("http"):
            from urllib.parse import urljoin as _urljoin
            href = _urljoin(base_url, raw)
        else:
            href = raw

        if not _NOTION_HOST_RE.match(href):
            continue
        link_id = _notion_page_id(href)
        if not link_id:
            continue
        if exclude_id and link_id == exclude_id:
            continue
        if link_id in seen:
            continue
        seen.add(link_id)
        text = a.get_text(strip=True) or href
        results.append((href, text))

    return results
