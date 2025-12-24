from django.utils import timezone
import datetime
from scheduling.models import Shift, UserShopScore, Preference
from attendance.models import TimeLog
from attendance.models import Shop
from django.db.models import Q

def ensure_roving_shop_and_assignments():
    from accounts.models import User
    roving_shop, created = Shop.objects.get_or_create(name="Roving")
    if created or not roving_shop.is_active:
        roving_shop.is_active = True
        roving_shop.save()

    regular_shops = Shop.objects.filter(is_active=True).exclude(id=roving_shop.id)
    all_users = User.objects.filter(is_active=True)

    for user in all_users:
        # Check if user already has assigned shops. If so, respect manual assignment.
        if user.applicable_shops.exists():
            continue

        target_applicable = set()

        if user.tier == 'supervisor':
            target_applicable.add(roving_shop)
        elif user.tier == 'regular':
            for s in regular_shops:
                target_applicable.add(s)

        user.applicable_shops.set(target_applicable)

def calculate_assignment_score(user, shop, date, history_data, current_week_assignments, min_duty_count_among_eligible=None, use_attendance_history=True):
    """
    Calculates the score for assigning 'user' to 'shop' on 'date' as Duty Staff.
    Returns tuple (score, breakdown_dict)

    history_data:
      - prev_week_logs: QuerySet or List of TimeLog for the previous week
      - past_3_weeks_logs: QuerySet or List of TimeLog for the 3 weeks prior to previous week
      - prev_week_shifts: QuerySet or List of Shift for the previous week

    current_week_assignments:
      - Dictionary or Helper Object containing current week's assignments so far.
        Must support querying:
        - user's duty count
        - user's assignments (shop, date)
    """
    score = 20.0  # a. Base score
    breakdown = {'Base Score': 20.0}

    # Helper to check if log matches shop
    def log_matches_shop(log, shop_id):
        # Handle case where log.shop might be None or ID
        if not log.shop: return False
        return log.shop.id == shop_id

    # b. Deduct 1 point for each day of the previous week that the staff reported (timed-in) to the same shop.
    if use_attendance_history:
        deduction = 0
        for log in history_data['prev_week_logs']:
            if log.user_id == user.id and log_matches_shop(log, shop.id):
                deduction += 1.0
        if deduction > 0:
            score -= deduction
            breakdown['Prev Week Same Shop Attendance'] = -deduction

    # c. Deduct 1 point for each week from the past 3 weeks, not counting the previous, that the staff reported at least once (timed-in) to the same shop.
    if use_attendance_history:
        weeks_worked = set()
        for log in history_data['past_3_weeks_logs']:
            if log.user_id == user.id and log_matches_shop(log, shop.id):
                weeks_worked.add(log.date.isocalendar()[:2])
        deduction = len(weeks_worked) * 1.0
        if deduction > 0:
            score -= deduction
            breakdown['Past 3 Weeks Same Shop Attendance'] = -deduction

    # d. Deduct 1 point for each day of the previous week that the staff timed-in.
    if use_attendance_history:
        deduction = 0
        for log in history_data['prev_week_logs']:
            if log.user_id == user.id:
                deduction += 1.0
        if deduction > 0:
            score -= deduction
            breakdown['Prev Week Attendance (Any Shop)'] = -deduction

    # e. Deduct 2 points for each time the staff was assigned as Duty Staff in the same week.
    current_duty_count = current_week_assignments.get_duty_count(user.id)
    deduction = current_duty_count * 2.0
    if deduction > 0:
        score -= deduction
        breakdown['Current Week Duty Assignments'] = -deduction

    # f. Deduct 1 point if staff's preferred day off matches the current day.
    try:
        pref = user.preference
        if pref.top_preferred_day_off == date.weekday():
            score -= 5.0
            breakdown['Preferred Day Off'] = -5.0
    except Preference.DoesNotExist:
        pass

    # g. Deduct 2 points for each day staff acted as substitute in the past week.
    if use_attendance_history:
        sub_count = 0
        worked_dates = set()
        for log in history_data['prev_week_logs']:
            if log.user_id == user.id:
                worked_dates.add(log.date)

        for shift in history_data['prev_week_shifts']:
            if shift.user_id == user.id and shift.role == 'backup':
                if shift.date in worked_dates:
                    sub_count += 1
        deduction = sub_count * 2.0
        if deduction > 0:
            score -= deduction
            breakdown['Prev Week Substitutions'] = -deduction

    # h. Deduct another 4 points if the staff has been assigned as Duty Staff for the 6th time...
    if current_duty_count >= 6:
        score -= 4.0
        breakdown['6+ Duty Assignments'] = -4.0

    # i. Add 4 points for each day staff was absent in the past week.
    if use_attendance_history:
        absent_count = 0
        worked_dates = set()
        for log in history_data['prev_week_logs']:
            if log.user_id == user.id:
                worked_dates.add(log.date)

        for shift in history_data['prev_week_shifts']:
            if shift.user_id == user.id and shift.role == 'main':
                if shift.date not in worked_dates:
                    absent_count += 1
        addition = absent_count * 4.0
        if addition > 0:
            score += addition
            breakdown['Prev Week Absences'] = addition

    # k. Add 1 point to all user/s with the fewest assigned shift in the current week
    if min_duty_count_among_eligible is not None:
        if current_duty_count == min_duty_count_among_eligible:
            score += 1.0
            breakdown['Fewest Shifts Bonus'] = 1.0

    # +1 if user is assigned to the same shop the previous day within the week
    prev_day_date = date - datetime.timedelta(days=1)
    was_at_same_shop_prev_day = False
    for uid, sid, d in current_week_assignments.assignments:
        if uid == user.id and sid == shop.id and d == prev_day_date:
            was_at_same_shop_prev_day = True
            break
    if was_at_same_shop_prev_day:
        score += 1.0
        breakdown['Consecutive Day Same Shop Bonus'] = 1.0

    # +10.0 if user already has 2 days off in the current week
    days_to_check = []
    current_weekday = date.weekday() # 0=Mon, ...
    for i in range(current_weekday):
        check_date = date - datetime.timedelta(days=(current_weekday - i))
        days_to_check.append(check_date)

    days_off_count = 0
    for d in days_to_check:
        if not current_week_assignments.is_assigned_on_day(user.id, d):
            days_off_count += 1

    if days_off_count >= 2:
        score += 10.0
        breakdown['2+ Days Off Bonus'] = 10.0

    return score, breakdown


class CurrentWeekAssignments:
    def __init__(self):
        self.duty_counts = {} # user_id -> count
        self.shop_counts = {} # user_id -> {shop_id -> count}
        self.assignments = [] # list of (user_id, shop_id, date)

    def add_assignment(self, user_id, shop_id, date):
        self.duty_counts[user_id] = self.duty_counts.get(user_id, 0) + 1

        if user_id not in self.shop_counts:
            self.shop_counts[user_id] = {}
        self.shop_counts[user_id][shop_id] = self.shop_counts[user_id].get(shop_id, 0) + 1

        self.assignments.append((user_id, shop_id, date))

    def get_duty_count(self, user_id):
        return self.duty_counts.get(user_id, 0)

    def get_shop_assignment_count(self, user_id, shop_id):
        if user_id in self.shop_counts:
            return self.shop_counts[user_id].get(shop_id, 0)
        return 0

    def is_assigned_on_day(self, user_id, date):
        for uid, sid, d in self.assignments:
            if uid == user_id and d == date:
                return True
        return False

# Retain existing functions for compatibility
def update_scores_for_date(target_date):
    print(f"Processing scores for {target_date}...")
    main_shifts = Shift.objects.filter(date=target_date, role='main')

    for shift in main_shifts:
        has_timelog = TimeLog.objects.filter(user=shift.user, date=target_date).exists()

        if not has_timelog:
            _adjust_score_all_shops(shift.user, 20.0)

    backup_shifts = Shift.objects.filter(date=target_date, role='backup')

    for shift in backup_shifts:
        has_timelog = TimeLog.objects.filter(user=shift.user, date=target_date).exists()

        if has_timelog:
            _adjust_score_all_shops(shift.user, -20.0)
        else:
            _adjust_score_all_shops(shift.user, 10.0)

    for shift in main_shifts:
        has_timelog = TimeLog.objects.filter(user=shift.user, date=target_date).exists()
        if has_timelog:
                _adjust_score_all_shops(shift.user, -5.0)
                _adjust_score_shop(shift.user, shift.shop, -2.0)

    shops = set(UserShopScore.objects.values_list('shop', flat=True))
    for shop_id in shops:
            scores = UserShopScore.objects.filter(shop_id=shop_id)
            if scores.exists():
                avg = sum(s.score for s in scores) / scores.count()
                delta = 100.0 - avg
                if abs(delta) > 0.01:
                    for s in scores:
                        s.score += delta
                        s.save()

def _adjust_score_shop(user, shop, amount):
    s, _ = UserShopScore.objects.get_or_create(user=user, shop=shop)
    s.score += amount
    s.save()

def _adjust_score_all_shops(user, amount):
    scores = UserShopScore.objects.filter(user=user)
    if not scores.exists():
        for shop in user.applicable_shops.all():
            UserShopScore.objects.create(user=user, shop=shop, score=100.0 + amount)
    else:
        for s in scores:
            s.score += amount
            s.save()
