"""
Document processing utilities used by the Celery ingestion task.

extract_text_from_pdf -> PyPDF / Unstructured stub
chunk_text            -> LangChain's RecursiveCharacterTextSplitter
generate_embeddings   -> Gemini embeddings stub (via LangChain)
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str) -> str:
    """Extract raw text from a PDF file path.

    Preferred: pypdf for clean, text-based PDFs.
    Fallback: 'unstructured' for scanned/complex layouts (tables, multi-column).
    Swap the implementation below for real parsing once dependencies are
    installed in the worker image.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        pages_text = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(pages_text).strip()
        if text:
            return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("pypdf extraction failed for %s: %s", file_path, exc)

    # Fallback path — Unstructured handles messier documents (scanned PDFs,
    # multi-column layouts) that pypdf can't parse cleanly.
    try:
        from unstructured.partition.pdf import partition_pdf

        elements = partition_pdf(filename=file_path)
        return "\n\n".join(str(el) for el in elements)
    except Exception as exc:  # noqa: BLE001
        logger.error("unstructured extraction failed for %s: %s", file_path, exc)
        raise


def chunk_text(text: str, chunk_size: int | None = None, chunk_overlap: int | None = None) -> list[str]:
    """Split text into overlapping chunks using LangChain's
    RecursiveCharacterTextSplitter, configured from settings.CHUNK_SIZE /
    settings.CHUNK_OVERLAP.

    Import note: recent LangChain releases moved text splitters out of
    `langchain.text_splitter` into the standalone `langchain-text-splitters`
    package (`langchain_text_splitters`). We try the new location first and
    fall back to the old one for older pinned versions.
    """
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        from langchain.text_splitter import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size or settings.CHUNK_SIZE,
        chunk_overlap=chunk_overlap or settings.CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_text(text)


def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate embedding vectors for a batch of text chunks via Gemini.

    Stubbed to return deterministic placeholder vectors so the ingestion
    pipeline is runnable end-to-end without a real API key. Replace the
    body with a real LangChain GoogleGenerativeAIEmbeddings call:

        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        embedder = GoogleGenerativeAIEmbeddings(
            model=settings.GEMINI_EMBEDDING_MODEL,
            google_api_key=settings.GEMINI_API_KEY,
        )
        return embedder.embed_documents(texts)
    """
    if not settings.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — returning stub embeddings.")
        dim = settings.VECTOR_EMBEDDING_DIM
        return [[0.0] * dim for _ in texts]

    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    embedder = GoogleGenerativeAIEmbeddings(
        model=settings.GEMINI_EMBEDDING_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        output_dimensionality=settings.VECTOR_EMBEDDING_DIM,
    )
    return embedder.embed_documents(texts)