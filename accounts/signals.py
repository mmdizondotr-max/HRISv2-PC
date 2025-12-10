from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from .models import User

@receiver(user_logged_in)
def promote_superuser_to_admin(sender, user, request, **kwargs):
    """
    Automatically promotes a superuser to Administrator tier upon login.
    """
    if user.is_superuser and user.tier != 'administrator':
        user.tier = 'administrator'
        user.save()
