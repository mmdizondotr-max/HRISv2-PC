from django.db import models
from django.conf import settings
from django.utils import timezone

class Shop(models.Model):
    name = models.CharField(max_length=100)
    supervisors = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='supervised_shops')
    is_active = models.BooleanField(default=True)
    # Opening/Closing times could be useful for validation, though requirements didn't explicitly demand strict enforcement logic yet.
    # We can add them as fields to display or validate against.

    def __str__(self):
        return self.name

class TimeLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='time_logs')
    shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True)
    date = models.DateField(default=timezone.now)
    time_in = models.TimeField(null=True, blank=True)
    time_out = models.TimeField(null=True, blank=True)

    class Meta:
        unique_together = ('user', 'date') # One log per user per day as per implication of "Time-In button... record it as employee's time-in for the day"
        ordering = ['-date']

    def __str__(self):
        return f"{self.user} - {self.date}"
