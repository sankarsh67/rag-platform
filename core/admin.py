from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from core.models import ChatMessage, ChatSession, Document, DocumentChunk, Tenant, User


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    search_fields = ("name", "slug")
    list_filter = ("is_active",)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "email", "tenant", "role", "is_active", "is_staff")
    list_filter = ("role", "tenant", "is_active")
    fieldsets = UserAdmin.fieldsets + (
        ("Tenant & Role", {"fields": ("tenant", "role")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Tenant & Role", {"fields": ("tenant", "role")}),
    )


class DocumentChunkInline(admin.TabularInline):
    model = DocumentChunk
    extra = 0
    fields = ("chunk_index", "page", "embedding_id")
    readonly_fields = ("chunk_index", "page", "embedding_id")
    can_delete = False
    show_change_link = False


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "status", "version", "uploaded_by", "created_at")
    list_filter = ("status", "tenant")
    search_fields = ("name",)
    inlines = [DocumentChunkInline]


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "tenant", "chunk_index", "page", "embedding_id")
    list_filter = ("tenant",)
    search_fields = ("embedding_id", "content")


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    fields = ("role", "content", "created_at")
    readonly_fields = ("role", "content", "created_at")
    can_delete = False


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "user", "title", "updated_at")
    list_filter = ("tenant",)
    inlines = [ChatMessageInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("session", "role", "created_at")
    list_filter = ("role",)
