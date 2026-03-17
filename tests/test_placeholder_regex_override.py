import importlib
import os


def test_placeholder_regex_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv('PLACEHOLDER_REGEX_PATTERNS', 'FOO,bar')
    monkeypatch.setenv('PLACEHOLDER_USE_REGEX', 'True')
    # reload settings module to pick up env changes
    import settings
    importlib.reload(settings)

    import notion_utils
    importlib.reload(notion_utils)

    has, matches = notion_utils.find_placeholders_in_text('foo happened')
    assert has is True
    assert any('foo' in m.lower() for m in matches)
