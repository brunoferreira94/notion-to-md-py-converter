from converter_config import ConverterConfig
from page_renderer import PageRenderer
from notion_converter import NotionMarkdownConverter
import renderers
import settings


def test_attr_str_behaviour():
    # `_attr_str` lives in notion_converter, but we can validate via export
    from notion_converter import _attr_str

    assert _attr_str(None) == ''
    assert _attr_str([]) == ''
    assert _attr_str(['a', 'b']) == 'a'
    assert _attr_str('x') == 'x'
    assert _attr_str(123) == '123'


def test_renderer_falls_back_to_requests_when_forced(monkeypatch):
    monkeypatch.setattr(renderers, 'PLAYWRIGHT_AVAILABLE', True)
    monkeypatch.setattr(renderers, 'fetch_html_requests', lambda u: 'req')

    renderer = PageRenderer(use_requests=True)
    assert renderer.render('http://example.com') == 'req'


def test_renderer_uses_playwright_when_available(monkeypatch):
    monkeypatch.setattr(renderers, 'PLAYWRIGHT_AVAILABLE', True)
    monkeypatch.setattr(renderers, 'render_with_playwright', lambda *args, **kwargs: 'pw')

    renderer = PageRenderer(use_requests=False)
    assert renderer.render('http://example.com') == 'pw'


def test_resolve_output_paths_uses_export_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'EXPORT_BASE_DIR', str(tmp_path))
    config = ConverterConfig(page_url='http://example.com', output=None)
    converter = NotionMarkdownConverter(config)

    out_path, output_folder, assets_dir = converter._resolve_output_paths('Title')
    assert output_folder is not None
    assert str(output_folder).startswith(str(tmp_path))
    assert out_path.parent == output_folder
    assert assets_dir is not None
    assert assets_dir.parent == output_folder
