from django.core.management.base import BaseCommand
from django.utils import timezone
import datetime
from scheduling.models import Shift, UserShopScore
from attendance.models import TimeLog

class Command(BaseCommand):
    help = 'Updates user scores based on yesterday\'s attendance'

    def handle(self, *args, **options):
        # We look at yesterday
        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)

        self.stdout.write(f"Processing scores for {yesterday}...")

        # 1. Main Staff who were ABSENT (Scheduled Main, No TimeLog)
        # "absent staff will get a jump in score for all future slots (greatly increase chances of getting assigned)"

        # Get all Main shifts for yesterday
        main_shifts = Shift.objects.filter(date=yesterday, role='main')

        for shift in main_shifts:
            # Check for TimeLog
            # Note: TimeLog is per user per day.
            has_timelog = TimeLog.objects.filter(user=shift.user, date=yesterday).exists()

            if not has_timelog:
                # Absent!
                # Update score for this shop (and others? "for all future slots")
                # Prompt: "absent staff will get a jump in score for all future slots"
                # This implies global increase or all shops increase.
                # Since score is (User, Shop), we increase for ALL shops?
                # "score for all shops of the employee for that day goes lower" (Day off logic)
                # "jump in score for all future slots" implies general availability increase.
                # Let's increase for ALL applicable shops.

                self.stdout.write(f"User {shift.user} was ABSENT (Main) at {shift.shop}.")
                self._adjust_score_all_shops(shift.user, 20.0) # Significant Increase

        # 2. Reserve Staff who WORKED (Scheduled Backup, Has TimeLog)
        # "reserve will get a significant decrease in score for all future slots (greatly decrease chances of getting assigned for a more probable day off)"

        backup_shifts = Shift.objects.filter(date=yesterday, role='backup')

        for shift in backup_shifts:
            has_timelog = TimeLog.objects.filter(user=shift.user, date=yesterday).exists()

            if has_timelog:
                # Worked!
                self.stdout.write(f"User {shift.user} WORKED (Reserve) at {shift.shop}.")
                self._adjust_score_all_shops(shift.user, -20.0) # Significant Decrease

        # 3. Main Staff who WORKED (Scheduled Main, Has TimeLog)
        # "Whenever an employee is assigned as a main staff... its score decreases"
        # Since we use this daily update as the "Rollover" mechanism, we must apply this fatigue here.
        # If we rely only on the generator simulation, the scores won't actually change in the DB.

        for shift in main_shifts:
            has_timelog = TimeLog.objects.filter(user=shift.user, date=yesterday).exists()
            if has_timelog:
                 # Worked Main
                 self.stdout.write(f"User {shift.user} WORKED (Main) at {shift.shop}.")
                 self._adjust_score_all_shops(shift.user, -5.0) # Fatigue Penalty

                 # Also, "lower for all slots of the same shop during the following weeks" (Rotation)
                 # This should be a permanent change to the Shop-Specific score.
                 # "Score goes up for all slots of same shop during SAME week" -> Temporary.
                 # "Score goes lower for all slots of same shop during FOLLOWING weeks" -> Permanent Rotation.
                 self._adjust_score_shop(shift.user, shift.shop, -2.0) # Rotation Penalty

        self.stdout.write(self.style.SUCCESS("Score update complete."))

    def _adjust_score_shop(self, user, shop, amount):
        s, _ = UserShopScore.objects.get_or_create(user=user, shop=shop)
        s.score += amount
        s.save()

    def _adjust_score_all_shops(self, user, amount):
        # Get all shop scores for this user
        # Only existing scores? Or create if missing?
        # Ideally, every applicable shop should have a score.

        # If user has no scores, we might need to initialize them.
        # But usually generation initializes them.

        scores = UserShopScore.objects.filter(user=user)
        if not scores.exists():
            # Initialize for applicable shops
            for shop in user.applicable_shops.all():
                UserShopScore.objects.create(user=user, shop=shop, score=100.0 + amount)
        else:
            for s in scores:
                s.score += amount
                s.save()
