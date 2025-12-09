from django.test import TestCase
from accounts.models import User
from attendance.models import Shop
from scheduling.models import UserShopScore, Schedule, Shift, Preference
from scheduling.views import _generate_multi_week_schedule
from scheduling.management.commands.update_attendance_scores import Command as UpdateScoreCommand
from attendance.models import TimeLog
import datetime

class ScheduleAlgorithmTests(TestCase):
    def setUp(self):
        # Create Users
        self.u1 = User.objects.create_user(username='u1', first_name='A', last_name='A', is_active=True, is_approved=True)
        self.u2 = User.objects.create_user(username='u2', first_name='B', last_name='B', is_active=True, is_approved=True)
        self.u3 = User.objects.create_user(username='u3', first_name='C', last_name='C', is_active=True, is_approved=True)

        # Create Shops
        self.shop1 = Shop.objects.create(name="Shop 1", is_active=True)
        self.shop2 = Shop.objects.create(name="Shop 2", is_active=True)

        # Assign users to shops
        self.u1.applicable_shops.add(self.shop1, self.shop2)
        self.u2.applicable_shops.add(self.shop1, self.shop2)
        self.u3.applicable_shops.add(self.shop1, self.shop2)

        # Shop Requirements
        # Default is 1 Main, 0 Reserve. Let's make Shop 1 need 2 Main.
        from scheduling.models import ShopRequirement
        ShopRequirement.objects.create(shop=self.shop1, required_main_staff=2, required_reserve_staff=0)
        ShopRequirement.objects.create(shop=self.shop2, required_main_staff=1, required_reserve_staff=1)

    def test_score_initialization(self):
        # Run Generation
        start_date = datetime.date.today()
        weeks = [Schedule.objects.create(week_start_date=start_date + datetime.timedelta(days=i*7)) for i in range(4)]

        _generate_multi_week_schedule([self.shop1, self.shop2], weeks)

        # Check scores exist
        self.assertTrue(UserShopScore.objects.filter(user=self.u1, shop=self.shop1).exists())
        self.assertEqual(UserShopScore.objects.get(user=self.u1, shop=self.shop1).score, 100.0)

    def test_generate_assignments(self):
        start_date = datetime.date.today()
        weeks = [Schedule.objects.create(week_start_date=start_date)]
        _generate_multi_week_schedule([self.shop1, self.shop2], weeks)

        # Verify shifts created
        self.assertTrue(Shift.objects.filter(schedule=weeks[0]).exists())

        # Verify Shop 1 has 2 Main per day
        s1_shifts = Shift.objects.filter(schedule=weeks[0], shop=self.shop1, role='main', date=start_date)
        self.assertEqual(s1_shifts.count(), 2)

    def test_reserve_constraint(self):
        # Shop 2 needs 1 Main, 1 Reserve.
        # Ensure the Reserve is NOT one of the Main staff
        start_date = datetime.date.today()
        weeks = [Schedule.objects.create(week_start_date=start_date)]

        _generate_multi_week_schedule([self.shop2], weeks)

        s2_main = Shift.objects.filter(schedule=weeks[0], shop=self.shop2, role='main', date=start_date).first()
        s2_res = Shift.objects.filter(schedule=weeks[0], shop=self.shop2, role='backup', date=start_date).first()

        if s2_main and s2_res:
            self.assertNotEqual(s2_main.user, s2_res.user)
            # Reserve should not be assigned Main ANYWHERE this week?
            # We only scheduled Shop 2.
            pass

    def test_daily_score_update(self):
        # Simulate yesterday
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        # Create Shift
        s = Schedule.objects.create(week_start_date=yesterday)
        Shift.objects.create(schedule=s, user=self.u1, shop=self.shop1, date=yesterday, role='main')

        # Add another user with baseline score to allow normalization to work meaningfully
        UserShopScore.objects.create(user=self.u2, shop=self.shop1, score=100.0)
        # u1 score will be created by the command (starts 100) or we create it here
        UserShopScore.objects.create(user=self.u1, shop=self.shop1, score=100.0)

        # 1. Absent (No TimeLog)
        # u1: 100 -> +20 = 120
        # u2: 100
        # Avg = 110. Delta = 100 - 110 = -10.
        # u1 Final = 120 - 10 = 110.
        # u2 Final = 100 - 10 = 90.

        cmd = UpdateScoreCommand()
        cmd.handle()

        score = UserShopScore.objects.get(user=self.u1, shop=self.shop1).score
        self.assertAlmostEqual(score, 110.0)

        # 2. Worked Main
        # Reset scores for clarity
        UserShopScore.objects.filter(shop=self.shop1).update(score=100.0)

        TimeLog.objects.create(user=self.u1, shop=self.shop1, date=yesterday, time_in=datetime.time(9,0))
        cmd.handle()

        # u1 (Worked): 100 - 5 (Fatigue) - 2 (Rotation) = 93.
        # u2 (No Shift): 100.
        # Avg = (93 + 100) / 2 = 96.5.
        # Delta = 100 - 96.5 = +3.5.
        # u1 Final = 93 + 3.5 = 96.5.
        # u2 Final = 100 + 3.5 = 103.5.

        score = UserShopScore.objects.get(user=self.u1, shop=self.shop1).score
        self.assertAlmostEqual(score, 96.5)
