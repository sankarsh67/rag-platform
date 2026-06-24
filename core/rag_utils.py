"""
RAGOrchestrator

Ties together:
  - VectorStoreClient (tenant-scoped retrieval)
  - Gemini LLM (via LangChain, stubbed when no API key is configured)
  - Postgres DocumentChunk rows (to hydrate full chunk text + citations
    from the embedding IDs returned by the vector search)

answer_question(query, chat_history) returns:
    {
        "answer": str,
        "sources": [{"document_id": ..., "chunk_id": ..., "page": ..., "source": ...}],
    }
"""
import logging

from django.conf import settings

from core.models import DocumentChunk
from core.utils import generate_embeddings
from core.vector_db import VectorStoreClient

logger = logging.getLogger(__name__)

RAG_SYSTEM_PROMPT = (
    "You are an enterprise knowledge assistant. Answer the user's question "
    "using ONLY the provided context. If the answer isn't in the context, "
    "say you don't have enough information. Cite sources by document name."
)


class RAGOrchestrator:
    def __init__(self, tenant_id, top_k: int | None = None):
        self.tenant_id = tenant_id
        self.top_k = top_k or settings.RAG_TOP_K
        self.vector_store = VectorStoreClient()
        self._llm = None

    # ------------------------------------------------------------------
    @property
    def llm(self):
        """Lazily construct the Gemini chat model via LangChain.

        Falls back to a stub callable when GEMINI_API_KEY isn't configured,
        so the rest of the pipeline (retrieval, citation assembly, chat
        persistence) is testable without live credentials.
        """
        if self._llm is not None:
            return self._llm

        if not settings.GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY not set — using stub LLM.")
            self._llm = _StubChatModel()
            return self._llm

        from langchain_google_genai import ChatGoogleGenerativeAI

        self._llm = ChatGoogleGenerativeAI(
            model=settings.GEMINI_LLM_MODEL,
            google_api_key=settings.GEMINI_API_KEY,
            temperature=0.2,
        )
        return self._llm

    # ------------------------------------------------------------------
    def _retrieve(self, query: str):
        query_vector = generate_embeddings([query])[0]
        hits = self.vector_store.search(
            tenant_id=self.tenant_id,
            query_vector=query_vector,
            top_k=self.top_k,
        )
        if not hits:
            return [], []

        chunk_ids = [h["id"] for h in hits]
        chunks = {
            str(c.embedding_id): c
            for c in DocumentChunk.objects.for_tenant(self.tenant_id).filter(
                embedding_id__in=chunk_ids
            )
        }

        context_blocks = []
        sources = []
        for hit in hits:
            chunk = chunks.get(str(hit["id"]))
            if not chunk:
                continue
            context_blocks.append(f"[{chunk.document.name}] {chunk.content}")
            sources.append(
                {
                    "document_id": str(chunk.document_id),
                    "chunk_id": str(chunk.id),
                    "source": chunk.document.name,
                    "page": chunk.page,
                    "score": hit.get("score"),
                }
            )
        return context_blocks, sources

    def _format_history(self, chat_history: list[dict]) -> str:
        lines = []
        for turn in chat_history or []:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            lines.append(f"{role.upper()}: {content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def answer_question(self, query: str, chat_history: list[dict] | None = None) -> dict:
        context_blocks, sources = self._retrieve(query)
        context_text = "\n\n".join(context_blocks) if context_blocks else "(no relevant context found)"
        history_text = self._format_history(chat_history)

        prompt = (
            f"{RAG_SYSTEM_PROMPT}\n\n"
            f"Conversation so far:\n{history_text}\n\n"
            f"Context:\n{context_text}\n\n"
            f"Question: {query}\n"
            f"Answer:"
        )

        response = self.llm.invoke(prompt)
        answer = getattr(response, "content", None) or str(response)

        if not context_blocks:
            sources = []

        return {"answer": answer, "sources": sources}


class _StubChatModel:
    """Deterministic placeholder so the RAG flow can run end-to-end
    without a live Gemini key — useful for local dev and tests."""

    def invoke(self, prompt: str):
        class _Resp:
            content = (
                "[stub response — configure GEMINI_API_KEY for real answers] "
                "Based on the retrieved context, here is a placeholder answer."
            )

        return _Resp()
