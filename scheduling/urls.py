from django.urls import path
from . import views

app_name = 'scheduling'

urlpatterns = [
    path('preferences/', views.preferences, name='preferences'),
    path('my-schedule/', views.my_schedule, name='my_schedule'),
    path('generator/', views.generator, name='generator'),
    path('shift/delete/<int:shift_id>/', views.shift_delete, name='shift_delete'),
    path('shift/add/<int:schedule_id>/<str:date>/<int:shop_id>/<str:role>/', views.shift_add, name='shift_add'),
    path('history/', views.schedule_history_list, name='schedule_history_list'),
    path('history/<int:schedule_id>/', views.schedule_history_detail, name='schedule_history_detail'),
]
