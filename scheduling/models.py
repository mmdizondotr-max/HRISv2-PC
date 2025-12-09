from django.db import models
from django.conf import settings

class Preference(models.Model):
    DAY_CHOICES = [
        (0, 'Monday'),
        (1, 'Tuesday'),
        (2, 'Wednesday'),
        (3, 'Thursday'),
        (4, 'Friday'),
        (5, 'Saturday'),
        (6, 'Sunday'),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='preference')
    preferred_days_off_count = models.PositiveIntegerField(default=1)
    birthday = models.DateField(null=True, blank=True)
    top_preferred_day_off = models.IntegerField(choices=DAY_CHOICES, default=6) # Sunday default

    def __str__(self):
        return f"{self.user} Preferences"

class Schedule(models.Model):
    week_start_date = models.DateField() # Should typically be a Sunday or Monday
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-week_start_date']

    def __str__(self):
        return f"Schedule for week of {self.week_start_date}"

class ScheduleChangeLog(models.Model):
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='change_logs')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True) # User who made the change
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Change by {self.user} on {self.created_at}"

class Shift(models.Model):
    ROLE_CHOICES = (
        ('main', 'Main Staff'),
        ('backup', 'Backup Staff'),
    )

    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='shifts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    shop = models.ForeignKey('attendance.Shop', on_delete=models.CASCADE)
    date = models.DateField()
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='main')
    score = models.FloatField(null=True, blank=True) # Score at the time of assignment

    class Meta:
        unique_together = ('user', 'date') # User can't be in two places at once
        ordering = ['date', 'shop']

    def __str__(self):
        return f"{self.user} @ {self.shop} on {self.date} ({self.role})"

class UserPriority(models.Model):
    """
    Stores the dynamic priority score for the scheduling algorithm.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='priority_score')
    score = models.FloatField(default=100.0)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user}: {self.score}"

class UserShopScore(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='shop_scores')
    shop = models.ForeignKey('attendance.Shop', on_delete=models.CASCADE, related_name='staff_scores')
    score = models.FloatField(default=100.0)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'shop')

    def __str__(self):
        return f"{self.user} - {self.shop}: {self.score}"

class ShopRequirement(models.Model):
    """
    Stores the min staff requirement per shop.
    """
    shop = models.OneToOneField('attendance.Shop', on_delete=models.CASCADE, related_name='requirement')
    min_staff = models.PositiveIntegerField(default=1) # Keeping for safety, but will use new fields
    required_main_staff = models.PositiveIntegerField(default=1)
    required_reserve_staff = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.shop}: Main {self.required_main_staff}, Reserve {self.required_reserve_staff}"
