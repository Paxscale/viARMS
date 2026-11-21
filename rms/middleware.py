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
            owner_profile = getattr(owner, 'profile', None)
            if owner_profile and not owner_profile.is_approved:
                logout(request)
                messages.error(request, "Your account is pending administrator approval.")
                return redirect('login')

            # Apply subscription suspension rules; tenant data is retained while suspended.
            from .views import check_and_process_tenant_lifecycle
            check_and_process_tenant_lifecycle(owner)

        return self.get_response(request)
