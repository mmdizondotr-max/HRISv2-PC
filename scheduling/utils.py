from django.utils import timezone
import datetime
from scheduling.models import Shift, UserShopScore
from attendance.models import TimeLog
from attendance.models import Shop

def ensure_roving_shop_and_assignments():
    from accounts.models import User
    """
    Ensures 'Roving' shop exists.
    Updates assignments:
    - Supervisors -> Only Roving
    - Regulars -> All Shops (excluding Roving? or including?
      Prompt: "all Regulars should be assigned to all Shops".
      Prompt: "Supervisors are only assigned under Roving".
      Interpretation: Shops = {S1, S2, Roving}.
      Supervisors = {Roving}
      Regulars = {S1, S2} (Assuming Regulars don't 'Rove').
    """
    roving_shop, created = Shop.objects.get_or_create(name="Roving")
    if created or not roving_shop.is_active:
        roving_shop.is_active = True
        roving_shop.save()

    # Get all active shops excluding Roving
    regular_shops = Shop.objects.filter(is_active=True).exclude(id=roving_shop.id)

    all_users = User.objects.filter(is_active=True)

    for user in all_users:
        current_applicable = set(user.applicable_shops.all())
        target_applicable = set()

        if user.tier == 'supervisor':
            target_applicable.add(roving_shop)
        else:
            # Regular (and Administrator? assuming Admins act as Regulars or Supervisors?
            # Prompt: "all Regulars should be assigned to all Shops".
            # Usually Admins are not scheduled, or scheduled as Supervisors.
            # But the tier choices are regular, supervisor, administrator.
            # I will treat Administrator as Supervisor for scheduling purposes?
            # Or Regular?
            # Existing code: generator access allowed for 'supervisor', 'administrator'.
            # I will assume 'Regular' tier specifically.
            if user.tier == 'regular':
                for s in regular_shops:
                    target_applicable.add(s)
            else:
                 # Administrator?
                 # Let's assume they are like Supervisors for Roving?
                 # Or just leave them alone?
                 # Prompt only specifies "Supervisors" and "Regulars".
                 # I'll stick to strictly those tiers.
                 pass

        # Apply changes if needed
        # Note: If an Administrator is handling scheduling, we might not want to mess with their shops.
        # But if the prompt implies a rule...
        # "all Supervisors are only assigned under Roving"

        if user.tier == 'supervisor':
            # Force set
            if set(current_applicable) != target_applicable:
                user.applicable_shops.set(target_applicable)
        elif user.tier == 'regular':
             # Force set
             # Note: This overwrites manual assignments. The prompt implies this is a rule.
             if set(current_applicable) != target_applicable:
                user.applicable_shops.set(target_applicable)

def update_scores_for_date(target_date):
    """
    Updates user scores based on attendance for the given target_date.
    Logic extracted from update_attendance_scores management command.
    """
    print(f"Processing scores for {target_date}...")

    # 1. Main Staff who were ABSENT (Scheduled Main, No TimeLog)
    main_shifts = Shift.objects.filter(date=target_date, role='main')

    for shift in main_shifts:
        has_timelog = TimeLog.objects.filter(user=shift.user, date=target_date).exists()

        if not has_timelog:
            # Absent!
            print(f"User {shift.user} was ABSENT (Main) at {shift.shop}.")
            _adjust_score_all_shops(shift.user, 20.0) # Significant Increase

    # 2. Reserve Staff
    backup_shifts = Shift.objects.filter(date=target_date, role='backup')

    for shift in backup_shifts:
        has_timelog = TimeLog.objects.filter(user=shift.user, date=target_date).exists()

        if has_timelog:
            # Worked!
            print(f"User {shift.user} WORKED (Reserve) at {shift.shop}.")
            _adjust_score_all_shops(shift.user, -20.0) # Significant Decrease
        else:
            # Did NOT Work (Rest)
            print(f"User {shift.user} was RESERVE (No Work) at {shift.shop}.")
            _adjust_score_all_shops(shift.user, 10.0) # Rest Bonus

    # 3. Main Staff who WORKED (Scheduled Main, Has TimeLog)
    for shift in main_shifts:
        has_timelog = TimeLog.objects.filter(user=shift.user, date=target_date).exists()
        if has_timelog:
                # Worked Main
                print(f"User {shift.user} WORKED (Main) at {shift.shop}.")
                _adjust_score_all_shops(shift.user, -5.0) # Fatigue Penalty
                _adjust_score_shop(shift.user, shift.shop, -2.0) # Rotation Penalty

    # 4. Normalization
    shops = set(UserShopScore.objects.values_list('shop', flat=True))
    for shop_id in shops:
            scores = UserShopScore.objects.filter(shop_id=shop_id)
            if scores.exists():
                avg = sum(s.score for s in scores) / scores.count()
                delta = 100.0 - avg
                if abs(delta) > 0.01:
                    print(f"Normalizing Shop {shop_id} scores by {delta:.2f} (Avg was {avg:.2f})")
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
        # Initialize for applicable shops if no scores exist
        for shop in user.applicable_shops.all():
            UserShopScore.objects.create(user=user, shop=shop, score=100.0 + amount)
    else:
        for s in scores:
            s.score += amount
            s.save()
