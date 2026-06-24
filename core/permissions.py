"""
RBAC + tenant isolation permission classes.

Role permission matrix (from the architecture doc):
    Admin  -> manage users, documents, analytics
    Member -> upload, chat, retrieve
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsTenantMember(BasePermission):
    """Base permission: request must carry a resolved tenant_id.

    Any authenticated user without a tenant (e.g. a mid-registration
    account) is rejected outright — there is no valid "no tenant" state
    for accessing tenant-scoped resources.
    """

    message = "Authenticated user has no associated tenant."

    def has_permission(self, request, view):
        return bool(getattr(request, "tenant_id", None))

    def has_object_permission(self, request, view, obj):
        obj_tenant_id = getattr(obj, "tenant_id", None)
        return obj_tenant_id is not None and str(obj_tenant_id) == str(request.tenant_id)


class IsTenantAdmin(IsTenantMember):
    """Admin-only actions: tenant management, user management, analytics."""

    message = "This action requires the 'admin' role within your tenant."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.tenant_role == "admin"


class IsTenantAdminOrReadOnly(IsTenantMember):
    """Members can read; only admins can write."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.tenant_role == "admin"


class IsOwnerOrTenantAdmin(IsTenantMember):
    """Used for chat sessions/messages: owner can access their own; admins
    can access anything within the tenant (e.g. for support/audit)."""

    def has_object_permission(self, request, view, obj):
        if not super().has_object_permission(request, view, obj):
            return False
        owner_id = getattr(obj, "user_id", None)
        return request.tenant_role == "admin" or str(owner_id) == str(request.user.id)
