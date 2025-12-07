from django import forms
from .models import Shift, Preference
from accounts.models import User

class PreferenceForm(forms.ModelForm):
    class Meta:
        model = Preference
        fields = ['preferred_days_off_count', 'top_preferred_day_off', 'birthday']
        widgets = {
            'birthday': forms.DateInput(attrs={'type': 'date'}),
        }

class ShiftAddForm(forms.ModelForm):
    class Meta:
        model = Shift
        fields = ['user']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = User.objects.filter(is_active=True, is_approved=True)
