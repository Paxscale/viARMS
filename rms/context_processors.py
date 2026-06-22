def user_roles(request):
    """
    Global context processor that injects `is_admin` and `is_manager`
    booleans into every template's context automatically.
    """
    is_admin = False
    is_manager = False
    if request.user.is_authenticated:
        is_admin = request.user.is_superuser or (
            hasattr(request.user, 'profile') and request.user.profile.role == 'ADMIN'
        )
        is_manager = hasattr(request.user, 'profile') and request.user.profile.role == 'MANAGER'
    return {
        'is_admin': is_admin,
        'is_manager': is_manager,
    }
