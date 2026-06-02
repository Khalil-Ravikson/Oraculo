# tests/unit/domain/test_ocr_parser.py
import pytest
from unittest.mock import MagicMock, patch
from src.rag.ingestion.parser_factory import ParserFactory, AutoParserWrapper
from src.infrastructure.adapters.parsers.rapidocr_adapter import RapidOcrAdapter


@pytest.fixture
def mock_rapidocr():
    with patch("rapidocr_onnxruntime.RapidOCR") as mock_cls:
        mock_instance = MagicMock()
        # Mock engine call returns (res, elapse)
        # res is a list of [[box, text, score], ...]
        mock_instance.return_value = ([
            [None, "Texto extraído via OCR", 0.99]
        ], 0.1)
        mock_cls.return_value = mock_instance
        yield mock_instance


def test_rapidocr_adapter_image(mock_rapidocr, tmp_path):
    # Test image parsing
    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"dummy image bytes")

    adapter = RapidOcrAdapter()
    text = adapter.parse(str(img_file))

    assert text == "Texto extraído via OCR"
    mock_rapidocr.assert_called_once_with(str(img_file))


def test_rapidocr_adapter_pdf(mock_rapidocr, tmp_path):
    # Mock pymupdf (fitz)
    fitz_mock = MagicMock()
    doc_mock = MagicMock()
    page_mock = MagicMock()
    pix_mock = MagicMock()
    
    # Setup mocks
    pix_mock.samples = b"\x00\x00\x00" * 100
    pix_mock.w = 10
    pix_mock.h = 10
    pix_mock.n = 3
    page_mock.get_pixmap.return_value = pix_mock
    doc_mock.__iter__.return_value = [page_mock]
    fitz_mock.open.return_value = doc_mock

    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"dummy pdf bytes")

    with patch("fitz.open", fitz_mock.open):
        adapter = RapidOcrAdapter()
        text = adapter.parse(str(pdf_file))
        
    assert text == "Texto extraído via OCR"
    mock_rapidocr.assert_called_once()


def test_autoparser_wrapper_triggers_fallback(mock_rapidocr, tmp_path):
    # Create a mock primary parser that returns short text
    primary_mock = MagicMock()
    primary_mock.parse.return_value = "short"  # < 100 chars
    
    # Setup fitz mock for OCR fallback
    fitz_mock = MagicMock()
    doc_mock = MagicMock()
    page_mock = MagicMock()
    pix_mock = MagicMock()
    pix_mock.samples = b"\x00\x00\x00" * 10
    pix_mock.w = 2
    pix_mock.h = 5
    pix_mock.n = 3
    page_mock.get_pixmap.return_value = pix_mock
    doc_mock.__iter__.return_value = [page_mock]
    fitz_mock.open.return_value = doc_mock

    pdf_file = tmp_path / "scanned_doc.pdf"
    pdf_file.write_bytes(b"dummy pdf bytes")

    adapter_ocr = RapidOcrAdapter()
    wrapper = AutoParserWrapper(primary=primary_mock, fallback_ocr=adapter_ocr)

    with patch("fitz.open", fitz_mock.open):
        text = wrapper.parse(str(pdf_file))

    assert text == "Texto extraído via OCR"
    primary_mock.parse.assert_called_once_with(str(pdf_file), "")
