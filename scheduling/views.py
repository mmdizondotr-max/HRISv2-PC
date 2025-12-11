from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from .models import Preference, Schedule, Shift, UserShopScore, ShopRequirement, ScheduleChangeLog
from attendance.models import Shop
from django.db.models import Count, Q
from django.utils import timezone
from .forms import PreferenceForm, ShiftAddForm
from .utils import ensure_roving_shop_and_assignments
import datetime
import math
import random

@login_required
def preferences(request):
    try:
        pref = request.user.preference
    except Preference.DoesNotExist:
        pref = Preference(user=request.user)

    if request.method == 'POST':
        form = PreferenceForm(request.POST, instance=pref)
        if form.is_valid():
            pref = form.save(commit=False)
            pref.user = request.user
            # Enforce 1 day off
            pref.preferred_days_off_count = 1
            pref.save()
            messages.success(request, "Preferences saved.")
            return redirect('scheduling:preferences')
    else:
        form = PreferenceForm(instance=pref)

    return render(request, 'scheduling/preferences.html', {'form': form})

@login_required
def my_schedule(request):
    today = timezone.localdate()
    # Week starts Monday (0)
    start_of_current_week = today - datetime.timedelta(days=today.weekday())
    start_of_next_week = start_of_current_week + datetime.timedelta(days=7)

    schedule = Schedule.objects.filter(week_start_date=start_of_next_week, is_published=True).first()
    if not schedule:
        schedule = Schedule.objects.filter(week_start_date=start_of_current_week, is_published=True).first()

    if not schedule:
        return render(request, 'scheduling/my_schedule.html', {'schedule': None})

    dates = [schedule.week_start_date + datetime.timedelta(days=i) for i in range(7)]
    shops = Shop.objects.filter(is_active=True)

    matrix = {}
    for d in dates:
        matrix[d] = {}
        for s in shops:
            matrix[d][s.id] = {'main': [], 'backup': []}

    shifts = schedule.shifts.all().select_related('user', 'shop')
    for shift in shifts:
        if shift.date in matrix and shift.shop.id in matrix[shift.date]:
             if shift.role == 'main':
                 matrix[shift.date][shift.shop.id]['main'].append(shift.user)
             else:
                 matrix[shift.date][shift.shop.id]['backup'].append(shift.user)

    return render(request, 'scheduling/my_schedule.html', {
        'schedule': schedule,
        'dates': dates,
        'shops': shops,
        'matrix': matrix,
        'change_logs': schedule.change_logs.all().order_by('-created_at')
    })

@login_required
def generator(request):
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    # 1. Ensure Roving Shop and Assignments logic
    ensure_roving_shop_and_assignments()

    today = timezone.localdate()
    days_until_monday = (0 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7

    next_week_start = today + datetime.timedelta(days=days_until_monday)

    # We need to handle 4 weeks
    weeks = []
    for i in range(4):
        start_date = next_week_start + datetime.timedelta(days=i*7)
        sch, _ = Schedule.objects.get_or_create(week_start_date=start_date)
        weeks.append(sch)

    current_schedule = weeks[0]

    shops = Shop.objects.filter(is_active=True)

    if request.method == 'POST':
        if 'generate' in request.POST:
            _generate_multi_week_schedule(shops, weeks)
            messages.success(request, "Schedule generated for 4 weeks.")
            return redirect('scheduling:generator')
        elif 'publish' in request.POST:
            current_schedule.is_published = True
            current_schedule.save()
            messages.success(request, "Schedule published (Week 1 only).")
            return redirect('scheduling:generator')
        elif 'clear' in request.POST:
            for sch in weeks:
                sch.shifts.all().delete()
                sch.change_logs.all().delete()
                sch.is_published = False
                sch.save()
            messages.success(request, "Generated schedule window cleared.")
            return redirect('scheduling:generator')

    # Prepare data for Template (All 4 weeks)
    weeks_data = []
    for schedule in weeks:
        dates = [schedule.week_start_date + datetime.timedelta(days=i) for i in range(7)]
        matrix = {}
        for d in dates:
            matrix[d] = {}
            for s in shops:
                matrix[d][s.id] = {'main': [], 'backup': []}

        shifts = schedule.shifts.all().select_related('user', 'shop')
        for shift in shifts:
            if shift.date in matrix and shift.shop.id in matrix[shift.date]:
                if shift.role == 'main':
                    matrix[shift.date][shift.shop.id]['main'].append(shift)
                else:
                    matrix[shift.date][shift.shop.id]['backup'].append(shift)

        weeks_data.append({
            'schedule': schedule,
            'dates': dates,
            'matrix': matrix
        })

    # New Ideal Staff Calculation
    # Formula: Max( ceil(Total_Main_Weekly / 6), Daily_Main_Max + 1 )

    total_main_slots = 0
    daily_main_total = 0
    for shop in shops:
        if shop.name == 'Roving':
            continue
        try:
            req_main = shop.requirement.required_main_staff
        except ShopRequirement.DoesNotExist:
            req_main = 1

        total_main_slots += req_main * 7
        daily_main_total += req_main

    staff_needed_workload = math.ceil(total_main_slots / 6)
    staff_needed_daily_coverage = daily_main_total + 1

    ideal_staff_count = max(staff_needed_workload, staff_needed_daily_coverage)

    # Verification Checklist
    checklist = verify_schedule(weeks_data, shops)

    return render(request, 'scheduling/generator.html', {
        'weeks_data': weeks_data,
        'current_schedule': current_schedule,
        'shops': shops,
        'change_logs': current_schedule.change_logs.all().order_by('-created_at'),
        'ideal_staff_count': ideal_staff_count,
        'checklist': checklist
    })

def verify_schedule(weeks_data, shops):
    """
    Checks conditions 1-6.
    Returns a dict with status for each condition.
    """
    results = {
        'cond1': True, # Shop Coverage (Main)
        'cond2': True, # Equal Days Off (Mainly Main assignment balance)
        'cond3': True, # Reserve Staff filled & balanced
        'cond4': True, # Preferred Day Off followed
        'cond5': True, # Same Shop for rest of week
        'cond6': True, # Cycle to different shop next week
    }

    # Implementation of checks...
    # This runs on the generated data

    for week in weeks_data:
        schedule = week['schedule']
        matrix = week['matrix']
        shifts = schedule.shifts.all()

        # Helper to get shifts by user
        user_shifts = {}
        for s in shifts:
            if s.user.id not in user_shifts: user_shifts[s.user.id] = []
            user_shifts[s.user.id].append(s)

        # 1. Shop Coverage
        for d, shop_data in matrix.items():
            for s_id, assignments in shop_data.items():
                shop = shops.get(id=s_id)
                if shop.name == 'Roving':
                    continue

                try:
                    req_main = shop.requirement.required_main_staff
                except ShopRequirement.DoesNotExist:
                    req_main = 1

                if len(assignments['main']) < req_main:
                    results['cond1'] = False # Failed coverage

        # 2. Equal Days Off (Workload Balance)
        # Check variance in total Main shifts per user
        # Exclude Roving shifts from this calculation?
        # "Roving should not be included in checks related to Requirements Verification Checklist."
        # This implies we shouldn't fail if Roving staff have weird workload.
        # But if we check GLOBAL variance, Roving staff might skew it.
        # Let's filter out users who are primarily assigned to Roving?
        # Or just filter out Roving shifts from the count.

        main_counts = []
        for u_id, u_shifts in user_shifts.items():
            # Count only NON-Roving shifts?
            non_roving_shifts = [s for s in u_shifts if s.role == 'main' and s.shop.name != 'Roving']
            # If user has NO non-roving shifts, they might be a Roving Supervisor.
            # Should we include them with 0 count? That would break variance.
            # We should probably exclude users who ONLY have Roving shifts or NO shifts.
            if not non_roving_shifts:
                continue

            main_c = len(non_roving_shifts)
            main_counts.append(main_c)

        if main_counts:
            if max(main_counts) - min(main_counts) > 1:
                results['cond2'] = False # Variance > 1 implies inequality beyond remainder

        # 3. Reserve Staff Coverage & Balance
        # Check reserve slots filled
        # Check reserve assignment balance (Equal numbers... per day)
        # This is hard to check perfectly on aggregate, but let's check basic filling.
        for d, shop_data in matrix.items():
            for s_id, assignments in shop_data.items():
                shop = shops.get(id=s_id)
                if shop.name == 'Roving':
                    continue

                try:
                    req_res = shop.requirement.required_reserve_staff
                except ShopRequirement.DoesNotExist:
                    req_res = 0
                if len(assignments['backup']) < req_res:
                    results['cond3'] = False

        # 4. Preferred Day Off
        # Need access to user prefs.
        for u_id, u_shifts in user_shifts.items():
            if not u_shifts: continue
            user = u_shifts[0].user # Get user object
            try:
                pref_day = user.preference.top_preferred_day_off
                # Check if user worked Main on this day
                worked_main_on_pref = any(s.date.weekday() == pref_day and s.role == 'main' for s in u_shifts)
                if worked_main_on_pref:
                     results['cond4'] = False
            except Preference.DoesNotExist:
                pass

        # 5. Same Shop for rest of week
        # Check if user switches shops within the week
        for u_id, u_shifts in user_shifts.items():
            # Filter out Roving shifts?
            # If a user switches from Regular to Roving, is that bad?
            # Roving logic is separate. Let's exclude Roving shops from this set.
            shop_ids = set(s.shop.id for s in u_shifts if s.role == 'main' and s.shop.name != 'Roving')
            if len(shop_ids) > 1:
                results['cond5'] = False

    # 6. Cycle to different shop next week
    # Compare Week 1 vs Week 2, etc.
    if len(weeks_data) > 1:
        for i in range(len(weeks_data) - 1):
            w1_shifts = weeks_data[i]['schedule'].shifts.filter(role='main')
            w2_shifts = weeks_data[i+1]['schedule'].shifts.filter(role='main')

            w1_map = {s.user.id: s.shop.id for s in w1_shifts if s.shop.name != 'Roving'} # Last shop assigned
            w2_map = {s.user.id: s.shop.id for s in w2_shifts if s.shop.name != 'Roving'} # First shop assigned?

            # This is a rough check. "Cycled to a different shop".
            # Check if user assigned to Shop A in W1 is assigned to Shop A in W2
            for u_id, s1_id in w1_map.items():
                if u_id in w2_map and w2_map[u_id] == s1_id:
                     results['cond6'] = False

    return results

def _generate_multi_week_schedule(shops, weeks):
    from accounts.models import User

    all_users = list(User.objects.filter(is_active=True, is_approved=True))

    # Identify Roving Shop
    roving_shop = None
    for s in shops:
        if s.name == 'Roving':
            roving_shop = s
            break

    # Shops to process in standard loop (Exclude Roving)
    standard_shops = [s for s in shops if s.name != 'Roving']

    # State tracking across weeks
    # user_history[user_id] = { 'last_main_shop': None, 'missed_pref_day_off_last_week': False }
    user_history = {u.id: {'last_main_shop': None, 'missed_pref_day_off': False} for u in all_users}

    for schedule in weeks:
        # Clear existing
        schedule.shifts.all().delete()
        schedule.change_logs.all().delete()

        if schedule.is_published:
             ScheduleChangeLog.objects.create(schedule=schedule, message="Regenerated.")

        # Weekly State
        # assigned_main_count[user_id]
        assigned_main_count = {u.id: 0 for u in all_users}

        # assigned_main_shop[user_id] -> ShopID (Enforce single shop per week)
        assigned_main_shop = {u.id: None for u in all_users}

        # Assignments by day for Reserve checks
        daily_main_assignments = {i: set() for i in range(7)} # i=0..6
        daily_reserve_counts = {i: {u.id: 0 for u in all_users} for i in range(7)}

        # --- Phase 1: Main Assignments (Standard Shops) ---
        for i in range(7):
            current_date = schedule.week_start_date + datetime.timedelta(days=i)
            day_idx = i

            sorted_shops = list(standard_shops)
            # Maybe sort by requirement descending?

            for shop in sorted_shops:
                try:
                    req_main = shop.requirement.required_main_staff
                except ShopRequirement.DoesNotExist:
                    req_main = 1

                assigned_count = 0
                while assigned_count < req_main:
                    candidates = []

                    for u in shop.applicable_staff.filter(is_active=True, is_approved=True):
                        # Filter: Already Main today?
                        if u.id in daily_main_assignments[day_idx]:
                            continue

                        # Filter: Assigned to DIFFERENT shop this week?
                        if assigned_main_shop[u.id] is not None and assigned_main_shop[u.id] != shop.id:
                            continue

                        # Filter: Max 6 days? (Implicit "At least 1 day off")
                        if assigned_main_count[u.id] >= 6:
                            continue

                        # Calculate Priority Score (Heuristic)
                        score = 0

                        # 1. Continuity (Already assigned to this shop this week)
                        if assigned_main_shop[u.id] == shop.id:
                            score += 1000

                        # 2. Preferred Day Off
                        # If today is preferred, HUGE Penalty.
                        # Unless "did not get preferred day off... last week".
                        try:
                            pref = u.preference
                            if pref.top_preferred_day_off == day_idx:
                                if user_history[u.id]['missed_pref_day_off']:
                                    # If they missed it last week, we MUST honor it this week.
                                    # So we want to make it VERY unlikely they are picked.
                                    # Score should be much LOWER than standard penalty.
                                    score -= 2000 # Massive penalty to prevent assignment
                                else:
                                    score -= 100 # Standard penalty
                        except Preference.DoesNotExist:
                            pass

                        # 3. Rotation (Different shop than last week)
                        if user_history[u.id]['last_main_shop'] == shop.id:
                            score -= 200 # Penalize repeating shop across weeks

                        # 4. Workload (Equal Days Off) -> Prioritize fewer shifts
                        score -= (assigned_main_count[u.id] * 10)

                        candidates.append((u, score))

                    if not candidates:
                        break

                    # Sort Descending
                    candidates.sort(key=lambda x: x[1], reverse=True)

                    # Pick top
                    chosen_user = candidates[0][0]

                    Shift.objects.create(
                        schedule=schedule,
                        user=chosen_user,
                        shop=shop,
                        date=current_date,
                        role='main',
                        score=0.0 # No longer using scores
                    )

                    assigned_count += 1
                    assigned_main_count[chosen_user.id] += 1
                    assigned_main_shop[chosen_user.id] = shop.id
                    daily_main_assignments[day_idx].add(chosen_user.id)

        # --- Phase 2: Reserve Assignments (Standard Shops) ---
        for i in range(7):
            current_date = schedule.week_start_date + datetime.timedelta(days=i)
            day_idx = i

            for shop in standard_shops:
                try:
                    req_res = shop.requirement.required_reserve_staff
                except ShopRequirement.DoesNotExist:
                    req_res = 0

                if req_res == 0: continue

                assigned_count = 0
                while assigned_count < req_res:
                    candidates = []
                    for u in shop.applicable_staff.filter(is_active=True, is_approved=True):
                        # Filter: Main today?
                        if u.id in daily_main_assignments[day_idx]:
                            continue

                        # Already reserved at this shop today?
                        # Check DB? Or in-memory?
                        if Shift.objects.filter(schedule=schedule, date=current_date, user=u, shop=shop).exists():
                            continue

                        # Priority: "Equal numbers of Standby Staff assignment per day"
                        # Prioritize those with FEWEST reserves TODAY.
                        res_today = daily_reserve_counts[day_idx][u.id]

                        score = -res_today

                        candidates.append((u, score))

                    if not candidates:
                        break

                    candidates.sort(key=lambda x: x[1], reverse=True)
                    chosen_user = candidates[0][0]

                    Shift.objects.create(
                        schedule=schedule,
                        user=chosen_user,
                        shop=shop,
                        date=current_date,
                        role='backup',
                        score=0.0
                    )

                    assigned_count += 1
                    daily_reserve_counts[day_idx][chosen_user.id] += 1

        # --- Phase 3: Roving Assignments ---
        # "All Supervisors assigned as Roving automatically go to Roving.
        # Remember that a Supervisor can only be in Roving if he is not assigned to any regular store."

        if roving_shop:
            roving_staff = roving_shop.applicable_staff.filter(is_active=True, is_approved=True)
            for i in range(7):
                current_date = schedule.week_start_date + datetime.timedelta(days=i)
                day_idx = i

                for u in roving_staff:
                    # Check if assigned to any regular shop today (Main or Standby)
                    # We check:
                    # 1. Is user in daily_main_assignments? (Covers Main)
                    # 2. Is user in daily_reserve_counts > 0? (Covers Standby)

                    is_main = u.id in daily_main_assignments[day_idx]
                    is_reserve = daily_reserve_counts[day_idx][u.id] > 0

                    if not is_main and not is_reserve:
                        # Assign to Roving
                        Shift.objects.create(
                            schedule=schedule,
                            user=u,
                            shop=roving_shop,
                            date=current_date,
                            role='main',
                            score=0.0
                        )
                        # No need to update assigned_main_count/shop unless we want History tracking to work for them?
                        # It's better to update so they don't get picked for other things if we added more logic later.
                        daily_main_assignments[day_idx].add(u.id)
                        assigned_main_shop[u.id] = roving_shop.id

        # --- End of Week Analysis for History ---
        for u in all_users:
            # Update last shop
            if assigned_main_shop[u.id]:
                user_history[u.id]['last_main_shop'] = assigned_main_shop[u.id]

            # Check preferred day off adherence
            try:
                pref_day = u.preference.top_preferred_day_off
                # Did they work Main on this day?
                worked = u.id in daily_main_assignments[pref_day]
                if worked:
                    user_history[u.id]['missed_pref_day_off'] = True
                else:
                    user_history[u.id]['missed_pref_day_off'] = False
            except Preference.DoesNotExist:
                user_history[u.id]['missed_pref_day_off'] = False

@login_required
def shift_delete(request, shift_id):
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    shift = get_object_or_404(Shift, id=shift_id)
    schedule = shift.schedule

    ScheduleChangeLog.objects.create(
        schedule=schedule,
        user=request.user,
        message=f"Removed {shift.user} from {shift.shop} on {shift.date} ({shift.get_role_display()})"
    )

    shift.delete()
    messages.success(request, "Shift removed.")
    return redirect('scheduling:generator')

@login_required
def shift_add(request, schedule_id, date, shop_id, role):
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    schedule = get_object_or_404(Schedule, id=schedule_id)
    shop = get_object_or_404(Shop, id=shop_id)
    target_date = datetime.datetime.strptime(date, "%Y-%m-%d").date()

    if request.method == 'POST':
        form = ShiftAddForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data['user']

            if Shift.objects.filter(user=user, date=target_date).exists():
                messages.error(request, f"{user} is already assigned on {target_date}")
            else:
                Shift.objects.create(
                    schedule=schedule,
                    user=user,
                    shop=shop,
                    date=target_date,
                    role=role,
                    score=0.0
                )
                ScheduleChangeLog.objects.create(
                    schedule=schedule,
                    user=request.user,
                    message=f"Added {user} to {shop} on {target_date} ({role})"
                )
                messages.success(request, "Shift added.")
                return redirect('scheduling:generator')
    else:
        form = ShiftAddForm()

    return render(request, 'scheduling/shift_add.html', {
        'form': form, 'date': target_date, 'shop': shop, 'role': role
    })

def _generate_schedule(shops, schedule):
    return _generate_multi_week_schedule(shops, [schedule])
