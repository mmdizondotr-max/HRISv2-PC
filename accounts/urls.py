from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

app_name = 'accounts'

urlpatterns = [
    path('register/', views.register, name='register'),
    path('approvals/', views.approvals, name='approvals'),
    # Forgot password flows would go here (using standard Django auth views or custom if needed)
]
