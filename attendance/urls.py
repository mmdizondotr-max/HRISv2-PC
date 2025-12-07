from django.urls import path
from . import views

app_name = 'attendance'

urlpatterns = [
    path('', views.home, name='home'),
    path('shops/', views.shop_list, name='shop_list'),
    path('shops/create/', views.shop_manage, name='shop_create'),
    path('shops/edit/<int:shop_id>/', views.shop_manage, name='shop_edit'),
    path('shops/delete/<int:shop_id>/', views.shop_delete, name='shop_delete'),
]
