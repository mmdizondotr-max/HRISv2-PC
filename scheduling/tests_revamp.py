from django.test import TestCase
from scheduling.utils import calculate_assignment_score, CurrentWeekAssignments
from scheduling.models import Shift, Schedule, Preference, ShopRequirement
from attendance.models import Shop, TimeLog
from accounts.models import User
import datetime
from django.utils import timezone

class ScheduleRevampTests(TestCase):
    def setUp(self):
        self.shop1 = Shop.objects.create(name="Shop 1", is_active=True)
        self.shop2 = Shop.objects.create(name="Shop 2", is_active=True)
        self.roving = Shop.objects.create(name="Roving", is_active=True)

        self.user1 = User.objects.create(username="user1", first_name="User", last_name="One", tier='regular', is_approved=True)
        self.user2 = User.objects.create(username="user2", first_name="User", last_name="Two", tier='regular', is_approved=True)

        self.user1.applicable_shops.add(self.shop1, self.shop2)
        self.user2.applicable_shops.add(self.shop1, self.shop2)

        ShopRequirement.objects.create(shop=self.shop1, required_main_staff=1)

        self.today = datetime.date(2023, 10, 23) # A Monday

    def test_score_calculation(self):
        # Setup History
        # User 1 worked in Shop 1 last week (Monday)
        last_week_monday = self.today - datetime.timedelta(days=7)
        TimeLog.objects.create(user=self.user1, shop=self.shop1, date=last_week_monday, time_in=datetime.time(9,0))

        # User 2 worked nowhere last week

        history_data = {
            'prev_week_logs': list(TimeLog.objects.filter(date__gte=last_week_monday)),
            'past_3_weeks_logs': [],
            'prev_week_shifts': []
        }

        current_assignments = CurrentWeekAssignments()

        # Calculate Score for User 1 on Shop 1 Today
        # Base: 20
        # -1 (Prev week same shop)
        # -1 (Prev week any shop)
        # Score = 18
        score1 = calculate_assignment_score(self.user1, self.shop1, self.today, history_data, current_assignments)
        self.assertEqual(score1, 18.0)

        # Calculate Score for User 2 on Shop 1 Today
        # Base: 20
        # Score = 20
        score2 = calculate_assignment_score(self.user2, self.shop1, self.today, history_data, current_assignments)
        self.assertEqual(score2, 20.0)

    def test_current_week_deductions(self):
        # User 1 assigned once already
        current_assignments = CurrentWeekAssignments()
        current_assignments.add_assignment(self.user1.id, self.shop2.id, self.today)

        history_data = {
            'prev_week_logs': [],
            'past_3_weeks_logs': [],
            'prev_week_shifts': []
        }

        # Evaluate for Tuesday (different day)
        tuesday = self.today + datetime.timedelta(days=1)

        # Base 20
        # -2 (Duty count in current week is 1)
        # Score = 18
        score = calculate_assignment_score(self.user1, self.shop1, tuesday, history_data, current_assignments)
        self.assertEqual(score, 18.0)

    def test_same_shop_bonus_removed(self):
        # User 1 assigned to Shop 1 on Monday
        current_assignments = CurrentWeekAssignments()
        current_assignments.add_assignment(self.user1.id, self.shop1.id, self.today)

        history_data = {
            'prev_week_logs': [],
            'past_3_weeks_logs': [],
            'prev_week_shifts': []
        }

        # Evaluate for Tuesday on Shop 1
        tuesday = self.today + datetime.timedelta(days=1)

        # Base 20
        # -2 (Duty count 1)
        # +0 (Assigned to same shop current week - REMOVED)
        # Score = 18
        score = calculate_assignment_score(self.user1, self.shop1, tuesday, history_data, current_assignments)
        self.assertEqual(score, 18.0)

    def test_absent_bonus(self):
         # User 1 was absent last week (Shift exists, no Log)
        last_week_monday = self.today - datetime.timedelta(days=7)
        sch = Schedule.objects.create(week_start_date=last_week_monday)
        Shift.objects.create(schedule=sch, user=self.user1, shop=self.shop1, date=last_week_monday, role='main')

        history_data = {
            'prev_week_logs': [],
            'past_3_weeks_logs': [],
            'prev_week_shifts': list(Shift.objects.all())
        }

        current_assignments = CurrentWeekAssignments()

        # Base 20
        # +4 (Absent)
        # Score = 24
        score = calculate_assignment_score(self.user1, self.shop1, self.today, history_data, current_assignments)
        self.assertEqual(score, 24.0)
