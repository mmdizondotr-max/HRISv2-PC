from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import HttpResponseForbidden
from .models import Preference, Schedule, Shift, UserShopScore, ShopRequirement, ScheduleChangeLog, UserPriority
from attendance.models import Shop, ShopOperatingHours, TimeLog
from accounts.models import AccountActionLog, PasswordResetRequest
from django.db.models import Count, Q
from django.utils import timezone
from .forms import PreferenceForm, ShiftAddForm
from .utils import ensure_roving_shop_and_assignments, update_scores_for_date
import datetime
import math
import random
from accounts.models import User

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
def schedule_history_list(request):
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    today = timezone.localdate()
    start_of_current_week = today - datetime.timedelta(days=today.weekday())

    schedules = Schedule.objects.filter(is_published=True).order_by('-week_start_date')

    return render(request, 'scheduling/schedule_history_list.html', {'schedules': schedules})

@login_required
def schedule_history_detail(request, schedule_id):
    if request.user.tier not in ['supervisor', 'administrator'] and not request.user.is_superuser:
        return HttpResponseForbidden()

    schedule = get_object_or_404(Schedule, id=schedule_id, is_published=True)

    dates = [schedule.week_start_date + datetime.timedelta(days=i) for i in range(7)]
    shops = Shop.objects.all()

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

    return render(request, 'scheduling/schedule_history_detail.html', {
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
        main_counts = []
        for u_id, u_shifts in user_shifts.items():
            # Count only NON-Roving shifts?
            non_roving_shifts = [s for s in u_shifts if s.role == 'main' and s.shop.name != 'Roving']
            if not non_roving_shifts:
                continue

            main_c = len(non_roving_shifts)
            main_counts.append(main_c)

        if main_counts:
            if max(main_counts) - min(main_counts) > 1:
                results['cond2'] = False # Variance > 1 implies inequality beyond remainder

        # 3. Reserve Staff Coverage & Balance
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
        for u_id, u_shifts in user_shifts.items():
            if not u_shifts: continue
            user = u_shifts[0].user # Get user object
            try:
                pref_day = user.preference.top_preferred_day_off
                worked_main_on_pref = any(s.date.weekday() == pref_day and s.role == 'main' for s in u_shifts)
                if worked_main_on_pref:
                     results['cond4'] = False
            except Preference.DoesNotExist:
                pass

        # 5. Same Shop for rest of week
        for u_id, u_shifts in user_shifts.items():
            shop_ids = set(s.shop.id for s in u_shifts if s.role == 'main' and s.shop.name != 'Roving')
            if len(shop_ids) > 1:
                results['cond5'] = False

    # 6. Cycle to different shop next week
    if len(weeks_data) > 1:
        for i in range(len(weeks_data) - 1):
            w1_shifts = weeks_data[i]['schedule'].shifts.filter(role='main')
            w2_shifts = weeks_data[i+1]['schedule'].shifts.filter(role='main')

            w1_map = {s.user.id: s.shop.id for s in w1_shifts if s.shop.name != 'Roving'} # Last shop assigned
            w2_map = {s.user.id: s.shop.id for s in w2_shifts if s.shop.name != 'Roving'} # First shop assigned?

            for u_id, s1_id in w1_map.items():
                if u_id in w2_map and w2_map[u_id] == s1_id:
                     results['cond6'] = False

    return results

def _generate_multi_week_schedule(shops, weeks):
    from accounts.models import User

    all_users = list(User.objects.filter(is_active=True, is_approved=True))

    roving_shop = None
    for s in shops:
        if s.name == 'Roving':
            roving_shop = s
            break

    if not roving_shop:
        roving_shop = Shop.objects.filter(name='Roving').first()

    standard_shops = [s for s in shops if s.name != 'Roving']

    user_history = {u.id: {'last_main_shop': None, 'missed_pref_day_off': False} for u in all_users}

    for schedule in weeks:
        schedule.shifts.filter(shop__in=shops).delete()

        if schedule.is_published:
             ScheduleChangeLog.objects.create(schedule=schedule, message="Regenerated (Partial/Full).")

        assigned_main_count = {u.id: 0 for u in all_users}
        assigned_main_shop = {u.id: None for u in all_users}
        daily_main_assignments = {i: set() for i in range(7)}
        daily_reserve_counts = {i: {u.id: 0 for u in all_users} for i in range(7)}

        existing_shifts = schedule.shifts.exclude(shop__in=shops)
        for s in existing_shifts:
            day_idx = (s.date - schedule.week_start_date).days
            if 0 <= day_idx < 7:
                if s.role == 'main':
                    assigned_main_count[s.user.id] = assigned_main_count.get(s.user.id, 0) + 1
                    assigned_main_shop[s.user.id] = s.shop.id
                    daily_main_assignments[day_idx].add(s.user.id)
                else:
                    daily_reserve_counts[day_idx][s.user.id] = daily_reserve_counts[day_idx].get(s.user.id, 0) + 1

        # --- Phase 1: Main Assignments (Standard Shops) ---
        for i in range(7):
            current_date = schedule.week_start_date + datetime.timedelta(days=i)
            day_idx = i

            sorted_shops = list(standard_shops)

            for shop in sorted_shops:
                try:
                    req_main = shop.requirement.required_main_staff
                except ShopRequirement.DoesNotExist:
                    req_main = 1

                assigned_count = 0
                while assigned_count < req_main:
                    # Helper to find candidates
                    def find_candidates(relax_days_limit=False):
                        candidates = []
                        for u in shop.applicable_staff.filter(is_active=True, is_approved=True):
                            if u.id in daily_main_assignments[day_idx]: continue
                            if assigned_main_shop[u.id] is not None and assigned_main_shop[u.id] != shop.id: continue

                            # Max 6 days check (unless relaxed)
                            if not relax_days_limit and assigned_main_count[u.id] >= 6: continue

                            score = 0
                            try:
                                shop_score = UserShopScore.objects.get(user=u, shop=shop).score
                                score += shop_score
                            except UserShopScore.DoesNotExist:
                                score += 100.0

                            if assigned_main_shop[u.id] == shop.id: score += 1000

                            try:
                                pref = u.preference
                                if pref.top_preferred_day_off == day_idx:
                                    if user_history[u.id]['missed_pref_day_off']:
                                        score -= 2000
                                    else:
                                        score -= 100
                            except Preference.DoesNotExist:
                                pass

                            if user_history[u.id]['last_main_shop'] == shop.id: score -= 200
                            score -= (assigned_main_count[u.id] * 10)
                            candidates.append((u, score))
                        return candidates

                    # 1. Try strict search
                    candidates = find_candidates(relax_days_limit=False)

                    # 2. If no candidates AND we have 0 assigned (insufficient staff), Try relaxed search
                    if not candidates and assigned_count == 0:
                        candidates = find_candidates(relax_days_limit=True)

                    if not candidates:
                        break

                    candidates.sort(key=lambda x: x[1], reverse=True)
                    chosen_user = candidates[0][0]

                    Shift.objects.create(
                        schedule=schedule,
                        user=chosen_user,
                        shop=shop,
                        date=current_date,
                        role='main',
                        score=candidates[0][1]
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
                        if u.id in daily_main_assignments[day_idx]: continue
                        if Shift.objects.filter(schedule=schedule, date=current_date, user=u, shop=shop).exists(): continue

                        res_today = daily_reserve_counts[day_idx][u.id]
                        score = -res_today

                        try:
                            shop_score = UserShopScore.objects.get(user=u, shop=shop).score
                            score += (shop_score / 10.0)
                        except UserShopScore.DoesNotExist:
                            pass

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
                        score=candidates[0][1]
                    )

                    assigned_count += 1
                    daily_reserve_counts[day_idx][chosen_user.id] += 1

        # --- Phase 3: Roving Assignments ---
        if roving_shop:
            roving_staff = roving_shop.applicable_staff.filter(is_active=True, is_approved=True)
            for i in range(7):
                current_date = schedule.week_start_date + datetime.timedelta(days=i)
                day_idx = i

                for u in roving_staff:
                    is_main = u.id in daily_main_assignments[day_idx]
                    is_reserve = daily_reserve_counts[day_idx][u.id] > 0

                    if not is_main and not is_reserve:
                        if Shift.objects.filter(schedule=schedule, date=current_date, user=u, shop=roving_shop).exists():
                            continue

                        Shift.objects.create(
                            schedule=schedule,
                            user=u,
                            shop=roving_shop,
                            date=current_date,
                            role='main',
                            score=0.0
                        )
                        daily_main_assignments[day_idx].add(u.id)
                        assigned_main_shop[u.id] = roving_shop.id

        # --- End of Week Analysis for History ---
        for u in all_users:
            if assigned_main_shop[u.id]:
                user_history[u.id]['last_main_shop'] = assigned_main_shop[u.id]

            try:
                pref_day = u.preference.top_preferred_day_off
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

def reset_system_data(request_user):
    """
    Deletes all data except:
    - Superusers
    - The 'Roving' Shop
    - The request_user (to prevent self-lockout)
    """
    # 1. Users: Exclude superusers and current user
    users_to_delete = User.objects.exclude(Q(is_superuser=True) | Q(id=request_user.id))
    users_to_delete.delete()

    # 2. Shops: Exclude 'Roving'
    shops_to_delete = Shop.objects.exclude(name='Roving')
    shops_to_delete.delete()

    # 3. All other operational data
    Schedule.objects.all().delete()
    TimeLog.objects.all().delete()
    AccountActionLog.objects.all().delete()
    ScheduleChangeLog.objects.all().delete()
    UserPriority.objects.all().delete()
    UserShopScore.objects.all().delete()
    Preference.objects.all().delete()
    PasswordResetRequest.objects.all().delete()

@user_passes_test(lambda u: u.tier == 'administrator' or u.is_superuser)
def reset_data(request):
    if request.method == 'POST':
        if 'confirm_reset' in request.POST:
            reset_system_data(request.user)
            messages.success(request, "All system data has been reset.")
            return redirect('scheduling:generator')

    return render(request, 'scheduling/reset_confirm.html')

@user_passes_test(lambda u: u.tier == 'administrator' or u.is_superuser)
def load_test_data(request):
    if request.method == 'POST':
        # 0. Reset Data first
        reset_system_data(request.user)

        # 1. Create Dummy Shops
        shop1, _ = Shop.objects.get_or_create(name="Dummy Shop 1")
        shop2, _ = Shop.objects.get_or_create(name="Dummy Shop 2")
        shop1.is_active = True
        shop2.is_active = True
        shop1.save()
        shop2.save()

        # Operating Hours
        for shop in [shop1, shop2]:
            for day in range(7):
                ShopOperatingHours.objects.get_or_create(
                    shop=shop, day=day,
                    defaults={'open_time': datetime.time(9, 0), 'close_time': datetime.time(17, 0)}
                )

        # Requirements
        # Shop 1: Duty=2, Standby=1
        r1, _ = ShopRequirement.objects.get_or_create(shop=shop1)
        r1.required_main_staff = 2
        r1.required_reserve_staff = 1
        r1.save()

        # Shop 2: Duty=1, Standby=1
        r2, _ = ShopRequirement.objects.get_or_create(shop=shop2)
        r2.required_main_staff = 1
        r2.required_reserve_staff = 1
        r2.save()

        # 2. Create Dummy Users
        # 4 Regulars, 1 Supervisor
        first_names = ['James', 'John', 'Robert', 'Michael', 'William', 'Mary', 'Patricia', 'Jennifer', 'Linda', 'Elizabeth']

        # Select 5 unique names
        chosen_names = random.sample(first_names, 5)

        dummy_users = []
        for i in range(1, 5):
            fname = chosen_names[i-1]
            u, _ = User.objects.get_or_create(username=f"dummy_user_{i}", defaults={
                'first_name': fname,
                'last_name': "Dummy",
                'email': f"dummy{i}@example.com",
                'tier': 'regular',
                'is_approved': True
            })
            u.set_password("dummy")
            u.save()
            dummy_users.append(u)

        fname_sup = chosen_names[4]
        sup, _ = User.objects.get_or_create(username="dummy_supervisor", defaults={
            'first_name': fname_sup,
            'last_name': "Dummy",
            'email': "dummysup@example.com",
            'tier': 'supervisor',
            'is_approved': True
        })
        sup.set_password("dummy")
        sup.save()

        # Assign Shops
        # Regulars -> Both Dummy Shops
        for u in dummy_users:
            u.applicable_shops.add(shop1, shop2)

        # Supervisor -> Roving (standard logic)
        ensure_roving_shop_and_assignments()

        # 3. Simulation Loop (Past 8 weeks)
        today = timezone.localdate()
        # Find start date: Monday 8 weeks ago
        # Start of current week
        start_current_week = today - datetime.timedelta(days=today.weekday())
        start_sim = start_current_week - datetime.timedelta(weeks=7) # 8 weeks total including current

        # Simulation Target Shops
        target_shops = [shop1, shop2]
        roving = Shop.objects.filter(name='Roving').first()
        if roving:
            target_shops.append(roving)

        for w in range(8):
            week_start = start_sim + datetime.timedelta(weeks=w)

            # Create Schedule
            schedule, _ = Schedule.objects.get_or_create(week_start_date=week_start)

            # Generate
            _generate_schedule(target_shops, schedule)

            # Publish
            schedule.is_published = True
            schedule.save()

            # Simulate Attendance for each day of this week
            for d in range(7):
                sim_date = week_start + datetime.timedelta(days=d)

                # Stop if future
                if sim_date > today:
                    break

                # Duty Staff
                duty_shifts = Shift.objects.filter(schedule=schedule, date=sim_date, role='main', shop__in=[shop1, shop2])

                absent_shops = set()

                for shift in duty_shifts:
                    # 1/60 chance of absence
                    if random.randint(1, 60) == 1:
                        # Absent
                        absent_shops.add(shift.shop.id)
                    else:
                        # Present -> TimeLog
                        # Use operating hours?
                        # Assuming 9-17
                        TimeLog.objects.get_or_create(
                            user=shift.user,
                            date=sim_date,
                            defaults={
                                'shop': shift.shop,
                                'time_in': datetime.time(9, 0),
                                'time_out': datetime.time(17, 0)
                            }
                        )

                # Standby Staff Substitution
                standby_shifts = Shift.objects.filter(schedule=schedule, date=sim_date, role='backup', shop__in=[shop1, shop2])

                for shift in standby_shifts:
                    if shift.shop.id in absent_shops:
                        # Substitute!
                        TimeLog.objects.get_or_create(
                            user=shift.user,
                            date=sim_date,
                            defaults={
                                'shop': shift.shop,
                                'time_in': datetime.time(9, 0),
                                'time_out': datetime.time(17, 0)
                            }
                        )

                # Roving Supervisor?
                if roving:
                    sup_shifts = Shift.objects.filter(schedule=schedule, date=sim_date, role='main', shop=roving)
                    for shift in sup_shifts:
                         if random.randint(1, 60) != 1:
                            TimeLog.objects.get_or_create(
                                user=shift.user,
                                date=sim_date,
                                defaults={
                                    'shop': shift.shop,
                                    'time_in': datetime.time(9, 0),
                                    'time_out': datetime.time(17, 0)
                                }
                            )

                # Update Scores
                update_scores_for_date(sim_date)

        messages.success(request, "Load Test Data Generated Successfully (8 Weeks).")
        return redirect('scheduling:load_test_data')

    return render(request, 'scheduling/load_test_confirm.html')
