"""
Custom JWT authentication that reads the custom `tenant_id` / `role` claims
(see core.serializers.TenantTokenObtainPairSerializer) off the validated
token and attaches them directly to the request object. This is the
enforcement point referenced in the architecture doc as "Tenant ID
enforced at: JWT level".

Views then read `request.tenant_id` to scope all querysets — see
core.permissions.IsTenantMember and the ViewSets in core.views.
"""
from rest_framework_simplejwt.authentication import JWTAuthentication


class TenantJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, validated_token = result

        token_tenant_id = validated_token.get("tenant_id")
        token_role = validated_token.get("role")

        # Defense in depth: never trust a token whose tenant claim doesn't
        # match the user's current tenant in the DB (e.g. user was moved
        # or deactivated after the token was issued).
        if token_tenant_id and str(user.tenant_id) != str(token_tenant_id):
            from rest_framework.exceptions import AuthenticationFailed

            raise AuthenticationFailed("Token tenant does not match user tenant.")

        request.tenant_id = str(user.tenant_id) if user.tenant_id else None
        request.tenant_role = token_role or user.role

        return user, validated_token
