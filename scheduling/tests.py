from django.test import TestCase
from accounts.models import User
from attendance.models import Shop
from scheduling.models import UserShopScore, Schedule, Shift, Preference
from scheduling.views import _generate_multi_week_schedule
from scheduling.management.commands.update_attendance_scores import Command as UpdateScoreCommand
from attendance.models import TimeLog
from scheduling.utils import ensure_roving_shop_and_assignments
import datetime

class ScheduleAlgorithmTests(TestCase):
    def setUp(self):
        # Create Users
        self.u1 = User.objects.create_user(username='u1', first_name='A', last_name='A', is_active=True, is_approved=True, tier='regular')
        self.u2 = User.objects.create_user(username='u2', first_name='B', last_name='B', is_active=True, is_approved=True, tier='regular')
        self.u3 = User.objects.create_user(username='u3', first_name='C', last_name='C', is_active=True, is_approved=True, tier='regular')
        self.sup = User.objects.create_user(username='sup', first_name='Sup', last_name='S', is_active=True, is_approved=True, tier='supervisor')

        # Create Shops
        self.shop1 = Shop.objects.create(name="Shop 1", is_active=True)
        self.shop2 = Shop.objects.create(name="Shop 2", is_active=True)

        # Ensure Roving Logic works
        ensure_roving_shop_and_assignments()
        self.roving_shop = Shop.objects.get(name="Roving")

        # Shop Requirements
        # Default is 1 Main, 0 Reserve. Let's make Shop 1 need 2 Main.
        from scheduling.models import ShopRequirement
        ShopRequirement.objects.create(shop=self.shop1, required_main_staff=2, required_reserve_staff=0)
        ShopRequirement.objects.create(shop=self.shop2, required_main_staff=1, required_reserve_staff=1)

    def test_roving_assignment_logic(self):
        # Verify assignments were set correctly by ensure_roving_shop_and_assignments
        self.assertTrue(self.roving_shop in self.sup.applicable_shops.all())
        self.assertEqual(self.sup.applicable_shops.count(), 1)

        self.assertFalse(self.roving_shop in self.u1.applicable_shops.all())
        self.assertTrue(self.shop1 in self.u1.applicable_shops.all())

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
