from django.db import models
from django.conf import settings
from django.utils import timezone

class Shop(models.Model):
    name = models.CharField(max_length=100)
    supervisors = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='supervised_shops')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class ShopOperatingHours(models.Model):
    DAY_CHOICES = [
        (0, 'Monday'),
        (1, 'Tuesday'),
        (2, 'Wednesday'),
        (3, 'Thursday'),
        (4, 'Friday'),
        (5, 'Saturday'),
        (6, 'Sunday'),
    ]
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='operating_hours')
    day = models.IntegerField(choices=DAY_CHOICES)
    open_time = models.TimeField()
    close_time = models.TimeField()

    class Meta:
        unique_together = ('shop', 'day')
        ordering = ['day']

class TimeLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='time_logs')
    shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True)
    date = models.DateField(default=timezone.now)
    time_in = models.TimeField(null=True, blank=True)
    time_out = models.TimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, null=True, help_text="Stores manual override history and remarks.")

    class Meta:
        unique_together = ('user', 'date') # One log per user per day as per implication of "Time-In button... record it as employee's time-in for the day"
        ordering = ['-date']

    def __str__(self):
        return f"{self.user} - {self.date}"
