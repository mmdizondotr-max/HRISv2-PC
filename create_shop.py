import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hris_project.settings")
django.setup()
from attendance.models import Shop
if not Shop.objects.exists():
    Shop.objects.create(name="Test Shop")
