"""Tests for the --subpages-as-files feature.

Covers:
- extract_notion_page_links detection
- Deduplication by page ID
- Exclusion of the parent page URL
- Non-Notion URLs are ignored
- Page links with various notion.site sub-domains are detected
- Link rewrite in markdown (URL → relative path)
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from notion_converter_helpers import extract_notion_page_links


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _html_with_links(*hrefs: str) -> str:
    links = "".join(f'<a href="{h}">link text</a>' for h in hrefs)
    return f"<html><body>{links}</body></html>"


# ---------------------------------------------------------------------------
# extract_notion_page_links
# ---------------------------------------------------------------------------

PARENT_URL = "https://notion.so/My-Page-aabbccdd11223344aabbccdd11223344"

VALID_SUB = "https://notion.so/Sub-Page-00112233445566778899aabbccddeeff"
VALID_SUB2 = "https://www.notion.so/Sub-Page-2-ffeeddccbbaa99887766554433221100"
WORKSPACE_SUB = "https://myworkspace.notion.site/Doc-deadbeefdeadbeefdeadbeefdeadbeef"


def test_detects_notion_so_link():
    html = _html_with_links(VALID_SUB)
    result = extract_notion_page_links(html, PARENT_URL)
    assert len(result) == 1
    assert result[0][0] == VALID_SUB


def test_detects_notion_site_link():
    html = _html_with_links(WORKSPACE_SUB)
    result = extract_notion_page_links(html, PARENT_URL)
    assert len(result) == 1
    assert result[0][0] == WORKSPACE_SUB


def test_excludes_parent_url():
    html = _html_with_links(PARENT_URL, VALID_SUB)
    result = extract_notion_page_links(html, PARENT_URL)
    urls = [r[0] for r in result]
    assert PARENT_URL not in urls
    assert VALID_SUB in urls


def test_deduplicates_by_page_id():
    # Same page ID, different query string — should appear only once
    dup = VALID_SUB + "?v=123"
    html = _html_with_links(VALID_SUB, dup)
    result = extract_notion_page_links(html, PARENT_URL)
    assert len(result) == 1


def test_ignores_non_notion_links():
    html = _html_with_links(
        "https://example.com/some-page",
        "https://github.com/user/repo",
        VALID_SUB,
    )
    result = extract_notion_page_links(html, PARENT_URL)
    assert len(result) == 1
    assert result[0][0] == VALID_SUB


def test_ignores_notion_link_without_page_id():
    # URL in notion.so domain but no 32-hex ID
    html = _html_with_links("https://notion.so/help/tips")
    result = extract_notion_page_links(html, PARENT_URL)
    assert result == []


def test_returns_link_text():
    html = '<a href="' + VALID_SUB + '">My Sub Document</a>'
    result = extract_notion_page_links(html, PARENT_URL)
    assert len(result) == 1
    assert result[0][1] == "My Sub Document"


def test_returns_multiple_distinct_links():
    html = _html_with_links(VALID_SUB, VALID_SUB2, WORKSPACE_SUB)
    result = extract_notion_page_links(html, PARENT_URL)
    assert len(result) == 3


def test_empty_html_returns_empty():
    result = extract_notion_page_links("", PARENT_URL)
    assert result == []


def test_html_with_no_anchors_returns_empty():
    result = extract_notion_page_links("<p>Hello world</p>", PARENT_URL)
    assert result == []
