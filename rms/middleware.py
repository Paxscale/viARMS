from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect


class TenantAccountStatusMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not user.is_superuser:
            profile = getattr(user, 'profile', None)
            owner = profile.tenant_owner if profile and profile.tenant_owner else user
            if not owner.is_active:
                logout(request)
                messages.error(request, "This business account has been suspended. Contact support for assistance.")
                return redirect('login')

        return self.get_response(request)
