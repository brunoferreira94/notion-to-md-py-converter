import html
import re
from bs4 import BeautifulSoup
import settings
from notion_utils import normalize_notion_code_blocks


def _get_code_text(out_html: str) -> str:
    soup = BeautifulSoup(out_html, 'html.parser')
    code = soup.find('code')
    return code.get_text() if code else ''


def test_normalize_spanned_code_block_basic():
    inp = '<div class="line-numbers notion-code-block"><span class="token">git</span><span> commit --author</span><span class="token">=</span><span class="token">"PR Writer &lt;prwriter@reveloexperts.com&gt;"</span></div>'
    out = normalize_notion_code_blocks(inp)
    assert '<span' not in out
    assert '<pre' in out and '<code' in out
    text = _get_code_text(out)
    assert text == 'git commit --author = "PR Writer <prwriter@reveloexperts.com>"' or text == 'git commit --author = "PR Writer <prwriter@reveloexperts.com>"'


def test_remove_placeholder_only_block():
    inp = '<div class="line-numbers notion-code-block"><span>Carregando código de Plain Text...</span></div>'
    out = normalize_notion_code_blocks(inp)
    text = _get_code_text(out)
    assert 'Carregando' not in out
    assert text == ''


def test_partial_placeholder_removal():
    inp = '<div class="line-numbers notion-code-block"><span>Carregando</span><span>def</span><span> x=1</span></div>'
    out = normalize_notion_code_blocks(inp)
    text = _get_code_text(out)
    assert text.strip() == 'def x=1'


def test_entities_and_nbsp():
    inp = '<div class="line-numbers notion-code-block"><span>foo&nbsp;&lt;bar&gt;</span></div>'
    out = normalize_notion_code_blocks(inp)
    text = _get_code_text(out)
    assert text == 'foo <bar>'
