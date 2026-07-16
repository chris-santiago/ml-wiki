import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wiki_util import slugify, detect_source_type, extract_citations, normalize_tag


def test_slugify_basic():
    assert slugify("Hello World") == "hello-world"


def test_slugify_strips_punctuation():
    assert slugify("What's the deal with RNNs?") == "whats-the-deal-with-rnns"


def test_slugify_collapses_hyphens():
    assert slugify("foo--bar---baz") == "foo-bar-baz"


def test_slugify_strips_leading_trailing_hyphens():
    assert slugify("--hello--") == "hello"


def test_slugify_max_length():
    long = "this is a very long title that should be truncated at some point"
    result = slugify(long, max_len=30)
    assert len(result) <= 30


def test_slugify_preserves_numbers():
    assert slugify("gpt 5.4 nano benchmark") == "gpt-54-nano-benchmark"


def test_slugify_with_prefix():
    assert slugify("my idea title", prefix="synthesis-") == "synthesis-my-idea-title"


def test_detect_pdf():
    assert detect_source_type("paper.pdf") == "pdf"


def test_detect_markdown():
    assert detect_source_type("notes.md") == "markdown"


def test_detect_text():
    assert detect_source_type("readme.txt") == "text"


def test_detect_url_https():
    assert detect_source_type("https://arxiv.org/abs/2312.00752") == "url"


def test_detect_url_http():
    assert detect_source_type("http://example.com") == "url"


def test_detect_image_png():
    assert detect_source_type("chart.png") == "image"


def test_detect_image_jpg():
    assert detect_source_type("photo.jpg") == "image"


def test_detect_image_jpeg():
    assert detect_source_type("photo.jpeg") == "image"


def test_detect_image_svg():
    assert detect_source_type("diagram.svg") == "image"


def test_detect_unknown():
    assert detect_source_type("smith2024attention") is None


def test_extract_citations_basic():
    text = "As shown in [[smith2024]], this contradicts [[jones2023fast]]."
    assert extract_citations(text) == ["smith2024", "jones2023fast"]


def test_extract_citations_empty():
    assert extract_citations("No citations here.") == []


def test_extract_citations_deduplicates():
    text = "See [[foo]] and also [[foo]] again."
    assert extract_citations(text) == ["foo"]


def test_normalize_tag_basic():
    assert normalize_tag("Machine Learning") == "machine-learning"


def test_normalize_tag_strips_special():
    assert normalize_tag("C++ Programming") == "c-programming"


def test_normalize_tag_already_clean():
    assert normalize_tag("fraud-detection") == "fraud-detection"
