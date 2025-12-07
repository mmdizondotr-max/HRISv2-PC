from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib import messages
from .forms import UserRegistrationForm, AccountSettingsForm, UserPromotionForm
from django.contrib.auth.decorators import login_required
from .models import User
from django.http import HttpResponseForbidden

def register(request):
    if request.user.is_authenticated:
        return redirect('attendance:home')

    if request.method == 'POST':
        form = UserRegistrationForm(request.POST, request.FILES)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False # Require approval
            user.save()
            messages.success(request, 'Registration successful! Please wait for Supervisor approval.')
            return redirect('login')
    else:
        form = UserRegistrationForm()

    return render(request, 'accounts/register.html', {'form': form})

@login_required
def approvals(request):
    if request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden("You are not authorized to view this page.")

    pending_users = User.objects.filter(is_active=False).order_by('-date_joined')

    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        action = request.POST.get('action')
        try:
            target_user = User.objects.get(id=user_id)
            if action == 'approve':
                target_user.is_active = True
                target_user.is_approved = True
                target_user.save()
                messages.success(request, f'User {target_user.username} approved.')
            elif action == 'reject':
                target_user.delete()
                messages.warning(request, f'User {target_user.username} rejected/deleted.')
        except User.DoesNotExist:
            messages.error(request, 'User not found.')

        return redirect('accounts:approvals')

    return render(request, 'accounts/approvals.html', {'pending_users': pending_users})

@login_required
def account_settings(request):
    if request.method == 'POST':
        form = AccountSettingsForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Account settings updated.')
            return redirect('accounts:account_settings')
    else:
        form = AccountSettingsForm(instance=request.user)

    return render(request, 'accounts/account_settings.html', {'form': form})

@login_required
def account_list(request):
    if request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden("You are not authorized to view this page.")

    users = User.objects.all().order_by('last_name')
    return render(request, 'accounts/account_list.html', {'users': users})

@login_required
def account_promote(request, user_id):
    if request.user.tier not in ['supervisor', 'administrator']:
        return HttpResponseForbidden("You are not authorized to view this page.")

    target_user = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        form = UserPromotionForm(request.POST, instance=target_user)
        if form.is_valid():
            # Check if attempting to promote to or beyond own tier?
            # "Promote lower-tiered users up to the tier below its current tier"
            # Supervisor can promote Regular -> Supervisor?
            # Actually, "Accounts Lists... Accessible by Supervisors and above. This is also where account promotions can be performed."
            # Logic:
            # - Admin can do anything.
            # - Supervisor can promote Regular to Regular? No.
            # - Supervisor can promote Regular to Supervisor? "up to the tier below its current tier" implies Supervisor CANNOT promote to Supervisor.
            # So Supervisor can only manage Regulars.

            new_tier = form.cleaned_data.get('tier')

            if request.user.tier == 'supervisor':
                if new_tier != 'regular': # Can't promote to Supervisor or Admin
                     messages.error(request, "Supervisors cannot promote users to Supervisor or Administrator.")
                     return redirect('accounts:account_list')

                # Check if target is already higher or equal?
                if target_user.tier in ['supervisor', 'administrator']:
                     messages.error(request, "You cannot modify this user.")
                     return redirect('accounts:account_list')

            form.save()
            messages.success(request, f"User {target_user.username} updated.")
            return redirect('accounts:account_list')
    else:
        form = UserPromotionForm(instance=target_user)

    return render(request, 'accounts/account_promote.html', {'form': form, 'target_user': target_user})
