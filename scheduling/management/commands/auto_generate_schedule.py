from django.core.management.base import BaseCommand
from django.utils import timezone
import datetime
from scheduling.models import Schedule
from attendance.models import Shop
from scheduling.views import _generate_schedule

class Command(BaseCommand):
    help = 'Automatically generates and publishes schedule if not done by Sunday 12AM'

    def handle(self, *args, **options):
        # This command is intended to be run by a cron job, likely at Sunday 00:01

        today = timezone.localdate()
        # Ensure it is Sunday (weekday = 6)
        if today.weekday() != 6:
            self.stdout.write(self.style.WARNING("It is not Sunday. Skipping auto-generation."))
            return

        # Target next week.
        # Requirement: "Week starts with Monday and ends with Sunday"
        # Requirement: "auto-generated... by Sunday 12AM" (24 hours before week start)
        # If Today is Sunday (6). Next Week starts Tomorrow (Monday).
        # Target = Today + 1

        target_start = today + datetime.timedelta(days=1)

        # Check if schedule exists
        schedule, created = Schedule.objects.get_or_create(week_start_date=target_start)

        if schedule.shifts.exists():
            # Schedule has content, meaning someone generated it.
            if not schedule.is_published:
                self.stdout.write("Draft schedule exists. Auto-publishing it...")
                schedule.is_published = True
                schedule.save()
            else:
                self.stdout.write(self.style.SUCCESS("Schedule already published."))
            return

        # If no shifts, it means it's empty (just created or empty). Generate it.
        self.stdout.write("Generating schedule...")
        shops = Shop.objects.filter(is_active=True)

        # Call the logic from views. Note: _generate_schedule signature changed to (shops, schedule)
        _generate_schedule(shops, schedule)

        # Publish
        schedule.is_published = True
        schedule.save()

        self.stdout.write(self.style.SUCCESS(f"Successfully auto-generated and published schedule for {target_start}"))
