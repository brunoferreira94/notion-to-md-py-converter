import notion_utils


def test_find_placeholders_in_html_class():
    html = '<div class="nds-shimmer-text"></div>'
    has, occ = notion_utils.find_placeholders_in_html(html)
    assert has is True
    assert any(o.get('match_type') in ('class','text','regex') for o in occ)


def test_find_placeholders_in_html_attribute():
    html = '<div aria-busy="true"></div>'
    has, occ = notion_utils.find_placeholders_in_html(html)
    assert has is True
    assert any(o.get('match_type') == 'attribute' for o in occ)


def test_find_placeholders_in_html_click_to_open():
    html = '<div>(click to open)</div>'
    has, occ = notion_utils.find_placeholders_in_html(html)
    assert has is True
    assert any(o.get('match_type') in ('text','regex') for o in occ)
