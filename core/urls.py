from django.urls import path
from rest_framework.routers import DefaultRouter

from core.views import (
    ChatMessageCreateView,
    ChatMessageListView,
    ChatSessionViewSet,
    DocumentStatsView,
    DocumentViewSet,
    LoginView,
    RegisterView,
    TenantViewSet,
    TokenRefreshView,
    UsageAnalyticsView,
    UserViewSet,
)

router = DefaultRouter()
router.register(r"tenant", TenantViewSet, basename="tenant")
router.register(r"users", UserViewSet, basename="user")
router.register(r"documents", DocumentViewSet, basename="document")
router.register(r"chat/sessions", ChatSessionViewSet, basename="chat-session")

urlpatterns = [
    # Auth
    path("auth/register", RegisterView.as_view(), name="auth-register"),
    path("auth/login", LoginView.as_view(), name="auth-login"),
    path("auth/refresh", TokenRefreshView.as_view(), name="auth-refresh"),
    # Documents — explicit alias matching the architecture doc's
    # POST /api/documents/upload (the router below also exposes the same
    # create action at POST /api/documents/)
    path(
        "documents/upload",
        DocumentViewSet.as_view({"post": "create"}),
        name="document-upload",
    ),
    # Chat (custom, non-router routes per the architecture doc's API surface)
    path("chat/<uuid:session_id>/message", ChatMessageCreateView.as_view(), name="chat-message-create"),
    path("chat/<uuid:session_id>", ChatMessageListView.as_view(), name="chat-message-list"),
    # Admin & analytics
    path("admin/usage", UsageAnalyticsView.as_view(), name="admin-usage"),
    path("admin/document-stats", DocumentStatsView.as_view(), name="admin-document-stats"),
] + router.urls
