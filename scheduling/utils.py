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
        current_applicable = set(user.applicable_shops.all())
        target_applicable = set()

        if user.tier == 'supervisor':
            target_applicable.add(roving_shop)
        elif user.tier == 'regular':
            for s in regular_shops:
                target_applicable.add(s)

        if user.tier == 'supervisor':
            if set(current_applicable) != target_applicable:
                user.applicable_shops.set(target_applicable)
        elif user.tier == 'regular':
             if set(current_applicable) != target_applicable:
                user.applicable_shops.set(target_applicable)

def calculate_assignment_score(user, shop, date, history_data, current_week_assignments, min_duty_count_among_eligible=None):
    """
    Calculates the score for assigning 'user' to 'shop' on 'date' as Duty Staff.

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

    # Helper to check if log matches shop
    def log_matches_shop(log, shop_id):
        # Handle case where log.shop might be None or ID
        if not log.shop: return False
        return log.shop.id == shop_id

    # b. Deduct 1 point for each day of the previous week that the staff reported (timed-in) to the same shop.
    for log in history_data['prev_week_logs']:
        if log.user_id == user.id and log_matches_shop(log, shop.id):
            score -= 1.0

    # c. Deduct 1 point for each week from the past 3 weeks, not counting the previous, that the staff reported at least once (timed-in) to the same shop.
    # We need to group past_3_weeks_logs by week.
    # Assuming history_data['past_3_weeks_logs'] is flat list.
    # We can use the date to determine week.
    weeks_worked = set()
    for log in history_data['past_3_weeks_logs']:
        if log.user_id == user.id and log_matches_shop(log, shop.id):
            # Identify week. Simple way: iso year and week number.
            weeks_worked.add(log.date.isocalendar()[:2])
    score -= len(weeks_worked) * 1.0

    # d. Deduct 1 point for each day of the previous week that the staff timed-in.
    # (Any shop)
    for log in history_data['prev_week_logs']:
        if log.user_id == user.id:
            score -= 1.0

    # e. Deduct 2 points for each time the staff was assigned as Duty Staff in the same week.
    # "same week" means the current week being generated.
    current_duty_count = current_week_assignments.get_duty_count(user.id)
    score -= (current_duty_count * 2.0)

    # f. Deduct 1 point if staff's preferred day off matches the current day.
    try:
        pref = user.preference
        # date.weekday() returns 0=Mon, 6=Sun. Preference uses same mapping.
        if pref.top_preferred_day_off == date.weekday():
            score -= 1.0
    except Preference.DoesNotExist:
        pass

    # g. Deduct 2 points for each day staff acted as substitute in the past week.
    # "Acted as substitute" = Shift(backup) AND TimeLog exists for that day.
    # We need to cross reference prev_week_shifts (backup) and prev_week_logs.
    sub_count = 0
    # Create set of dates user worked
    worked_dates = set()
    for log in history_data['prev_week_logs']:
        if log.user_id == user.id:
            worked_dates.add(log.date)

    for shift in history_data['prev_week_shifts']:
        if shift.user_id == user.id and shift.role == 'backup':
            if shift.date in worked_dates:
                sub_count += 1
    score -= (sub_count * 2.0)

    # h. Deduct another 4 points if the staff has been assigned as Duty Staff for the 6th time prior to the current slot being evaluated.
    # Interpretation: If they ALREADY have 6 or more assignments, deduct 4.
    # Or is it "if this assignment WOULD BE the 7th"?
    # "if the staff has been assigned ... for the 6th time".
    # If they have 6 assigned, then score -= 4.
    if current_duty_count >= 6:
        score -= 4.0

    # i. Add 4 points for each day staff was absent in the past week.
    # Absent = Shift(main) AND NO TimeLog.
    absent_count = 0
    for shift in history_data['prev_week_shifts']:
        if shift.user_id == user.id and shift.role == 'main':
            if shift.date not in worked_dates:
                absent_count += 1
    score += (absent_count * 4.0)

    # j. Add 2 points for each day staff is assigned to the same shop in the current week.
    # Removed to fix "flat score" issue (User wants visible deduction per duty).
    # same_shop_count = current_week_assignments.get_shop_assignment_count(user.id, shop.id)
    # score += (same_shop_count * 2.0)

    # k. Add 1 point to all user/s with the fewest assigned shift in the current week
    if min_duty_count_among_eligible is not None:
        if current_duty_count == min_duty_count_among_eligible:
            score += 1.0

    return score


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

# Retain existing functions for compatibility if needed, or remove if fully replaced.
# keeping update_scores_for_date for daily updates.

def update_scores_for_date(target_date):
    # This might need to be revamped if the scoring system is completely dynamic now.
    # But the prompt says "Redo the entire scoring system. Each staff will have an independent score per Duty Staff slot..."
    # This refers to the *Assignment* scoring.
    # The prompt memory mentions: "A management command update_attendance_scores runs daily to update UserShopScore."
    # AND "The UserShopScore model stores persistent assignment priority scores per User per Shop."
    # BUT, the new algorithm seems to calculate scores *on the fly* based on history (logs/shifts) and doesn't mention `UserShopScore`.
    # Item 4 says: "The scoring will be as follows: a. Each staff has a base score of 20..."
    # It seems the `UserShopScore` model might be obsolete for the *assignment* phase, or at least the logic described replaces it.
    # However, I should be careful not to break other parts.
    # The prompt says "Redo the entire scoring system".
    # I will assume `UserShopScore` is NOT used for assignment anymore.
    # But `update_scores_for_date` is used by the load test and potentially daily ops.
    # If the assignment logic no longer uses `UserShopScore`, then `update_scores_for_date` might be irrelevant for *future* schedules,
    # but maybe the user still wants to see "scores" drift?
    # Actually, the new criteria rely on *TimeLogs* and *Shifts*.
    # So `update_scores_for_date` (which updates `UserShopScore`) is likely not needed for the *new generator*.
    # I'll keep it there to avoid import errors but the generator won't use it.

    print(f"Processing scores for {target_date}...")
    # ... (existing logic, kept for safety but likely unused by new generator)
    # 1. Main Staff who were ABSENT (Scheduled Main, No TimeLog)
    main_shifts = Shift.objects.filter(date=target_date, role='main')

    for shift in main_shifts:
        has_timelog = TimeLog.objects.filter(user=shift.user, date=target_date).exists()

        if not has_timelog:
            _adjust_score_all_shops(shift.user, 20.0)

    # 2. Reserve Staff
    backup_shifts = Shift.objects.filter(date=target_date, role='backup')

    for shift in backup_shifts:
        has_timelog = TimeLog.objects.filter(user=shift.user, date=target_date).exists()

        if has_timelog:
            _adjust_score_all_shops(shift.user, -20.0)
        else:
            _adjust_score_all_shops(shift.user, 10.0)

    # 3. Main Staff who WORKED (Scheduled Main, Has TimeLog)
    for shift in main_shifts:
        has_timelog = TimeLog.objects.filter(user=shift.user, date=target_date).exists()
        if has_timelog:
                _adjust_score_all_shops(shift.user, -5.0)
                _adjust_score_shop(shift.user, shift.shop, -2.0)

    # 4. Normalization
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
