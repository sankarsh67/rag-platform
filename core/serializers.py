from django.contrib.auth import authenticate
from django.db import transaction
from django.utils.text import slugify
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import ChatMessage, ChatSession, Document, Tenant, User


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class TenantTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Issues JWTs carrying the custom claims shown in the architecture doc:
    sub, tenant_id, role, exp.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["tenant_id"] = str(user.tenant_id) if user.tenant_id else None
        token["role"] = user.role
        return token


class RegisterSerializer(serializers.Serializer):
    """Registers a brand-new tenant + its first admin user in one call.

    Subsequent users for that tenant are created via the admin-only
    UserViewSet (Phase 2), not this endpoint.
    """

    tenant_name = serializers.CharField(max_length=255)
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_username(self, value):
        return value

    @transaction.atomic
    def create(self, validated_data):
        tenant_name = validated_data["tenant_name"]
        base_slug = slugify(tenant_name) or "tenant"
        slug = base_slug
        suffix = 1
        while Tenant.objects.filter(slug=slug).exists():
            suffix += 1
            slug = f"{base_slug}-{suffix}"

        tenant = Tenant.objects.create(name=tenant_name, slug=slug)
        user = User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            password=validated_data["password"],
            tenant=tenant,
            role=User.ROLE_ADMIN,
        )
        return user

    def to_representation(self, instance):
        refresh = RefreshToken.for_user(instance)
        refresh["tenant_id"] = str(instance.tenant_id)
        refresh["role"] = instance.role
        return {
            "user": UserSerializer(instance).data,
            "tenant": TenantSerializer(instance.tenant).data,
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(username=attrs["username"], password=attrs["password"])
        if not user:
            raise serializers.ValidationError("Invalid credentials.")
        if not user.is_active :
            raise serializers.ValidationError("This account/tenant is inactive.")
        attrs["user"] = user
        return attrs

    def to_representation(self, instance):
        user = instance["user"]
        token = TenantTokenObtainPairSerializer.get_token(user)
        return {
            "user": UserSerializer(user).data,
            "refresh": str(token),
            "access": str(token.access_token),
        }


# ---------------------------------------------------------------------------
# Tenant / User
# ---------------------------------------------------------------------------
class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ["id", "name", "slug", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "role", "tenant", "is_active", "date_joined"]
        read_only_fields = ["id", "tenant", "date_joined"]


class UserCreateSerializer(serializers.ModelSerializer):
    """Admin-only: create a new member/admin user inside their own tenant."""

    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["id", "username", "email", "password", "role"]
        read_only_fields = ["id"]

    def create(self, validated_data):
        request = self.context["request"]
        return User.objects.create_user(
            tenant_id=request.tenant_id,
            **validated_data,
        )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = [
            "id",
            "name",
            "file",
            "version",
            "status",
            "error_message",
            "metadata",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "error_message", "uploaded_by", "created_at", "updated_at"]


class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "name", "file", "metadata"]
        read_only_fields = ["id"]


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ["id", "session", "role", "content", "sources", "created_at"]
        read_only_fields = ["id", "session", "role", "sources", "created_at"]


class ChatMessageCreateSerializer(serializers.Serializer):
    """Input for posting a new user message — role/content only.
    The assistant's reply is generated server-side via RAGOrchestrator.
    """

    content = serializers.CharField()


class ChatSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = ["id", "title", "user", "created_at", "updated_at"]
        read_only_fields = ["id", "user", "created_at", "updated_at"]
