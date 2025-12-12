from django import forms
from .models import TimeLog

class TimeLogEditForm(forms.ModelForm):
    manual_remarks = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        required=True,
        label="Reason for Edit / Remarks",
        help_text="Please explain why you are modifying this record."
    )

    class Meta:
        model = TimeLog
        fields = ['time_in', 'time_out']
        widgets = {
            'time_in': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'time_out': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
        }
