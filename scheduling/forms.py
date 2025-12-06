from django import forms
from .models import Preference

class PreferenceForm(forms.ModelForm):
    birthday = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        required=False
    )

    class Meta:
        model = Preference
        fields = ['preferred_days_off_count', 'birthday', 'top_preferred_day_off']
