from django.core.management.base import BaseCommand
from django.utils import timezone
import datetime
from scheduling.models import Schedule, Preference, UserPriority, Shift
from attendance.models import Shop
from scheduling.views import _generate_schedule, _publish_schedule

class Command(BaseCommand):
    help = 'Automatically generates and publishes schedule if not done by Sunday 12AM'

    def handle(self, *args, **options):
        # This command is intended to be run by a cron job, likely at Sunday 00:01

        today = timezone.localdate()
        # Ensure it is Sunday (weekday = 6)
        if today.weekday() != 6:
            self.stdout.write(self.style.WARNING("It is not Sunday. Skipping auto-generation."))
            return

        target_start = today # Sunday is the start

        # Check if schedule exists
        try:
            schedule = Schedule.objects.get(week_start_date=target_start)
            if schedule.is_published:
                self.stdout.write(self.style.SUCCESS("Schedule already published."))
                return
        except Schedule.DoesNotExist:
            pass

        self.stdout.write("Generating schedule...")
        shops = Shop.objects.filter(is_active=True)
        # Reuse logic from views (refactor if needed, but import works for now)
        _generate_schedule(shops)

        # Publish
        schedule = Schedule.objects.get(week_start_date=target_start)
        schedule.is_published = True
        schedule.save()

        self.stdout.write(self.style.SUCCESS(f"Successfully auto-generated and published schedule for {target_start}"))
