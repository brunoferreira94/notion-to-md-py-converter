import notion_converter


def test_bs4_guards(monkeypatch, tmp_path):
    # Simulate BeautifulSoup not being available
    monkeypatch.setattr(notion_converter, 'BS4_AVAILABLE', False)

    html = '<html><head><title>Test</title></head><body><p>hi</p></body></html>'

    # process_html_assets should return original html and empty downloads when BS4 not available
    out_html, downloads = notion_converter.process_html_assets(html, 'http://example.com', str(tmp_path))
    assert out_html == html
    assert downloads == []

    # normalize_html_for_markdown should return original html when BS4 not available
    assert notion_converter.normalize_html_for_markdown(html) == html

    # extract_title_from_html should return None when BS4 not available
    assert notion_converter.extract_title_from_html(html) is None
