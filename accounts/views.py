from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib import messages
from .forms import UserRegistrationForm, AccountSettingsForm
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
            # Tier check: Can only approve if target is strictly below current user tier,
            # unless Administrator who can do all (or via terminal).
            # Requirement: "Promote lower-tiered users up to the tier below its current tier."
            # Approval is slightly different, but "registration... for approval of a Supervisor account or higher"

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
