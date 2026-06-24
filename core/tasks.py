import logging
import uuid

from celery import shared_task
from django.db import transaction

from core.models import Document, DocumentChunk
from core.utils import chunk_text, extract_text_from_pdf, generate_embeddings
from core.vector_db import VectorStoreClient

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def ingest_document(self, document_id):
    """
    Orchestrates: extract text -> chunk -> embed -> upsert to vector DB ->
    mark document active. Mirrors the flow in the architecture doc section 7.

    Idempotent-ish: re-running for the same document_id first deletes any
    existing chunks/vectors for that document, so retries / re-ingestion
    don't duplicate data.
    """
    try:
        doc = Document.objects.select_related("tenant").get(id=document_id)
    except Document.DoesNotExist:
        logger.error("ingest_document: Document %s not found", document_id)
        return

    vector_store = VectorStoreClient()

    try:
        # Clear out any prior chunks for this document (covers re-ingestion).
        with transaction.atomic():
            doc.chunks.all().delete()
            vector_store.delete_by_document(doc.tenant_id, doc.id)

        text = extract_text_from_pdf(doc.file.path)
        if not text.strip():
            raise ValueError("No extractable text found in document.")

        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("Chunking produced zero chunks.")

        embeddings = generate_embeddings(chunks)

        chunk_objs = []
        embedding_ids = []
        metadatas = []
        for idx, (chunk_content, _vector) in enumerate(zip(chunks, embeddings)):
            embedding_id = str(uuid.uuid4())
            chunk_objs.append(
                DocumentChunk(
                    tenant_id=doc.tenant_id,
                    document=doc,
                    chunk_index=idx,
                    content=chunk_content,
                    embedding_id=embedding_id,
                )
            )
            embedding_ids.append(embedding_id)
            metadatas.append(
                {
                    "document_id": str(doc.id),
                    "chunk_id": embedding_id,
                    "source": doc.name,
                    "chunk_index": idx,
                }
            )

        with transaction.atomic():
            DocumentChunk.objects.bulk_create(chunk_objs)
            vector_store.upsert(
                tenant_id=doc.tenant_id,
                vectors=embeddings,
                ids=embedding_ids,
                metadatas=metadatas,
            )
            doc.status = Document.STATUS_ACTIVE
            doc.error_message = ""
            doc.save(update_fields=["status", "error_message", "updated_at"])

        logger.info("ingest_document: document %s ingested (%d chunks)", doc.id, len(chunk_objs))

    except Exception as exc:  # noqa: BLE001
        logger.exception("ingest_document failed for %s", document_id)
        doc.status = Document.STATUS_FAILED
        doc.error_message = str(exc)[:2000]
        doc.save(update_fields=["status", "error_message", "updated_at"])
        # Retry transient failures (e.g. embedding API rate limits);
        # max_retries caps this at 3 attempts.
        raise self.retry(exc=exc)


@shared_task
def delete_document_vectors(tenant_id, document_id):
    """Standalone task for document deletion — keeps the delete API fast
    by offloading the vector-store cleanup to the worker."""
    VectorStoreClient().delete_by_document(tenant_id, document_id)


@shared_task
def purge_tenant_data(tenant_id):
    """GDPR-style tenant offboarding: wipe the tenant's entire vector
    collection. Relational data deletion (CASCADE) happens via the Tenant
    model delete in the admin/API layer; this task only handles the
    vector store side, which the ORM can't cascade into."""
    VectorStoreClient().delete_collection(tenant_id)
