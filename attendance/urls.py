from django.urls import path
from . import views

app_name = 'attendance'

urlpatterns = [
    path('home/', views.home, name='home'),
]
