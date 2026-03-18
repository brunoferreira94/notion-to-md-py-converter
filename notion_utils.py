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

    matches: List[str] = []
    if use_regex:
        patterns = _ensure_compiled_patterns()
        for pat in patterns:
            for m in pat.finditer(text):
                matches.append(m.group(0))
    else:
        for p in settings.PLACEHOLDER_PATTERNS:
            try:
                # try word-boundary aware match and plain substring fallback
                if re.search(r"\b" + re.escape(p) + r"\b", text, flags=re.I) or re.search(re.escape(p), text, flags=re.I):
                    matches.append(p)
            except re.error:
                if re.search(re.escape(p), text, flags=re.I):
                    matches.append(p)

    # deduplicate preserving order
    seen = set()
    dedup: List[str] = []
    for m in matches:
        key = m.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(m)

    return (len(dedup) > 0, dedup)


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

    results: List[Dict] = []

    # Remove/ignore content inside configured tags (script/style by default)
    text_html = html
    for tag in settings.PLACEHOLDER_DETECTION_IGNORE_TAGS:
        if not tag:
            continue
        # remove <tag ...>...</tag> (non-greedy)
        text_html = re.sub(fr"<({tag})\b[^>]*>.*?</\1>", ' ', text_html, flags=re.I | re.S)

    # Detect class-based placeholders (heuristic)
    for m in re.finditer(r'class=["\']([^"\']+)["\']', html, flags=re.I):
        classes = m.group(1)
        for cls in classes.split():
            cls_low = cls.lower()
            if any(keyword in cls_low for keyword in ['shimmer', 'loading', 'loader', 'nds-shimmer', 'notion-unknown', 'placeholder', 'skeleton']):
                sel = '.' + cls
                ctx = html[max(0, m.start() - 60) : m.end() + 60]
                results.append({'selector_or_snippet': sel, 'match_type': 'class', 'context': ctx})

    # Detect attributes that indicate loading state
    for m in re.finditer(r'([a-zA-Z0-9_\-:]+)=["\']([^"\']*)["\']', html):
        attr = m.group(1).lower()
        val = m.group(2).lower()
        is_loading_attr = attr in ('aria-busy', 'data-loading') or any(k in attr for k in ['loading', 'busy', 'placeholder', 'skeleton', 'shimmer', 'unknown'])
        is_loading_val = any(k in val for k in ['loading', 'shimmer', 'placeholder', 'skeleton', 'unknown'])
        if is_loading_attr or is_loading_val:
            sel = f'{m.group(1)}="{m.group(2)}"'
            ctx = html[max(0, m.start() - 60) : m.end() + 60]
            results.append({'selector_or_snippet': sel, 'match_type': 'attribute', 'context': ctx})

    # Extract text content (ignoring tags entirely) for text/regex scanning
    text_content = re.sub(r'<[^>]+>', ' ', text_html)

    if use_regex:
        patterns = _ensure_compiled_patterns()
        for pat in patterns:
            for m in pat.finditer(text_content):
                idx = m.start()
                snippet = text_content[max(0, idx - 60) : idx + len(m.group(0)) + 60].strip()
                results.append({'selector_or_snippet': m.group(0), 'match_type': 'regex', 'context': snippet})
    else:
        for k in settings.PLACEHOLDER_PATTERNS:
            for match in re.finditer(re.escape(k), text_content, flags=re.I):
                idx = match.start()
                snippet = text_content[max(0, idx - 60) : idx + len(k) + 60].strip()
                results.append({'selector_or_snippet': k, 'match_type': 'text', 'context': snippet})

    # Deduplicate by (selector_or_snippet, match_type)
    seen = set()
    dedup: List[Dict] = []
    for r in results:
        key = (r.get('selector_or_snippet'), r.get('match_type'))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)

    return (len(dedup) > 0, dedup)


def detect_placeholders_in_text(text: str) -> list[str]:
    """Compatibility wrapper for older API: returns list of matched strings."""
    _, matches = find_placeholders_in_text(text)
    return matches


def detect_placeholders_in_html(html: str) -> list[dict]:
    """Compatibility wrapper for older API: returns list of occurrence dicts."""
    _, occ = find_placeholders_in_html(html)
    return occ


def normalize_notion_code_blocks(html: str) -> str:
    """Normalize Notion code blocks that are split across multiple <span> elements into
    a single <pre><code> block. Uses BeautifulSoup if available, falling back to a
    regex-based approach when bs4 isn't installed.

    This function is careful not to modify blocks that already contain <pre> or
    <code> elements. It also removes configured placeholder substrings and
    attempts to detect the language from element attributes or classes.
    """
    import html as _html
    import re as _re

    # Helper to remove placeholders from text and detect if text was only placeholders
    def _strip_placeholders(text: str) -> str:
        if not text:
            return text
        use_regex = getattr(settings, 'PLACEHOLDER_USE_REGEX', True)
        if use_regex:
            # Use compiled regex patterns first
            patterns = _ensure_compiled_patterns()
            for pat in patterns:
                try:
                    text = pat.sub('', text)
                except Exception:
                    pass
            # Also remove simple configured placeholder substrings (covers cases where regex uses word boundaries)
            for p in getattr(settings, 'PLACEHOLDER_PATTERNS', []):
                try:
                    text = _re.sub(_re.escape(p), '', text, flags=_re.I)
                except Exception:
                    try:
                        text = text.replace(p, '')
                    except Exception:
                        pass
        else:
            for p in getattr(settings, 'PLACEHOLDER_PATTERNS', []):
                try:
                    text = _re.sub(_re.escape(p), '', text, flags=_re.I)
                except Exception:
                    text = text.replace(p, '')
        # normalize nbsp and non-breaking
        text = text.replace('\u00A0', ' ').replace('\xa0', ' ')
        # collapse nothing else
        return text

    try:
        # Try to use BeautifulSoup if available
        from bs4 import BeautifulSoup, NavigableString

        soup = BeautifulSoup(html or '', settings.HTML_PARSER)

        # Candidate selection
        candidates = []

        # Primary: elements whose class contains both 'line-numbers' and 'notion-code-block'
        for el in soup.find_all(True):
            classes = el.get('class') or []
            if isinstance(classes, str):
                classes = [classes]
            clset = {c.lower() for c in classes}
            if 'line-numbers' in clset and 'notion-code-block' in clset:
                # skip if already contains pre/code
                if el.find('pre') or el.find('code'):
                    continue
                candidates.append(el)

        # Fallback: elements with >=4 <span> descendants and no existing <pre> or <code>
        if not candidates:
            for el in soup.find_all(True):
                if el.find('pre') or el.find('code'):
                    continue
                spans = el.find_all('span')
                if len(spans) >= 4:
                    candidates.append(el)

        # Process each candidate
        for el in candidates:
            # Replace <br> with newline text nodes to preserve line breaks
            for br in el.find_all('br'):
                br.replace_with(NavigableString('\n'))

            # Collect text from descendant text nodes in DOM order
            parts = []
            for node in el.descendants:
                if isinstance(node, NavigableString):
                    parts.append(str(node))
            # Detect if '=' appeared as a standalone token in its own span/text node
            add_spaces_around_eq = any(p.strip() == '=' for p in parts)
            code_text = ''.join(parts)

            # Unescape HTML entities and normalize non-breaking spaces
            code_text = _html.unescape(code_text)
            code_text = code_text.replace('\u00A0', ' ').replace('\xa0', ' ').replace('\u00a0', ' ')

            # Remove placeholder substrings. If the block consists only of placeholders, make it empty.
            stripped = _strip_placeholders(code_text).strip()
            # Heuristic: if after removing known placeholders nothing remains, it's placeholder-only.
            # Additionally treat common Notion loading phrases (e.g. "Carregando código de Plain Text...")
            # as placeholder-only only when they include typical filler words.
            lower = code_text.strip().lower()
            is_loading_phrase = False
            if lower.startswith(('carregando', 'loading')) and any(k in lower for k in ('plain', 'codigo', 'loading code')):
                is_loading_phrase = True
            if not stripped or is_loading_phrase:
                final_text = ''
            else:
                # Remove placeholders only (preserve surrounding text)
                final_text = code_text
                # Apply compiled regex patterns if configured
                if getattr(settings, 'PLACEHOLDER_USE_REGEX', True):
                    pats = _ensure_compiled_patterns()
                    for p in pats:
                        try:
                            final_text = p.sub('', final_text)
                        except Exception:
                            pass
                # Also remove simple placeholder substrings to catch boundary-less cases
                for p in getattr(settings, 'PLACEHOLDER_PATTERNS', []):
                    try:
                        final_text = _re.sub(_re.escape(p), '', final_text, flags=_re.I)
                    except Exception:
                        try:
                            final_text = final_text.replace(p, '')
                        except Exception:
                            pass

            # Post-process small normalization (e.g. ensure spaces around '=' when tokens were split)
            if add_spaces_around_eq:
                final_text = _re.sub(r"\s*=\s*", ' = ', final_text)

            # Detect language from attributes or classes
            lang = None
            # attributes
            for key in ('data-language', 'data-lang', 'data-block-language'):
                v = el.get(key)
                if v:
                    lang = str(v).strip()
                    break
            if not lang:
                # classes like language-python or lang-python
                for c in (el.get('class') or []):
                    if not c:
                        continue
                    m = _re.match(r'(?:language|lang)[-_](.+)', str(c), flags=_re.I)
                    if m:
                        lang = m.group(1)
                        break
            code_class = f'language-{lang}' if lang else None

            # Build new <pre><code>
            pre = soup.new_tag('pre')
            code = soup.new_tag('code')
            if code_class:
                code['class'] = [code_class]
            # Use the final_text as a NavigableString so BeautifulSoup will escape it on output
            code.append(NavigableString(final_text))
            pre.append(code)

            # Replace the original element with the new pre/code
            el.replace_with(pre)

        return str(soup)

    except Exception:
        # bs4 not available or failed — fallback to regex
        pass

    # Module-level compiled regex cache for fallback pattern
    _candidate_div_re = _re.compile(r'<div[^>]*class=["\']([^"\']*)["\'][^>]*>(.*?)</div>', flags=_re.I | _re.S)

    def _has_candidate_classes(class_attr: str) -> bool:
        if not class_attr:
            return False
        cl = class_attr.lower()
        return 'line-numbers' in cl or 'notion-code-block' in cl

    out = html or ''
    new_out = out
    for m in _candidate_div_re.finditer(out):
        class_attr = m.group(1)
        inner = m.group(2)
        if not _has_candidate_classes(class_attr):
            continue
        # skip if it already contains <pre> or <code>
        if _re.search(r'<\s*(?:pre|code)\b', inner, flags=_re.I):
            continue
        # strip span/div tags but keep inner text
        text = _re.sub(r'<br\s*/?>', '\n', inner, flags=_re.I)
        text = _re.sub(r'<\s*(?:span|div|code|pre)[^>]*>', '', text)
        text = _re.sub(r'<\s*/\s*(?:span|div|code|pre)[^>]*>', '', text)
        text = _html.unescape(text)
        text = text.replace('\u00A0', ' ').replace('\xa0', ' ').replace('\u00a0', ' ')

        stripped = _strip_placeholders(text).strip()
        lower = text.strip().lower()
        is_loading_phrase = False
        if lower.startswith(('carregando', 'loading')) and any(k in lower for k in ('plain', 'codigo', 'loading code')):
            is_loading_phrase = True
        if not stripped or is_loading_phrase:
            final_text = ''
        else:
            final_text = text
            if getattr(settings, 'PLACEHOLDER_USE_REGEX', True):
                pats = _ensure_compiled_patterns()
                for p in pats:
                    try:
                        final_text = p.sub('', final_text)
                    except Exception:
                        pass
            # remove simple substrings too to catch boundary-less cases
            for p in getattr(settings, 'PLACEHOLDER_PATTERNS', []):
                try:
                    final_text = _re.sub(_re.escape(p), '', final_text, flags=_re.I)
                except Exception:
                    try:
                        final_text = final_text.replace(p, '')
                    except Exception:
                        pass
        # For regex-fallback, detect if '=' was a standalone span/token in the original inner HTML
        add_spaces_around_eq = bool(_re.search(r'<span[^>]*>\s*=\s*</span>', m.group(0), flags=_re.I))
        if add_spaces_around_eq:
            final_text = _re.sub(r"\s*=\s*", ' = ', final_text)

        # language detection from class_attr or attributes in the opening div
        lang = None
        mlang = _re.search(r'data-(?:language|lang|block-language)=["\']([^"\']+)["\']', m.group(0), flags=_re.I)
        if mlang:
            lang = mlang.group(1)
        else:
            mcls = _re.search(r'(?:language|lang)[-_](\w+)', class_attr, flags=_re.I)
            if mcls:
                lang = mcls.group(1)
        code_class = f'language-{lang}' if lang else ''

        esc = _html.escape(final_text)
        class_attr_text = f' class="{code_class}"' if code_class else ''
        replacement = f"<pre><code{class_attr_text}>{esc}</code></pre>"

        new_out = new_out.replace(m.group(0), replacement)

    return new_out

