from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include('accounts.urls')),
    path("attendance/", include('attendance.urls')),
    path("scheduling/", include('scheduling.urls')),

    # Root redirect to login
    path('', auth_views.LoginView.as_view(template_name='accounts/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='/'), name='logout'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
