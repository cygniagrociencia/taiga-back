# -*- coding: utf-8 -*-
# Forces authentication on every /api/v1/ endpoint except a small whitelist
# needed for the login flow and initial site bootstrap. Internal-only fork.

from django import http
from django.conf import settings


API_PREFIX = "/api/v1/"

# Paths (relative to API_PREFIX) that must remain accessible to anonymous users.
# Match is by prefix, so "auth" covers "auth", "auth/register", "auth/refresh", etc.
PUBLIC_API_PREFIXES = (
    "auth",
    "users/password_recovery",
    "users/change_password_from_recovery",
    "locales",
    "site",
    "application-tokens/authorize",
)


def _is_public_path(path):
    if not path.startswith(API_PREFIX):
        return True
    rest = path[len(API_PREFIX):]
    for prefix in PUBLIC_API_PREFIXES:
        if rest == prefix or rest.startswith(prefix + "/"):
            return True
    return False


class RequireAuthMiddleware(object):
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "OPTIONS":
            return self.get_response(request)

        if _is_public_path(request.path):
            return self.get_response(request)

        if self._is_authenticated(request):
            return self.get_response(request)

        return http.JsonResponse(
            {"_error_message": "Authentication required", "_error_type": "taiga.base.exceptions.NotAuthenticated"},
            status=401,
        )

    def _is_authenticated(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return True

        from taiga.auth.authentication import JWTAuthentication
        try:
            result = JWTAuthentication().authenticate(request)
        except Exception:
            return False
        if result is None:
            return False
        user, _token = result
        return user is not None and user.is_authenticated
