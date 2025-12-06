from django import forms
from .models import User

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
