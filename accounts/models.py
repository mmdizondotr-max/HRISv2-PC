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

    applicable_shops = models.ManyToManyField('attendance.Shop', blank=True, related_name='applicable_staff')

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

class PasswordResetRequest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_requests')
    new_username = models.CharField(max_length=150)
    new_password = models.CharField(max_length=128) # Store hashed password
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Reset request for {self.user.username} ({self.created_at})"
