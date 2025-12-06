from django.urls import path
from . import views

app_name = 'scheduling'

urlpatterns = [
    path('preferences/', views.preferences, name='preferences'),
    path('my-schedule/', views.my_schedule, name='my_schedule'),
    path('generator/', views.generator, name='generator'),
]
