from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator

class User(AbstractUser):
    TIER_CHOICES = (
        ('regular', 'Regular'),
        ('supervisor', 'Supervisor'),
        ('administrator', 'Administrator'),
    )

    tier = models.CharField(max_length=20, choices=TIER_CHOICES, default='regular')
    photo_id = models.ImageField(upload_to='ids/', blank=True, null=True)
    is_approved = models.BooleanField(default=False) # For registration approval

    nickname = models.CharField(
        max_length=6,
        unique=True,
        blank=True,
        null=True,
        validators=[RegexValidator(r'^[a-zA-Z]*$', 'Only letters are allowed.')]
    )

    class Meta:
        unique_together = ('first_name', 'last_name')
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.username})"

    @property
    def get_short_name_for_schedule(self):
        if self.nickname:
            return self.nickname

        # Default: First Name + First Letter of Last Name
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name[0]}."
        elif self.first_name:
             return self.first_name
        return self.username
