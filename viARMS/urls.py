"""
URL configuration for viARMS project.
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include, reverse_lazy
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView
from rms import views as rms_views
from rms.forms import RequestHostPasswordResetForm

urlpatterns = [
    path('admin/', admin.site.urls),
    path('password-reset/', auth_views.PasswordResetView.as_view(
        form_class=RequestHostPasswordResetForm,
        template_name='registration/password_reset_form.html',
        email_template_name='registration/password_reset_email.txt',
        subject_template_name='registration/password_reset_subject.txt',
        success_url=reverse_lazy('password_reset_done'),
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='registration/password_reset_done.html',
    ), name='password_reset_done'),
    path('password-reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='registration/password_reset_confirm.html',
        success_url=reverse_lazy('password_reset_complete'),
    ), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='registration/password_reset_complete.html',
    ), name='password_reset_complete'),
    path('accounts/inactive/', rms_views.account_inactive_view, name='account_inactive'),
    path('accounts/', include('allauth.urls')),
    path('sw.js', TemplateView.as_view(template_name='sw.js', content_type='application/javascript'), name='service_worker'),
    path('manifest.json', TemplateView.as_view(template_name='manifest.json', content_type='application/json'), name='manifest_json'),
    path('', include('rms.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
