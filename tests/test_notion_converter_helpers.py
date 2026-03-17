import os
from pathlib import Path
from bs4 import BeautifulSoup
import sys
import os as _os
# Ensure local project root is on sys.path so tests import local modules first.
sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..')))

from notion_converter_helpers import (
    remove_notion_emojis,
    get_image_src,
    resolve_full_url,
    rel_url_from_saved,
    filter_sublinks,
)


def test_remove_notion_emojis_replaces_emoji():
    html = '<div><img class="notion-emoji" src="https://s3.notion-emojis.com/emoji.png" alt="😊 smile"><p>after</p></div>'
    soup = BeautifulSoup(html, "html.parser")
    remove_notion_emojis(soup)
    text = soup.get_text(separator=" ").strip()
    assert "😊" in text and "after" in text


def test_remove_notion_emojis_decomposes_non_unicode():
    html = '<div><img class="notion-emoji" src="https://s3.notion-emojis.com/empty.png" alt="emoji"><p>after</p></div>'
    soup = BeautifulSoup(html, "html.parser")
    remove_notion_emojis(soup)
    text = soup.get_text(separator=" ").strip()
    assert "emoji" not in text and "after" in text


def test_get_image_src_from_srcset():
    html = '<img srcset="/images/a.png 1x, /images/b.png 2x">'
    soup = BeautifulSoup(html, "html.parser")
    img = soup.find("img")
    src = get_image_src(img)
    assert src == "/images/a.png"


def test_resolve_full_url_relative():
    src = "/path/image.png"
    base = "https://example.com/base/page"
    full = resolve_full_url(src, base)
    assert full == "https://example.com/path/image.png"


def test_rel_url_from_saved_posix():
    # Simulate saved inside an assets dir whose parent is output folder
    assets_path = Path(os.path.join("/tmp", "out", "page_assets"))
    saved = os.path.join("/tmp", "out", "page_assets", "img.png")
    rel = rel_url_from_saved(saved, assets_path)
    assert rel == "page_assets/img.png"


def test_filter_sublinks_filters_duplicates_and_base():
    items = ["https://x/page/1", "https://x/page/1", "https://x/page/2", "https://base/page"]
    base = "https://base/page"
    out = filter_sublinks(items, base)
    assert out == ["https://x/page/1", "https://x/page/2"]
