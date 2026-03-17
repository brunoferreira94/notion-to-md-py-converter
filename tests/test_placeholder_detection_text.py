import importlib
import settings
import notion_utils


def test_find_placeholders_in_text_basic():
    has, matches = notion_utils.find_placeholders_in_text('Carregando...')
    assert has is True
    assert any('carregando' in m.lower() for m in matches)

    has2, matches2 = notion_utils.find_placeholders_in_text('Some Loading code here')
    assert has2 is True
    assert any('loading' in m.lower() for m in matches2)


def test_detect_placeholders_in_text_compat():
    res = notion_utils.detect_placeholders_in_text('Loading')
    assert isinstance(res, list)
    assert len(res) > 0
