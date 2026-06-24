"""
VectorStoreClient

Abstracts Qdrant/Pinecone behind one interface so the rest of the codebase
(tasks.py, rag_utils.py) never branches on provider. Collection naming
follows the architecture doc exactly: tenant_<tenant_id>.

Real provider calls are stubbed behind `_qdrant_*` / `_pinecone_*` private
methods — wire in real `qdrant-client` / `pinecone-client` calls there once
credentials are available. Swapping providers is a matter of setting
VECTOR_DB_PROVIDER in the environment; no call-site changes needed.
"""
import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


class VectorStoreClient:
    def __init__(self, provider: str | None = None):
        self.provider = provider or settings.VECTOR_DB_PROVIDER
        self._qdrant = None  # lazily constructed, cached per instance
        self._pinecone = None  # lazily constructed, cached per instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def collection_name(self, tenant_id) -> str:
        return f"tenant_{tenant_id}"

    def ensure_collection(self, tenant_id) -> None:
        """Create the tenant's collection if it doesn't already exist."""
        name = self.collection_name(tenant_id)
        if self.provider == "qdrant":
            self._qdrant_ensure_collection(name)
        elif self.provider == "pinecone":
            self._pinecone_ensure_index(name)
        else:
            raise ValueError(f"Unsupported VECTOR_DB_PROVIDER: {self.provider}")

    def upsert(
        self,
        tenant_id,
        vectors: list[list[float]],
        ids: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Upsert embedding vectors into the tenant-scoped collection.

        Args:
            tenant_id: tenant UUID/str — used purely for collection naming.
            vectors: list of embedding vectors (len == len(ids) == len(metadatas)).
            ids: stable vector IDs (matches DocumentChunk.embedding_id).
            metadatas: payload per vector, e.g.
                {"document_id": "...", "chunk_id": "...", "source": "f.pdf", "page": 4}
        """
        assert len(vectors) == len(ids) == len(metadatas), "vectors/ids/metadatas length mismatch"
        name = self.collection_name(tenant_id)
        self.ensure_collection(tenant_id)
        if self.provider == "qdrant":
            self._qdrant_upsert(name, vectors, ids, metadatas)
        elif self.provider == "pinecone":
            self._pinecone_upsert(name, vectors, ids, metadatas)
        else:
            raise ValueError(f"Unsupported VECTOR_DB_PROVIDER: {self.provider}")
        logger.info("Upserted %d vectors into %s", len(vectors), name)

    def search(
        self,
        tenant_id,
        query_vector: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return top_k matches: [{"id": ..., "score": ..., "metadata": {...}}, ...]"""
        name = self.collection_name(tenant_id)
        if self.provider == "qdrant":
            return self._qdrant_search(name, query_vector, top_k, filters)
        elif self.provider == "pinecone":
            return self._pinecone_search(name, query_vector, top_k, filters)
        raise ValueError(f"Unsupported VECTOR_DB_PROVIDER: {self.provider}")

    def delete_by_document(self, tenant_id, document_id) -> None:
        """Delete all vectors belonging to a single document (re-ingestion / deletion)."""
        name = self.collection_name(tenant_id)
        if self.provider == "qdrant":
            self._qdrant_delete_by_filter(name, {"document_id": str(document_id)})
        elif self.provider == "pinecone":
            self._pinecone_delete_by_filter(name, {"document_id": str(document_id)})
        else:
            raise ValueError(f"Unsupported VECTOR_DB_PROVIDER: {self.provider}")

    def delete_collection(self, tenant_id) -> None:
        """Wipe an entire tenant's vector data — used for GDPR-style tenant offboarding."""
        name = self.collection_name(tenant_id)
        if self.provider == "qdrant":
            self._qdrant_delete_collection(name)
        elif self.provider == "pinecone":
            self._pinecone_delete_index(name)
        else:
            raise ValueError(f"Unsupported VECTOR_DB_PROVIDER: {self.provider}")
        logger.info("Deleted vector collection %s", name)

    # ------------------------------------------------------------------
    # Qdrant — real client, current API (qdrant-client >= 1.10)
    #
    # Note: `QdrantClient.search()` was removed in recent qdrant-client
    # releases in favor of the unified `query_points()` method, which
    # covers search/recommend/discover/filter in one call. We use that
    # here rather than the older `.search()` signature you'll see in a lot
    # of older tutorials/blog posts.
    # ------------------------------------------------------------------
    @property
    def qdrant_client(self):
        if self._qdrant is None:
            from qdrant_client import QdrantClient

            self._qdrant = QdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY or None,
            )
        return self._qdrant

    @staticmethod
    def _build_qdrant_filter(filters: dict[str, Any] | None):
        if not filters:
            return None
        from qdrant_client import models as qmodels

        return qmodels.Filter(
            must=[
                qmodels.FieldCondition(key=key, match=qmodels.MatchValue(value=value))
                for key, value in filters.items()
            ]
        )

    def _qdrant_ensure_collection(self, name: str) -> None:
        from qdrant_client import models as qmodels

        client = self.qdrant_client
        if not client.collection_exists(name):
            client.create_collection(
                collection_name=name,
                vectors_config=qmodels.VectorParams(
                    size=settings.VECTOR_EMBEDDING_DIM, distance=qmodels.Distance.COSINE
                ),
            )

    def _qdrant_upsert(self, name, vectors, ids, metadatas) -> None:
        from qdrant_client import models as qmodels

        points = [
            qmodels.PointStruct(id=point_id, vector=vector, payload=metadata)
            for point_id, vector, metadata in zip(ids, vectors, metadatas)
        ]
        self.qdrant_client.upsert(collection_name=name, points=points, wait=True)

    def _qdrant_search(self, name, query_vector, top_k, filters) -> list[dict[str, Any]]:
        client = self.qdrant_client
        if not client.collection_exists(name):
            # Tenant has no ingested documents yet — nothing to search.
            return []

        result = client.query_points(
            collection_name=name,
            query=query_vector,
            limit=top_k,
            query_filter=self._build_qdrant_filter(filters),
            with_payload=True,
        )
        return [
            {"id": str(point.id), "score": point.score, "metadata": point.payload or {}}
            for point in result.points
        ]

    def _qdrant_delete_by_filter(self, name, filters: dict[str, Any]) -> None:
        from qdrant_client import models as qmodels

        client = self.qdrant_client
        if not client.collection_exists(name):
            return
        client.delete(
            collection_name=name,
            points_selector=qmodels.FilterSelector(filter=self._build_qdrant_filter(filters)),
        )

    def _qdrant_delete_collection(self, name: str) -> None:
        client = self.qdrant_client
        if client.collection_exists(name):
            client.delete_collection(name)

    # ------------------------------------------------------------------
    # Pinecone — real client, current SDK (package name is `pinecone`,
    # NOT the deprecated `pinecone-client` — that package is EOL and
    # renamed upstream as of v5.1.0+).
    #
    # Naming note: Pinecone index names must be DNS-compatible (lowercase
    # alphanumeric + hyphens only — no underscores), unlike Qdrant
    # collection names. We sanitize collection_name()'s "tenant_<uuid>"
    # into "tenant-<uuid>" specifically for Pinecone calls.
    #
    # Delete-by-metadata-filter on serverless indexes (the default/modern
    # index type) was only added to Pinecone in 2025 — older guides
    # claiming this is unsupported on serverless are out of date.
    # ------------------------------------------------------------------
    @property
    def pinecone_client(self):
        if self._pinecone is None:
            from pinecone import Pinecone

            self._pinecone = Pinecone(api_key=settings.PINECONE_API_KEY)
        return self._pinecone

    @staticmethod
    def _pinecone_index_name(name: str) -> str:
        return name.replace("_", "-")

    def _pinecone_get_index(self, sanitized_name: str):
        """Returns a connected Index client, or None if the index doesn't exist."""
        pc = self.pinecone_client
        if not pc.has_index(sanitized_name):
            return None
        host = pc.describe_index(sanitized_name).host
        return pc.Index(host=host)

    def _pinecone_ensure_index(self, name: str) -> None:
        import time

        from pinecone import ServerlessSpec

        sanitized = self._pinecone_index_name(name)
        pc = self.pinecone_client
        if pc.has_index(sanitized):
            return

        pc.create_index(
            name=sanitized,
            dimension=settings.VECTOR_EMBEDDING_DIM,
            metric="cosine",
            spec=ServerlessSpec(
                cloud=settings.PINECONE_CLOUD,
                region=settings.PINECONE_REGION,
            ),
        )
        # Serverless index creation is async on Pinecone's side — poll
        # briefly until it's ready before the caller tries to upsert.
        for _ in range(30):
            if pc.describe_index(sanitized).status.get("ready"):
                return
            time.sleep(1)
        logger.warning("Pinecone index %s did not report ready within 30s", sanitized)

    def _pinecone_upsert(self, name, vectors, ids, metadatas) -> None:
        sanitized = self._pinecone_index_name(name)
        index = self._pinecone_get_index(sanitized)
        if index is None:
            raise RuntimeError(f"Pinecone index {sanitized} does not exist after ensure_index")
        index.upsert(vectors=list(zip(ids, vectors, metadatas)))

    def _pinecone_search(self, name, query_vector, top_k, filters) -> list[dict[str, Any]]:
        sanitized = self._pinecone_index_name(name)
        index = self._pinecone_get_index(sanitized)
        if index is None:
            # Tenant has no ingested documents yet — nothing to search.
            return []

        result = index.query(
            vector=query_vector,
            top_k=top_k,
            include_metadata=True,
            filter=filters or None,
        )
        return [
            {"id": str(match.id), "score": match.score, "metadata": match.metadata or {}}
            for match in result.matches
        ]

    def _pinecone_delete_by_filter(self, name, filters: dict[str, Any]) -> None:
        sanitized = self._pinecone_index_name(name)
        index = self._pinecone_get_index(sanitized)
        if index is None:
            return
        index.delete(filter=filters)

    def _pinecone_delete_index(self, name: str) -> None:
        sanitized = self._pinecone_index_name(name)
        pc = self.pinecone_client
        if pc.has_index(sanitized):
            pc.delete_index(sanitized)