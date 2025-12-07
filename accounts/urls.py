from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

app_name = 'accounts'

urlpatterns = [
    path('register/', views.register, name='register'),
    path('approvals/', views.approvals, name='approvals'),
    path('settings/', views.account_settings, name='account_settings'),
    path('list/', views.account_list, name='account_list'),
    path('promote/<int:user_id>/', views.account_promote, name='account_promote'),
]
