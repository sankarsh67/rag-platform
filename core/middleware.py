"""
TenantMiddleware

DRF authenticates the request and sets request.user *after* Django's own
middleware stack runs, which makes a request-level middleware unreliable
for reading the authenticated user. Instead, this middleware just
initializes safe defaults; the actual tenant_id/role enforcement happens
in core.authentication.JWTAuthentication (which reads custom claims off
the validated token) and in core.permissions / core.managers at the view
layer. Keeping this here gives every view a consistent request.tenant_id
attribute to use, regardless of which layer ultimately set it.
"""


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant_id = None
        request.tenant_role = None
        response = self.get_response(request)
        return response
