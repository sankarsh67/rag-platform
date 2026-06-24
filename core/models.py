import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models

from core.managers import TenantManager, TenantScopedModel, TenantUserManager


class Tenant(models.Model):
    """An organization using the platform. The root of all data isolation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def vector_collection_name(self):
        """Per-tenant vector collection name, e.g. tenant_<uuid>."""
        return f"tenant_{self.id}"


class User(AbstractUser):
    """Custom user model. Every user belongs to exactly one tenant."""

    ROLE_ADMIN = "admin"
    ROLE_MEMBER = "member"
    ROLE_CHOICES = [
        (ROLE_ADMIN, "Admin"),
        (ROLE_MEMBER, "Member"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="users", null=True, blank=True
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_MEMBER)

    objects = TenantUserManager()

    class Meta:
        ordering = ["username"]
        constraints = [
            # Usernames only need to be unique within a tenant, not globally.
            models.UniqueConstraint(
                fields=["tenant", "username"], name="unique_username_per_tenant"
            )
        ]

    def __str__(self):
        return f"{self.username} ({self.tenant_id})"

    @property
    def is_tenant_admin(self):
        return self.role == self.ROLE_ADMIN


class Document(TenantScopedModel):
    STATUS_PROCESSING = "processing"
    STATUS_ACTIVE = "active"
    STATUS_ARCHIVED = "archived"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PROCESSING, "Processing"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_ARCHIVED, "Archived"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="documents")
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="uploaded_documents"
    )
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to="documents/%Y/%m/%d/")
    version = models.IntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PROCESSING)
    error_message = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "status"])]

    def __str__(self):
        return f"{self.name} v{self.version} ({self.tenant_id})"


class DocumentChunk(TenantScopedModel):
    """Metadata row mirroring a vector stored in Qdrant/Pinecone.

    The actual embedding vector lives in the vector DB; this row keeps the
    relational link back to tenant/document and the raw chunk text so the
    RAG orchestrator can hydrate full context + citations from Postgres
    after a vector similarity search returns embedding_ids.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="document_chunks")
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    chunk_index = models.IntegerField()
    content = models.TextField()
    embedding_id = models.CharField(max_length=255, unique=True)
    page = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document", "chunk_index"]
        indexes = [models.Index(fields=["tenant", "document"])]

    def __str__(self):
        return f"Chunk {self.chunk_index} of {self.document_id}"


class ChatSession(TenantScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="chat_sessions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_sessions")
    title = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["tenant", "user"])]

    def __str__(self):
        return f"Session {self.id} ({self.user_id})"


class ChatMessage(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_CHOICES = [
        (ROLE_USER, "User"),
        (ROLE_ASSISTANT, "Assistant"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    # Source document chunks cited for assistant answers, e.g.
    # [{"document_id": "...", "chunk_id": "...", "page": 4}]
    sources = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"

    @property
    def tenant_id(self):
        # Convenience accessor so chat messages can be tenant-filtered
        # through their parent session without duplicating the FK.
        return self.session.tenant_id