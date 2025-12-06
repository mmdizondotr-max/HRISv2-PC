from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    TIER_CHOICES = (
        ('regular', 'Regular'),
        ('supervisor', 'Supervisor'),
        ('administrator', 'Administrator'),
    )

    tier = models.CharField(max_length=20, choices=TIER_CHOICES, default='regular')
    photo_id = models.ImageField(upload_to='ids/', blank=True, null=True)
    is_approved = models.BooleanField(default=False) # For registration approval

    class Meta:
        unique_together = ('first_name', 'last_name')
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.username})"
