"""Utilitários específicos para a renderização de páginas Notion."""

from typing import Optional, Any, List, Tuple, Dict
import re

# Import settings lazily to allow tests to reload settings module
import settings

# Module-level cache for compiled regex patterns
_compiled_placeholder_regex: Optional[List[re.Pattern]] = None
_compiled_regex_source: Optional[Tuple] = None


def _ensure_compiled_patterns():
    """Compile regex patterns from settings and cache them. This is resilient to settings reloads by
    checking the source tuple."""
    global _compiled_placeholder_regex, _compiled_regex_source
    source = tuple(settings.PLACEHOLDER_REGEX_PATTERNS)
    if _compiled_placeholder_regex is None or _compiled_regex_source != source:
        _compiled_placeholder_regex = [re.compile(p, flags=re.I) for p in settings.PLACEHOLDER_REGEX_PATTERNS]
        _compiled_regex_source = source
    return _compiled_placeholder_regex


def click_expandables(page) -> int:
    """Clica em toggles / expanders para revelar conteúdo oculto."""
    return page.evaluate(
        """() => {
    const root = document.querySelector('div.notion-page-content') || document.body;
    const clickables = new Set();    // Generic ARIA expanders
    root.querySelectorAll("[aria-expanded='false']").forEach((el) => clickables.add(el));    // HTML details/summary
    root.querySelectorAll('details:not([open]) > summary').forEach((el) => clickables.add(el));    // Notion toggle blocks (heuristic)
    root.querySelectorAll('div.notion-toggle, div.notion-toggle-block, div.notion-toggle__content').forEach((wrap) => {
        const btn = wrap.querySelector("[role='button']") || wrap.querySelector('button');
        if (btn) clickables.add(btn);
    });    // "click to open" blocks often require clicking the row itself
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
    });    // Click everything once
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


def extract_selectables(page) -> List[Dict[str, Any]]:
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


def hydrate_dynamic_content(page, max_rounds: int = 14, per_round_limit: int = 60, wait_ms: int = 250) -> None:
    """Tenta forçar o carregamento de blocos lazy/virtualizados do Notion."""
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

        if stable >= 3:
            break

        try:
            page.wait_for_timeout(wait_ms)
        except Exception:
            break


def toggle_click_to_open_cycle(page) -> int:
    """Força ciclo fechar→abrir nos toggles \"(click to open)\"."""
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


# ---------------------------------------------------------------------------
# Placeholder detection helpers
# ---------------------------------------------------------------------------

def _find_regex_placeholder_matches(text: str) -> List[str]:
    """Return all regex-pattern matches found in *text*."""
    patterns = _ensure_compiled_patterns()
    matches: List[str] = []
    for pat in patterns:
        for m in pat.finditer(text):
            matches.append(m.group(0))
    return matches


def _find_literal_placeholder_matches(text: str) -> List[str]:
    """Return all literal-pattern matches found in *text* (word-boundary aware)."""
    matches: List[str] = []
    for p in settings.PLACEHOLDER_PATTERNS:
        try:
            if re.search(r"\b" + re.escape(p) + r"\b", text, flags=re.I) or re.search(re.escape(p), text, flags=re.I):
                matches.append(p)
        except re.error:
            if re.search(re.escape(p), text, flags=re.I):
                matches.append(p)
    return matches


def _dedup_matches_preserve_order(matches: List[str]) -> List[str]:
    """Deduplicate a list of match strings preserving insertion order (key = lower-case)."""
    seen: set = set()
    result: List[str] = []
    for m in matches:
        key = m.lower()
        if key not in seen:
            seen.add(key)
            result.append(m)
    return result


def find_placeholders_in_text(text: str, use_regex: Optional[bool] = None) -> Tuple[bool, List[str]]:
    """Detect placeholders in a plain text string.

    Returns a tuple (has_placeholders, matches_list). If use_regex is None the
    behavior is controlled by settings.PLACEHOLDER_USE_REGEX. When regex is
    enabled, patterns from settings.PLACEHOLDER_REGEX_PATTERNS are compiled with
    re.IGNORECASE and searched; matched substrings are returned. When regex is
    disabled, a case-insensitive substring/word-boundary search is performed
    against settings.PLACEHOLDER_PATTERNS for compatibility with existing
    behavior.
    """
    if not text:
        return False, []
    if use_regex is None:
        use_regex = settings.PLACEHOLDER_USE_REGEX

    raw = _find_regex_placeholder_matches(text) if use_regex else _find_literal_placeholder_matches(text)
    dedup = _dedup_matches_preserve_order(raw)
    return (len(dedup) > 0, dedup)


_HTML_LOADING_CLASS_KEYWORDS = ('shimmer', 'loading', 'loader', 'nds-shimmer', 'notion-unknown', 'placeholder', 'skeleton')
_HTML_LOADING_ATTR_KEYWORDS = ('loading', 'busy', 'placeholder', 'skeleton', 'shimmer', 'unknown')


def _find_class_based_placeholders(html: str) -> List[Dict]:
    """Scan CSS classes in *html* for loading/shimmer/placeholder indicators."""
    results: List[Dict] = []
    for m in re.finditer(r'class=["\']([^"\']+)["\']', html, flags=re.I):
        for cls in m.group(1).split():
            cls_low = cls.lower()
            if any(kw in cls_low for kw in _HTML_LOADING_CLASS_KEYWORDS):
                ctx = html[max(0, m.start() - 60): m.end() + 60]
                results.append({'selector_or_snippet': '.' + cls, 'match_type': 'class', 'context': ctx})
    return results


def _find_attr_based_placeholders(html: str) -> List[Dict]:
    """Scan HTML attributes in *html* for loading-state indicators."""
    results: List[Dict] = []
    for m in re.finditer(r'([a-zA-Z0-9_\-:]+)=["\']([^"\']*)["\']', html):
        attr = m.group(1).lower()
        val = m.group(2).lower()
        is_loading_attr = attr in ('aria-busy', 'data-loading') or any(k in attr for k in _HTML_LOADING_ATTR_KEYWORDS)
        is_loading_val = any(k in val for k in _HTML_LOADING_ATTR_KEYWORDS)
        if is_loading_attr or is_loading_val:
            ctx = html[max(0, m.start() - 60): m.end() + 60]
            results.append({'selector_or_snippet': f'{m.group(1)}="{m.group(2)}"', 'match_type': 'attribute', 'context': ctx})
    return results


def _find_text_placeholder_results(text_content: str, use_regex: bool) -> List[Dict]:
    """Scan plain *text_content* for placeholder patterns; return result dicts."""
    results: List[Dict] = []
    if use_regex:
        for pat in _ensure_compiled_patterns():
            for m in pat.finditer(text_content):
                idx = m.start()
                snippet = text_content[max(0, idx - 60): idx + len(m.group(0)) + 60].strip()
                results.append({'selector_or_snippet': m.group(0), 'match_type': 'regex', 'context': snippet})
    else:
        for k in settings.PLACEHOLDER_PATTERNS:
            for match in re.finditer(re.escape(k), text_content, flags=re.I):
                idx = match.start()
                snippet = text_content[max(0, idx - 60): idx + len(k) + 60].strip()
                results.append({'selector_or_snippet': k, 'match_type': 'text', 'context': snippet})
    return results


def _dedup_html_results(results: List[Dict]) -> List[Dict]:
    """Deduplicate HTML placeholder results by (selector_or_snippet, match_type)."""
    seen: set = set()
    dedup: List[Dict] = []
    for r in results:
        key = (r.get('selector_or_snippet'), r.get('match_type'))
        if key not in seen:
            seen.add(key)
            dedup.append(r)
    return dedup


def find_placeholders_in_html(html: str, use_regex: Optional[bool] = None) -> Tuple[bool, List[Dict]]:
    """Detect placeholders inside HTML markup.

    Returns (has_placeholders, occurrences) where each occurrence is a dict with
    keys: 'selector_or_snippet' (str), 'match_type' ('text'|'class'|'attribute'|'regex'),
    and 'context' (str) containing a snippet around the match. Content inside
    tags listed in settings.PLACEHOLDER_DETECTION_IGNORE_TAGS is ignored when
    scanning text content.
    """
    if not html:
        return False, []
    if use_regex is None:
        use_regex = settings.PLACEHOLDER_USE_REGEX

    # Remove/ignore content inside configured tags (script/style by default)
    text_html = html
    for tag in settings.PLACEHOLDER_DETECTION_IGNORE_TAGS:
        if not tag:
            continue
        # remove <tag ...>...</tag> (non-greedy)
        text_html = re.sub(fr"<({tag})\b[^>]*>.*?</\1>", ' ', text_html, flags=re.I | re.S)

    results: List[Dict] = []
    results.extend(_find_class_based_placeholders(html))
    results.extend(_find_attr_based_placeholders(html))
    text_content = re.sub(r'<[^>]+>', ' ', text_html)
    results.extend(_find_text_placeholder_results(text_content, use_regex))
    dedup = _dedup_html_results(results)
    return (len(dedup) > 0, dedup)


def detect_placeholders_in_text(text: str) -> list[str]:
    """Compatibility wrapper for older API: returns list of matched strings."""
    _, matches = find_placeholders_in_text(text)
    return matches


def detect_placeholders_in_html(html: str) -> list[dict]:
    """Compatibility wrapper for older API: returns list of occurrence dicts."""
    _, occ = find_placeholders_in_html(html)
    return occ


# ---------------------------------------------------------------------------
# Block conversion helpers (used by normalize_notion_blocks_to_html)
# ---------------------------------------------------------------------------

def _extract_leaf_children(block) -> list:
    """Return children of the contenteditable leaf inside *block*, or block's direct children."""
    leaf = block.find(attrs={"data-content-editable-leaf": True})
    if leaf:
        return list(leaf.children)
    return list(block.children)


def _copy_children_into(src_children, dest_tag) -> None:
    """Move/copy *src_children* nodes into *dest_tag*, handling both Tag and text nodes."""
    from bs4 import Tag, NavigableString  # type: ignore[import]
    for child in src_children:
        if isinstance(child, Tag):
            dest_tag.append(child.extract())
        else:
            dest_tag.append(NavigableString(str(child)))


def _replace_notion_list_blocks(soup, block_class: str, list_tag: str) -> None:
    """Convert Notion list-block divs to <li> elements and group them into <ul>/<ol>."""
    from bs4 import Tag, NavigableString  # type: ignore[import]
    for block in soup.find_all("div", class_=re.compile(r"\b" + re.escape(block_class) + r"\b")):
        for marker in block.find_all(class_="notion-list-item-box-left"):
            marker.decompose()
        new_li = soup.new_tag("li")
        _copy_children_into(_extract_leaf_children(block), new_li)
        block.replace_with(new_li)

    for li in soup.find_all("li"):
        if li.parent and li.parent.name in ("ul", "ol"):
            continue
        prev = li.previous_sibling
        while prev and isinstance(prev, NavigableString) and not prev.strip():
            prev = prev.previous_sibling
        if prev and isinstance(prev, Tag) and prev.name == list_tag:
            prev.append(li.extract())
        else:
            new_list = soup.new_tag(list_tag)
            li.replace_with(new_list)
            new_list.append(li)


def _replace_heading_blocks(soup) -> None:
    """Replace Notion heading-block divs with semantic <h1>/<h2>/<h3>."""
    _HEADING_MAP = {
        "notion-header-block": "h1",
        "notion-sub_header-block": "h2",
        "notion-sub_sub_header-block": "h3",
    }
    for cls, tag_name in _HEADING_MAP.items():
        for block in soup.find_all("div", class_=re.compile(r"\b" + re.escape(cls) + r"\b")):
            existing = block.find(["h1", "h2", "h3", "h4"])
            new_h = soup.new_tag(tag_name)
            if existing:
                _copy_children_into(list(existing.children), new_h)
            else:
                _copy_children_into(_extract_leaf_children(block), new_h)
            block.replace_with(new_h)


def _replace_callout_blocks(soup) -> None:
    """Replace Notion callout/quote-block divs with <blockquote>."""
    for block in soup.find_all("div", class_=re.compile(r"\bnotion-(callout|quote)-block\b")):
        new_bq = soup.new_tag("blockquote")
        icon = block.find("img")
        if icon:
            icon.decompose()
        _copy_children_into(_extract_leaf_children(block), new_bq)
        block.replace_with(new_bq)


def _replace_toggle_blocks(soup) -> None:
    """Replace Notion toggle-block divs with <details open><summary>…</summary>…</details>."""
    from bs4 import Tag  # type: ignore[import]
    for block in soup.find_all("div", class_=re.compile(r"\bnotion-toggle-block\b")):
        new_details = soup.new_tag("details", open="")
        new_summary = soup.new_tag("summary")
        title_el = block.find(["h1", "h2", "h3", "h4"]) or block.find("button")
        if title_el:
            _copy_children_into(list(title_el.children), new_summary)
            title_el.decompose()
        new_details.append(new_summary)
        for child in [*block.children]:
            if isinstance(child, Tag):
                new_details.append(child.extract())
        block.replace_with(new_details)


def normalize_notion_blocks_to_html(html: str) -> str:
    """Convert Notion-specific block divs to standard semantic HTML.

    Runs BEFORE markdownify so that block-level formatting (headings,
    paragraphs, lists, callouts, dividers, toggles) is preserved in the
    Markdown output.  Block types handled:

    * ``notion-text-block``           → ``<p>``
    * ``notion-header-block``         → ``<h1>``
    * ``notion-sub_header-block``     → ``<h2>``
    * ``notion-sub_sub_header-block`` → ``<h3>``
    * ``notion-callout-block``        → ``<blockquote>``
    * ``notion-quote-block``          → ``<blockquote>``
    * ``notion-divider-block``        → ``<hr>``
    * ``notion-bulleted_list-block``  → ``<ul><li>``
    * ``notion-numbered_list-block``  → ``<ol><li>``
    * ``notion-toggle-block``         → ``<details><summary>``
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return html

    soup = BeautifulSoup(html or "", "html.parser")

    # 1. Dividers → <hr>
    for block in soup.find_all("div", class_=re.compile(r"\bnotion-divider-block\b")):
        block.replace_with(soup.new_tag("hr"))

    # 2. Heading blocks — find existing <h*> inside and promote it
    _replace_heading_blocks(soup)

    # 3. Callout / Quote blocks → <blockquote>
    _replace_callout_blocks(soup)

    # 4. Toggle blocks → <details open><summary>title</summary>…</details>
    _replace_toggle_blocks(soup)

    # 5. Text blocks → <p>
    for block in soup.find_all("div", class_=re.compile(r"\bnotion-text-block\b")):
        new_p = soup.new_tag("p")
        _copy_children_into(_extract_leaf_children(block), new_p)
        block.replace_with(new_p)

    # 6. List blocks → <ul>/<ol>
    _replace_notion_list_blocks(soup, "notion-bulleted_list-block", "ul")
    _replace_notion_list_blocks(soup, "notion-numbered_list-block", "ol")

    return str(soup)


# ---------------------------------------------------------------------------
# Code block normalization helpers (used by normalize_notion_code_blocks)
# ---------------------------------------------------------------------------

def _apply_compiled_pattern_removals(text: str) -> str:
    """Apply all compiled regex patterns to remove placeholder matches from *text*."""
    for pat in _ensure_compiled_patterns():
        try:
            text = pat.sub('', text)
        except Exception:
            pass
    return text


def _apply_literal_pattern_removals(text: str) -> str:
    """Apply all literal PLACEHOLDER_PATTERNS to remove occurrences from *text*."""
    for p in getattr(settings, 'PLACEHOLDER_PATTERNS', []):
        try:
            text = re.sub(re.escape(p), '', text, flags=re.I)
        except Exception:
            try:
                text = text.replace(p, '')
            except Exception:
                pass
    return text


def _strip_code_placeholder_text(text: str) -> str:
    """Remove configured placeholder patterns from *text* and normalize non-breaking spaces."""
    if not text:
        return text
    if getattr(settings, 'PLACEHOLDER_USE_REGEX', True):
        text = _apply_compiled_pattern_removals(text)
    text = _apply_literal_pattern_removals(text)
    return text.replace('\u00A0', ' ').replace('\xa0', ' ')


def _code_is_loading_phrase(text: str) -> bool:
    """Return True if *text* is a Notion loading-placeholder phrase (not real code)."""
    lower = text.strip().lower()
    return lower.startswith(('carregando', 'loading')) and any(
        k in lower for k in ('plain', 'codigo', 'loading code')
    )


def _apply_code_text_cleanup(raw_text: str) -> str:
    """Unescape HTML, normalize spaces and strip placeholders from code block text.

    Returns empty string when the block is entirely placeholder-only or a loading phrase.
    """
    import html as _html  # stdlib alias — 'html' parameter in callers would shadow the module
    code_text = _html.unescape(raw_text)
    code_text = code_text.replace('\u00A0', ' ').replace('\xa0', ' ').replace('\u00a0', ' ')
    stripped = _strip_code_placeholder_text(code_text).strip()
    if not stripped or _code_is_loading_phrase(code_text):
        return ''
    final = code_text
    if getattr(settings, 'PLACEHOLDER_USE_REGEX', True):
        final = _apply_compiled_pattern_removals(final)
    final = _apply_literal_pattern_removals(final)
    return final


def _detect_code_lang_bs4(el) -> Optional[str]:
    """Detect code language from a BeautifulSoup element's attributes and classes."""
    for key in ('data-language', 'data-lang', 'data-block-language'):
        v = el.get(key)
        if v:
            return str(v).strip()
    for c in (el.get('class') or []):
        if not c:
            continue
        m = re.match(r'(?:language|lang)[-_](.+)', str(c), flags=re.I)
        if m:
            return m.group(1)
    return None


def _detect_code_lang_str(tag_opening: str, class_attr: str) -> Optional[str]:
    """Detect code language from the opening HTML tag string and class attribute."""
    mlang = re.search(r'data-(?:language|lang|block-language)=["\']([^"\']+)["\']', tag_opening, flags=re.I)
    if mlang:
        return mlang.group(1)
    mcls = re.search(r'(?:language|lang)[-_](\w+)', class_attr, flags=re.I)
    return mcls.group(1) if mcls else None


def _code_div_has_candidate_classes(class_attr: str) -> bool:
    """Return True if the div class attribute marks it as a Notion code block candidate."""
    if not class_attr:
        return False
    cl = class_attr.lower()
    return 'line-numbers' in cl or 'notion-code-block' in cl


def _find_bs4_code_candidates(soup) -> list:
    """Find BS4 elements that look like Notion code blocks needing normalization."""
    def _no_code(el) -> bool:
        return not el.find('pre') and not el.find('code')

    def _clset(el) -> set:
        classes = el.get('class') or []
        if isinstance(classes, str):
            classes = [classes]
        return {c.lower() for c in classes}

    candidates = [
        el for el in soup.find_all(True)
        if _no_code(el) and {'line-numbers', 'notion-code-block'} <= _clset(el)
    ]
    if not candidates:
        candidates = [
            el for el in soup.find_all(True)
            if _no_code(el) and len(el.find_all('span')) >= 4
        ]
    return candidates


def _process_bs4_code_candidate(soup, el) -> None:
    """Replace a BS4 element with a normalised <pre><code> block, in-place."""
    from bs4 import NavigableString  # type: ignore[import]
    for br in el.find_all('br'):
        br.replace_with(NavigableString('\n'))
    parts = [str(node) for node in el.descendants if isinstance(node, NavigableString)]
    add_eq_spaces = any(p.strip() == '=' for p in parts)
    final_text = _apply_code_text_cleanup(''.join(parts))
    if add_eq_spaces:
        final_text = re.sub(r"\s*=\s*", ' = ', final_text)
    lang = _detect_code_lang_bs4(el)
    pre = soup.new_tag('pre')
    code = soup.new_tag('code')
    if lang:
        code['class'] = [f'language-{lang}']
    code.append(NavigableString(final_text))
    pre.append(code)
    el.replace_with(pre)


def _process_regex_code_match(m) -> Optional[tuple]:
    """Process a regex-matched div candidate; return (original, replacement) or None."""
    import html as _html
    class_attr = m.group(1)
    inner = m.group(2)
    if not _code_div_has_candidate_classes(class_attr):
        return None
    if re.search(r'<\s*(?:pre|code)\b', inner, flags=re.I):
        return None
    text = re.sub(r'<br\s*/?>', '\n', inner, flags=re.I)
    text = re.sub(r'<\s*(?:span|div|code|pre)[^>]*>', '', text)
    text = re.sub(r'<\s*/\s*(?:span|div|code|pre)[^>]*>', '', text)
    final_text = _apply_code_text_cleanup(text)
    add_eq_spaces = bool(re.search(r'<span[^>]*>\s*=\s*</span>', m.group(0), flags=re.I))
    if add_eq_spaces:
        final_text = re.sub(r"\s*=\s*", ' = ', final_text)
    lang = _detect_code_lang_str(m.group(0), class_attr)
    code_class = f'language-{lang}' if lang else ''
    class_attr_text = f' class="{code_class}"' if code_class else ''
    replacement = f"<pre><code{class_attr_text}>{_html.escape(final_text)}</code></pre>"
    return m.group(0), replacement


def normalize_notion_code_blocks(html: str) -> str:
    """Normalize Notion code blocks that are split across multiple <span> elements into
    a single <pre><code> block. Uses BeautifulSoup if available, falling back to a
    regex-based approach when bs4 isn't installed.

    This function is careful not to modify blocks that already contain <pre> or
    <code> elements. It also removes configured placeholder substrings and
    attempts to detect the language from element attributes or classes.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or '', settings.HTML_PARSER)
        for el in _find_bs4_code_candidates(soup):
            _process_bs4_code_candidate(soup, el)
        return str(soup)
    except Exception:
        pass  # bs4 not available or failed — use regex fallback

    _candidate_div_re = re.compile(r'<div[^>]*class=["\']([^"\']*)["\'][^>]*>(.*?)</div>', flags=re.I | re.S)
    new_out = html or ''
    for m in _candidate_div_re.finditer(html or ''):
        result = _process_regex_code_match(m)
        if result:
            orig, replacement = result
            new_out = new_out.replace(orig, replacement)
    return new_out

