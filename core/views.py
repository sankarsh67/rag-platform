from django.db import transaction
from rest_framework import generics, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView as SimpleJWTTokenRefreshView

from core.models import ChatMessage, ChatSession, Document, Tenant, User
from core.permissions import IsOwnerOrTenantAdmin, IsTenantAdmin, IsTenantAdminOrReadOnly, IsTenantMember
from core.rag_utils import RAGOrchestrator
from core.serializers import (
    ChatMessageCreateSerializer,
    ChatMessageSerializer,
    ChatSessionSerializer,
    DocumentSerializer,
    DocumentUploadSerializer,
    LoginSerializer,
    RegisterSerializer,
    TenantSerializer,
    UserCreateSerializer,
    UserSerializer,
)
from core.tasks import delete_document_vectors, ingest_document

# ---------------------------------------------------------------------------
# Auth — POST /api/auth/register, /login, /refresh
# ---------------------------------------------------------------------------
class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]


class LoginView(generics.CreateAPIView):
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class TokenRefreshView(SimpleJWTTokenRefreshView):
    """Re-exported as-is; SimpleJWT already returns a fresh access token
    (and rotated refresh token, per SIMPLE_JWT.ROTATE_REFRESH_TOKENS)."""

    permission_classes = [AllowAny]


# ---------------------------------------------------------------------------
# Tenants — GET /api/tenant/ (admin sees own tenant; platform-admin patterns
# would extend this, but per the doc's scope each admin manages their tenant)
# ---------------------------------------------------------------------------
class TenantViewSet(viewsets.ModelViewSet):
    serializer_class = TenantSerializer
    permission_classes = [IsAuthenticated, IsTenantAdminOrReadOnly]
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        # Scoped to the caller's own tenant only — no cross-tenant listing.
        return Tenant.objects.filter(id=self.request.tenant_id)


# ---------------------------------------------------------------------------
# Users — POST/GET /api/users/ (admin-managed, tenant-scoped)
# ---------------------------------------------------------------------------
class UserViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsTenantMember]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return User.objects.for_tenant(self.request.tenant_id)

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        return UserSerializer

    def get_permissions(self):
        # Only admins can create/modify/delete users; any tenant member can list.
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [IsAuthenticated(), IsTenantAdmin()]
        return [IsAuthenticated(), IsTenantMember()]


# ---------------------------------------------------------------------------
# Documents — upload / list / delete
# ---------------------------------------------------------------------------
class DocumentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsTenantMember]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        return Document.objects.for_tenant(self.request.tenant_id).select_related("uploaded_by")

    def get_serializer_class(self):
        if self.action == "create":
            return DocumentUploadSerializer
        return DocumentSerializer

    @transaction.atomic
    def perform_create(self, serializer):
        document = serializer.save(
            tenant_id=self.request.tenant_id,
            uploaded_by=self.request.user,
            status=Document.STATUS_PROCESSING,
        )
        transaction.on_commit(lambda: ingest_document.delay(str(document.id)))

    def perform_destroy(self, instance):
        tenant_id, document_id = instance.tenant_id, instance.id
        instance.delete()
        delete_document_vectors.delay(str(tenant_id), str(document_id))


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
class ChatSessionViewSet(viewsets.ModelViewSet):
    serializer_class = ChatSessionSerializer
    permission_classes = [IsAuthenticated, IsTenantMember, IsOwnerOrTenantAdmin]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        qs = ChatSession.objects.for_tenant(self.request.tenant_id)
        if self.request.tenant_role != "admin":
            qs = qs.filter(user=self.request.user)
        return qs.select_related("user")

    def perform_create(self, serializer):
        serializer.save(tenant_id=self.request.tenant_id, user=self.request.user)


class ChatMessageListView(generics.ListAPIView):
    """GET /api/chat/{session_id}/ — list messages for a session."""

    serializer_class = ChatMessageSerializer
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        session = self._get_session()
        return session.messages.all()

    def _get_session(self):
        session = ChatSession.objects.for_tenant(self.request.tenant_id).get(
            id=self.kwargs["session_id"]
        )
        if self.request.tenant_role != "admin" and session.user_id != self.request.user.id:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You do not have access to this chat session.")
        return session


class ChatMessageCreateView(APIView):
    """POST /api/chat/{session_id}/message — post a user message, get the
    RAG-generated assistant reply, persist both."""

    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, session_id):
        session = ChatSession.objects.for_tenant(request.tenant_id).get(id=session_id)
        if request.tenant_role != "admin" and session.user_id != request.user.id:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You do not have access to this chat session.")

        serializer = ChatMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_content = serializer.validated_data["content"]

        with transaction.atomic():
            user_message = ChatMessage.objects.create(
                session=session, role=ChatMessage.ROLE_USER, content=user_content
            )

            history = [
                {"role": m.role, "content": m.content}
                for m in session.messages.order_by("created_at").exclude(id=user_message.id)
            ]

            orchestrator = RAGOrchestrator(tenant_id=request.tenant_id)
            result = orchestrator.answer_question(user_content, chat_history=history)

            assistant_message = ChatMessage.objects.create(
                session=session,
                role=ChatMessage.ROLE_ASSISTANT,
                content=result["answer"],
                sources=result["sources"],
            )
            session.save(update_fields=["updated_at"])

        return Response(
            {
                "user_message": ChatMessageSerializer(user_message).data,
                "assistant_message": ChatMessageSerializer(assistant_message).data,
            },
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Admin & Analytics
# ---------------------------------------------------------------------------
class UsageAnalyticsView(APIView):
    """GET /api/admin/usage — basic per-tenant counters."""

    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get(self, request):
        tenant_id = request.tenant_id
        data = {
            "document_count": Document.objects.for_tenant(tenant_id).count(),
            "active_document_count": Document.objects.for_tenant(tenant_id)
            .filter(status=Document.STATUS_ACTIVE)
            .count(),
            "user_count": User.objects.for_tenant(tenant_id).count(),
            "chat_session_count": ChatSession.objects.for_tenant(tenant_id).count(),
        }
        return Response(data)


class DocumentStatsView(APIView):
    """GET /api/admin/document-stats — breakdown by status."""

    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get(self, request):
        tenant_id = request.tenant_id
        qs = Document.objects.for_tenant(tenant_id)
        data = {
            choice_value: qs.filter(status=choice_value).count()
            for choice_value, _label in Document.STATUS_CHOICES
        }
        return Response(data)
