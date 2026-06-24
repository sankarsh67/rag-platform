"""
Tenant-aware QuerySet / Manager pattern.

Every tenant-scoped model uses TenantManager so that, when given an
active tenant (via `for_tenant(tenant_id)`), all lookups are automatically
filtered. This is the ORM-level half of tenant isolation described in the
architecture doc (the other halves being JWT claims and per-tenant vector
collections).

Usage:
    class Document(TenantScopedModel):
        ...

    Document.objects.for_tenant(tenant_id).filter(status="active")
"""
from django.contrib.auth.models import UserManager
from django.db import models


class TenantQuerySet(models.QuerySet):
    def for_tenant(self, tenant_id):
        if tenant_id is None:
            # Defensive default: a missing tenant context should never
            # silently return cross-tenant data.
            return self.none()
        return self.filter(tenant_id=tenant_id)


class TenantManager(models.Manager):
    """Default manager for tenant-scoped models.

    NOTE: This manager does NOT filter by tenant automatically on plain
    `.objects.all()` calls — that's intentional, since some contexts
    (Celery tasks, admin, cross-tenant superuser views) legitimately need
    unscoped access. Views/serializers MUST explicitly call
    `.for_tenant(request.tenant_id)` to enforce isolation. See
    core.permissions and core.views for where this is enforced.
    """

    def get_queryset(self):
        return TenantQuerySet(self.model, using=self._db)

    def for_tenant(self, tenant_id):
        return self.get_queryset().for_tenant(tenant_id)


class TenantUserManager(UserManager.from_queryset(TenantQuerySet)):
    """User-specific manager: keeps create_user/create_superuser from
    Django's UserManager (needed for password hashing, is_staff defaults,
    etc.) while adding the same `.for_tenant(tenant_id)` filtering used
    everywhere else."""

    pass


class TenantScopedModel(models.Model):
    """Abstract base for any model that carries a `tenant` FK.

    Subclasses must still declare:
        tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE)

    This base only wires up the manager; Django doesn't let abstract
    base classes declare the FK once-and-shared sensibly across models
    with different related_names, so each concrete model declares its own.
    """

    objects = TenantManager()

    class Meta:
        abstract = True
