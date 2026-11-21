from django import forms
from django.db import transaction
from django.utils import timezone
from allauth.socialaccount.forms import SignupForm

from .models import Shop, UserProfile


class ViARMSocialSignupForm(SignupForm):
    first_name = forms.CharField(max_length=150, label='First name')
    last_name = forms.CharField(max_length=150, label='Last name')
    business_name = forms.CharField(max_length=150, label='Business or shop name')
    business_type = forms.ChoiceField(
        choices=UserProfile._meta.get_field('business_type').choices,
        label='Business type',
    )
    tenant_type = forms.ChoiceField(choices=UserProfile.TENANT_TYPE_CHOICES, label='Business model')
    plan = forms.ChoiceField(choices=UserProfile.PLAN_CHOICES, initial='FREE')
    kyc_document_type = forms.ChoiceField(
        choices=[
            ('', 'Select document type'),
            ('Business registration', 'Business registration'),
            ('National ID', 'National ID'),
            ('International passport', 'International passport'),
            ("Driver's licence", "Driver's licence"),
        ],
        label='Verification document',
    )
    kyc_document_number = forms.CharField(max_length=120, label='Document number')
    agree_terms = forms.BooleanField(label='I agree to the Terms and Agreement and Privacy Policy')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
        self.fields['agree_terms'].widget.attrs['class'] = 'form-check-input'
        social_user = getattr(getattr(self, 'sociallogin', None), 'user', None)
        if social_user:
            self.fields['first_name'].initial = social_user.first_name
            self.fields['last_name'].initial = social_user.last_name

    @transaction.atomic
    def save(self, request):
        user = super().save(request)
        user.first_name = self.cleaned_data['first_name'].strip()
        user.last_name = self.cleaned_data['last_name'].strip()
        user.save(update_fields=['first_name', 'last_name'])

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = 'ADMIN'
        profile.tenant_owner = user
        profile.plan = self.cleaned_data['plan']
        profile.business_type = self.cleaned_data['business_type']
        profile.tenant_type = self.cleaned_data['tenant_type']
        profile.is_approved = False
        profile.kyc_status = 'PENDING'
        profile.kyc_document_type = self.cleaned_data['kyc_document_type']
        profile.kyc_document_number = self.cleaned_data['kyc_document_number'].strip()
        if profile.plan == 'FREE':
            profile.trial_start_date = timezone.localdate()
        shop = Shop.objects.create(
            name=self.cleaned_data['business_name'].strip(),
            tenant_owner=user,
            manager=user,
        )
        profile.shop = shop
        profile.save()
        return user
