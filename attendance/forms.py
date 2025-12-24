from django import forms
from .models import Shop, ShopOperatingHours
from scheduling.models import ShopRequirement

class ShopForm(forms.ModelForm):
    class Meta:
        model = Shop
        fields = ['name', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

class ShopRequirementForm(forms.ModelForm):
    class Meta:
        model = ShopRequirement
        fields = ['required_main_staff']
        widgets = {
            'required_main_staff': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

class ShopOperatingHoursForm(forms.ModelForm):
    class Meta:
        model = ShopOperatingHours
        fields = ['day', 'open_time', 'close_time']
        widgets = {
            'day': forms.Select(attrs={'class': 'form-select'}),
            'open_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'close_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
        }
