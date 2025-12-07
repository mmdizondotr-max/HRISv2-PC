from django import forms
from .models import User
from attendance.models import Shop

class UserRegistrationForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'password', 'photo_id']

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")

        if password != confirm_password:
            raise forms.ValidationError("Passwords do not match")

        first_name = cleaned_data.get("first_name")
        last_name = cleaned_data.get("last_name")

        # Check for existing account with same name (ignoring case usually good practice, but strict equality for now)
        if User.objects.filter(first_name__iexact=first_name, last_name__iexact=last_name).exists():
             raise forms.ValidationError("An account with this First and Last name already exists.")

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


class AccountSettingsForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['nickname']
        widgets = {
            'nickname': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '6'}),
        }
        help_texts = {
            'nickname': 'Strictly letters only, max 6 characters. Must be unique.'
        }

    def clean_nickname(self):
        nickname = self.cleaned_data.get('nickname')
        if not nickname:
            return None
        return nickname

class UserPromotionForm(forms.ModelForm):
    suspend_user = forms.BooleanField(required=False, label="Suspend/Terminate User")

    class Meta:
        model = User
        fields = ['tier', 'applicable_shops']
        widgets = {
            'tier': forms.Select(attrs={'class': 'form-select'}),
            'applicable_shops': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop('current_user', None)
        super().__init__(*args, **kwargs)
        self.fields['applicable_shops'].queryset = Shop.objects.filter(is_active=True)
        self.fields['applicable_shops'].label = "Assignable Shops"

        # Initialize suspend checkbox based on is_active
        if self.instance.pk:
            self.fields['suspend_user'].initial = not self.instance.is_active

        if self.current_user and not self.current_user.is_superuser:
            if self.current_user.tier == 'supervisor':
                # Supervisors cannot change tiers
                if 'tier' in self.fields:
                    del self.fields['tier']

class ForgotPasswordForm(forms.Form):
    first_name = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'})
    )
    last_name = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'})
    )
    new_username = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'New Username'})
    )
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'New Password'})
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm New Password'})
    )

    def clean(self):
        cleaned_data = super().clean()
        pwd = cleaned_data.get('new_password')
        cpwd = cleaned_data.get('confirm_password')

        if pwd and cpwd and pwd != cpwd:
            raise forms.ValidationError("Passwords do not match.")

        first_name = cleaned_data.get('first_name')
        last_name = cleaned_data.get('last_name')
        new_username = cleaned_data.get('new_username')

        # Verify user exists
        try:
            user = User.objects.get(first_name__iexact=first_name, last_name__iexact=last_name)
            cleaned_data['user_cache'] = user
        except User.DoesNotExist:
            raise forms.ValidationError("No account found with that First and Last name.")

        # Check username uniqueness, excluding the current user if they kept the same username
        if User.objects.exclude(pk=user.pk).filter(username__iexact=new_username).exists():
            raise forms.ValidationError("This username is already taken by another user.")

        return cleaned_data
