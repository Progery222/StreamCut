from utils.helpers import safe_filename, url_to_id


def test_safe_filename_strips_special_chars():
    assert safe_filename("Hello, World!") == "Hello-World"


def test_safe_filename_truncates_to_max_length():
    result = safe_filename("a" * 100, max_length=10)
    assert len(result) == 10


def test_safe_filename_fallback_for_empty():
    assert safe_filename("!!!") == "video"


def test_safe_filename_keeps_unicode():
    assert "Привет" in safe_filename("Привет мир")


def test_url_to_id_is_deterministic():
    url = "https://youtube.com/watch?v=abc123"
    assert url_to_id(url) == url_to_id(url)


def test_url_to_id_length():
    assert len(url_to_id("https://example.com")) == 12
