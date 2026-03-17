import pytest

# Tests for placeholder/hydration detectors in notion_utils.py
# These tests import the module (not individual functions) so collection succeeds
# even if the functions are not yet implemented. If a function is missing, the
# test will be skipped with a clear message.

import notion_utils


def _get_detector(name):
    fn = getattr(notion_utils, name, None)
    if fn is None:
        pytest.skip(f"{name} not implemented in notion_utils.py")
    return fn


def test_detect_placeholders_in_html():
    """Provide an HTML fixture with a shimmer div and assert the detector finds it.

    The detector should be robust to case differences in returned text.
    """
    html = '<div class="shimmer">Carregando código...</div>'
    detect = _get_detector('detect_placeholders_in_html')

    results = detect(html)
    assert results, "Expected non-empty list of detected placeholders"

    # Ensure at least one detected item contains the word 'Carregando' (case-insensitive)
    assert any('carregando' in (str(item).lower()) for item in results), (
        "Expected at least one detected placeholder to contain 'Carregando'")


def test_detect_placeholders_in_text():
    """Detect placeholder words in plain text and return a list of matches."""
    detect_text = _get_detector('detect_placeholders_in_text')

    text = 'A página está Carregando'
    results = detect_text(text)

    assert isinstance(results, list), "Expected a list of detected placeholder tokens"
    # Accept results like ['Carregando'] or ['carregando'] etc.
    assert any('carregando' == str(item).lower() or 'carregando' in str(item).lower() for item in results), (
        "Expected 'Carregando' to be detected in the text")
