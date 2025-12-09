from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from .models import Preference, Schedule, Shift, UserShopScore, ShopRequirement, ScheduleChangeLog
from attendance.models import Shop
from django.db.models import Count, Q
from django.utils import timezone
from .forms import PreferenceForm, ShiftAddForm
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
    # If today is Monday(0), start_of_current_week = today - 0 = today
    # If today is Sunday(6), start_of_current_week = today - 6 = Monday
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
    if request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden()

    today = timezone.localdate()
    # Find start of next week (Monday)
    # If today is Monday (0), next Monday is +7
    # If today is Sunday (6), next Monday is +1
    days_until_monday = (0 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7 # Always target next week from today if today is Monday?
        # Actually, if I run generator on Monday, I usually want "Next Week" preview.
        # But if I want to edit "This Week", I might need a way to select.
        # Standard behavior: Generator targets UPCOMING week.
        pass

    next_week_start = today + datetime.timedelta(days=days_until_monday)

    # We need to handle 4 weeks: next_week_start + 0, +7, +14, +21
    weeks = []
    for i in range(4):
        start_date = next_week_start + datetime.timedelta(days=i*7)
        sch, _ = Schedule.objects.get_or_create(week_start_date=start_date)
        weeks.append(sch)

    current_schedule = weeks[0] # The primary one to act on (Publish)

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

    # Calculate Ideal Staff Count (Same as before, roughly)
    total_main_slots = 0
    total_res_slots = 0
    for shop in shops:
        try:
            req_main = shop.requirement.required_main_staff
            req_res = shop.requirement.required_reserve_staff
        except ShopRequirement.DoesNotExist:
            req_main = 1
            req_res = 0
        total_main_slots += req_main * 7
        total_res_slots += req_res * 7

    # New Ideal Staff Calculation
    # Formula: Max( ceil(Total_Main_Weekly / 6), Daily_Main_Max + 1 )
    # Note: "Daily_Main_Max" is constant across days if shops have fixed requirements.
    # It is Sum(shop.req_main)

    daily_main_total = 0
    for shop in shops:
        try:
            req_main = shop.requirement.required_main_staff
        except ShopRequirement.DoesNotExist:
            req_main = 1
        daily_main_total += req_main

    staff_needed_workload = math.ceil(total_main_slots / 6)
    staff_needed_daily_coverage = daily_main_total + 1

    # Check if we actually need reserves
    if total_res_slots == 0:
        staff_needed_daily_coverage = daily_main_total

    ideal_staff_count = max(staff_needed_workload, staff_needed_daily_coverage)

    return render(request, 'scheduling/generator.html', {
        'weeks_data': weeks_data, # List of dicts
        'current_schedule': current_schedule, # For publish button
        'shops': shops,
        'change_logs': current_schedule.change_logs.all().order_by('-created_at'),
        'ideal_staff_count': ideal_staff_count
    })

def _generate_multi_week_schedule(shops, weeks):
    # This function handles the simulation across 4 weeks.
    from accounts.models import User

    # 1. Load Initial State (Scores)
    all_users = User.objects.filter(is_active=True, is_approved=True)

    # Map: UserID -> ShopID -> Score
    # We load from DB initially.
    user_scores = {}
    for u in all_users:
        user_scores[u.id] = {}
        # Ensure UserShopScore exists for applicable shops
        for s in u.applicable_shops.all():
            # New Employee Logic: Start with MAX score of current employees for this shop
            try:
                score_obj = UserShopScore.objects.get(user=u, shop=s)
            except UserShopScore.DoesNotExist:
                # Calculate max score for this shop
                existing_scores = UserShopScore.objects.filter(shop=s).values_list('score', flat=True)
                if existing_scores:
                    start_score = max(existing_scores)
                else:
                    start_score = 100.0
                score_obj = UserShopScore.objects.create(user=u, shop=s, score=start_score)

            user_scores[u.id][s.id] = score_obj.score

    # Helper to get current score from memory
    def get_score(u_id, s_id):
        return user_scores.get(u_id, {}).get(s_id, 0.0)

    def set_score(u_id, s_id, val):
        if u_id not in user_scores: user_scores[u_id] = {}
        user_scores[u_id][s_id] = val

    def adjust_score(u_id, s_id, delta):
        curr = get_score(u_id, s_id)
        set_score(u_id, s_id, curr + delta)

    # 2. Iterate Weeks
    for schedule in weeks:
        # --- Pre-Week: Normalization ---
        # "Reset team average to 100" per shop
        for s in shops:
            scores = []
            users_in_shop = []
            for u in all_users:
                if s.id in user_scores.get(u.id, {}):
                    scores.append(user_scores[u.id][s.id])
                    users_in_shop.append(u.id)

            if scores:
                avg = sum(scores) / len(scores)
                delta = 100.0 - avg
                # Apply delta to all
                for u_id in users_in_shop:
                    adjust_score(u_id, s.id, delta)

        # Clear change logs for the schedule being regenerated
        schedule.change_logs.all().delete()

        if schedule.is_published:
             ScheduleChangeLog.objects.create(schedule=schedule, message="Regenerated.")

        schedule.shifts.all().delete()

        # Track "Assigned to Shop X this week" for Continuity Bonus
        # Map: UserID -> Set(ShopIDs)
        assigned_shops_this_week = {u.id: set() for u in all_users}

        # Track "Assigned Main this week" for Reserve Exclusion
        assigned_main_users = set()

        # Helper to calculate Effective Score for a candidate
        def calculate_effective_score(user, shop, date):
            u_id = user.id
            s_id = shop.id

            base = get_score(u_id, s_id)
            effective = base

            # Modifier 1: Day Off Preference
            # "If an employee’s top preferred day off matches the current day... score... goes lower"
            try:
                if user.preference.top_preferred_day_off == date.weekday():
                    effective -= 50.0 # Penalty
            except Preference.DoesNotExist:
                pass

            # Modifier 2: Same Week Continuity
            # "score goes up for all slots of the same shop during the same week"
            if s_id in assigned_shops_this_week[u_id]:
                effective += 30.0 # Bonus

            return effective

        # --- Phase 1: Main Assignments ---
        # "moves from each day then each shop"
        for i in range(7):
            current_date = schedule.week_start_date + datetime.timedelta(days=i)

            for shop in shops:
                # Determine Requirements
                try:
                    req_main = shop.requirement.required_main_staff
                except ShopRequirement.DoesNotExist:
                    req_main = 1

                # Check current assignments for this slot
                # We need to fill `req_main` slots
                assigned_count = 0

                while assigned_count < req_main:
                    # Find Candidates
                    candidates = []
                    # Filter: Applicable, Active, Approved, Not Already Working Today
                    for u in shop.applicable_staff.filter(is_active=True, is_approved=True):
                        # Must check conflicts
                        # 1. Not working today (Main or Backup) - actually backup not assigned yet
                        # Just check if shift exists in DB (we are creating them as we go)
                        if Shift.objects.filter(schedule=schedule, date=current_date, user=u).exists():
                            continue

                        # Calculate Score
                        score = calculate_effective_score(u, shop, current_date)
                        candidates.append((u, score))

                    if not candidates:
                        break # No one available

                    # Sort by Score Descending
                    candidates.sort(key=lambda x: x[1], reverse=True)

                    # Handle Ties (Randomize among top scorers)
                    if candidates:
                        best_score = candidates[0][1]
                        top_candidates = [c for c in candidates if abs(c[1] - best_score) < 0.001]
                        chosen_user, final_score = random.choice(top_candidates)

                        # Assign
                        Shift.objects.create(
                            schedule=schedule,
                            user=chosen_user,
                            shop=shop,
                            date=current_date,
                            role='main',
                            score=final_score
                        )
                        assigned_count += 1
                        assigned_main_users.add(chosen_user.id)
                        assigned_shops_this_week[chosen_user.id].add(shop.id)

                        # Update State: "score decreases... unless... days off preferred"
                        # "score goes lower for all slots of the same shop during the following weeks"
                        # Actually, "Whenever an employee is assigned... score decreases (decrease chances of getting assigned)"
                        # This implies immediate fatigue for *current week* too?
                        # Yes, effectively lowering chance for tomorrow.

                        # Apply Fatigue (Global? or Shop Specific?)
                        # Prompt: "assigned as main staff to any slot (any shop, any day), its score decreases"
                        # This sounds Global.
                        # Since our scores are (User, Shop), we must decrease ALL shop scores for this user.
                        for s_iter in shops:
                            adjust_score(chosen_user.id, s_iter.id, -10.0) # Fatigue Penalty

                        # Apply Continuity (Already handled by `assigned_shops_this_week` check in `calculate_effective_score`)
                        # Note: The fatigue penalty (-10) fights the continuity bonus (+30).
                        # Net result: +20 for same shop, -10 for others. Correct.

        # --- Phase 2: Reserve Assignments ---
        # "moves from each day then each shop"
        for i in range(7):
            current_date = schedule.week_start_date + datetime.timedelta(days=i)

            for shop in shops:
                try:
                    req_res = shop.requirement.required_reserve_staff
                except ShopRequirement.DoesNotExist:
                    req_res = 0

                if req_res == 0:
                    continue

                assigned_count = 0
                while assigned_count < req_res:
                    candidates = []
                    # Filter: Applicable, Active, Approved
                    for u in shop.applicable_staff.filter(is_active=True, is_approved=True):
                        # Constraint: "only... if they have not been assigned as main staff to any shop for the day"
                        # Main Staff Check:
                        if Shift.objects.filter(schedule=schedule, date=current_date, user=u, role='main').exists():
                            continue

                        # Existing Reserve Count for this user today
                        current_reserve_count = Shift.objects.filter(schedule=schedule, date=current_date, user=u, role='backup').count()

                        # Prevent assigning same user to same shop twice on same day (sanity check)
                        if Shift.objects.filter(schedule=schedule, date=current_date, user=u, shop=shop).exists():
                            continue

                        # Score
                        score = calculate_effective_score(u, shop, current_date)

                        # Add tuple: (User, Score, ReserveCount)
                        candidates.append((u, score, current_reserve_count))

                    if not candidates:
                        break

                    # Sort:
                    # 1. Primary: Reserve Count (Ascending) -> Prioritize 0 shifts, then 1...
                    # 2. Secondary: Score (Descending)
                    candidates.sort(key=lambda x: (x[2], -x[1]))

                    # We pick from top. Handle Ties?
                    # Since we sort by Count first, then Score.
                    # Let's just pick the top one.
                    best_candidate = candidates[0]
                    chosen_user = best_candidate[0]
                    final_score = best_candidate[1]

                    # Tie breaking logic can be added, but (Count, -Score) is usually strict enough.
                    # If scores match, stable sort preserves order.

                    Shift.objects.create(
                        schedule=schedule,
                        user=chosen_user,
                        shop=shop,
                        date=current_date,
                        role='backup',
                        score=final_score
                    )
                    assigned_count += 1

                    # Reserve Impact:
                    # "Working a Reserve shift should decrease... BUT only if they actually report... I have not simulated time in/outs... so being assigned a Reserve slot should not have impact other than impacts of being on a Day-Off."
                    # "Day-Off" usually means score increases (Recovery).
                    # So Reserve assignment in simulation = Recovery (Increase Score).
                    # Increase for ALL shops to improve general availability.
                    for s_iter in shops:
                        adjust_score(chosen_user.id, s_iter.id, 10.0) # Reserve/Rest Bonus

        # --- End of Week Updates ---
        # "lower for all slots of the same shop during the following weeks" (Rotation)
        for u_id, shop_ids in assigned_shops_this_week.items():
            for s_id in shop_ids:
                # Apply Rotation Penalty for Future Weeks
                adjust_score(u_id, s_id, -10.0)

        # Note: Fatigue penalties applied during the week (-5 per shift) persist.
        # This naturally handles "Decrease chances of getting assigned" for future weeks too?
        # User said: "score goes up for all slots of the same shop during the same week... and lower... during the following weeks".
        # We handled "Same Week" via `assigned_shops_this_week` temporary bonus (+30).
        # We handle "Following Weeks" via this Rotation Penalty (-10).
        # We handled "Main Staff Fatigue" (-5 global per shift).

        # Re-save scores to DB?
        # Only if this is the "Real" generation?
        # User said: "Scores should always rollover... even when a schedule is regenerated".
        # This implies we should Commit the score changes to DB?
        # But if we regenerate 4 weeks every time, and we commit changes from Week 2, 3, 4 (simulated)...
        # Next time we run generator (for Week 1), we start with corrupted scores?
        # NO. We should only commit changes that result from ACTUAL usage or Published schedules?
        # Actually, if we regenerate Week 1, we start from "Current DB State".
        # If we commit changes from Week 1 generation, that's fine (it's the new plan).
        # But changes from Week 2, 3, 4 (Preview) should NOT be committed to the permanent `UserShopScore` table,
        # because those weeks might be regenerated differently next time.
        # However, the loop depends on the `user_scores` dictionary which carries over state in memory.
        # So the preview is consistent.
        # The question is: Do we `save()` the `UserShopScore` objects at the end?
        # Only for changes incurred by Week 1 (if we consider Week 1 "firm")?
        # Or maybe we only save when "Publish" is clicked?
        # The prompt says: "Generate schedule preview... Publishing should only cover once per week... Scores should always rollover".
        # I will choose to update the DB scores *only* when `Publish` is clicked, or maybe implicitly for the current week?
        # If I don't save to DB, the "Rollover" won't happen for the *next* real run.
        # But wait, if I run the generator 5 times before publishing, I shouldn't degrade the scores 5 times.
        # So: The `UserShopScore` in DB should only be updated by the `update_attendance_scores` command (Daily)
        # OR when a schedule is *Published* (committing the assignment effects).

        # However, the Requirement "Find a way to also show current scores... upon being assigned" implies we show them in the UI.
        # We are saving `score` on `Shift`.

        pass # End of week loop

    # Finally: Should we save the `UserShopScore` changes to DB?
    # If we don't, next time we click "Generate", we start from scratch.
    # This is safer. The "Rollover" the user wants likely means "The logic carries over week to week IN THE PREVIEW".
    # And "Scores always rollover" in real life means the *Daily Updates* and *Previous Assignments* affect it.
    # We should NOT save the speculative score changes from this generator run to the permanent DB.
    # The permanent DB scores should evolve based on *Attendance* (daily script) and maybe *Published Assignments*?
    # Actually, if we rely on "Absent -> Jump in Score", "Reserve -> Drop in Score", that handles the feedback loop.
    # Does "Assignment" itself change the permanent score?
    # "Whenever an employee is assigned... score decreases".
    # If we don't commit this, they won't get a day off in Week 2 if we haven't published Week 1?
    # Correct.
    # But if we Publish Week 1, do we commit the score changes?
    # I should add a logic in `Publish` action to commit the score changes for that week.
    # But `Publish` is simple `is_published=True`.
    # I'll stick to: Generator calculates based on current DB state + in-memory simulation.
    # The DB state is updated by `update_attendance_scores`.
    # Is that enough?
    # "Whenever an employee is assigned... score decreases". This effect must persist.
    # If I assign Mon, Score drops. Tue, Score is lower.
    # This works in simulation.
    # Does it persist to next week's generation *before* attendance happens?
    # Yes, if we want the "Fatigue" to cross weeks.
    # I will assume that the *Daily Update Script* handles the "Fact Check" (You worked -> Score changes).
    # The Generator is just a plan.
    # So I will NOT save `UserShopScore` changes here.
    # The `update_attendance_scores` script should probably also include "Worked Main -> Fatigue" logic?
    # Currently it only handles Exceptions (Absent/Reserve).
    # I should update `update_attendance_scores` to also handle "Worked Main -> Score Decrease".
    # That ensures the "Rollover" happens in reality.

    return

@login_required
def shift_delete(request, shift_id):
    if request.user.tier not in ['supervisor', 'administrator']:
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
    if request.user.tier not in ['supervisor', 'administrator']:
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
                    score=0.0 # Manual add, score unknown or irrelevant?
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
