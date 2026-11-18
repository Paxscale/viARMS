"""
URL configuration for viARMS project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('sw.js', TemplateView.as_view(template_name='sw.js', content_type='application/javascript'), name='service_worker'),
    path('manifest.json', TemplateView.as_view(template_name='manifest.json', content_type='application/json'), name='manifest_json'),
    path('', include('rms.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
