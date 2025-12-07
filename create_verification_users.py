import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hris_project.settings")
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()

if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@example.com', 'password', first_name='Admin', last_name='User')

if not User.objects.filter(username='target').exists():
    u = User.objects.create_user('target', 'target@example.com', 'password', first_name='Target', last_name='User', tier='regular')
    u.is_active = True
    u.save()
