from notion_converter import extract_page_id


def test_extract_page_id_from_url():
    url = 'https://www.notion.so/Some-Title-abcdef123456'
    assert extract_page_id(url) == 'abcdef123456'


def test_extract_page_id_from_file_path():
    path = 'file:///C:/pages/Some-Title-abcdef123456'
    assert extract_page_id(path) == 'abcdef123456'
