import re

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from store.models import Address, Order, User
from store.services.shipping import CITIES, SERVICES, provider, validate_destination


def ukrainian_phone(value):
    digits = re.sub(r"[\s()\-+]", "", value)
    if digits.startswith("0") and len(digits) == 10:
        digits = "38" + digits
    if not re.fullmatch(r"380\d{9}", digits):
        raise ValidationError("Вкажіть український номер у форматі +380XXXXXXXXX.")
    return "+" + digits


class CheckoutForm(forms.Form):
    name = forms.CharField(
        label="Ім’я та прізвище",
        max_length=120,
        min_length=3,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    email = forms.EmailField(
        label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"})
    )
    phone = forms.CharField(
        label="Телефон",
        validators=[ukrainian_phone],
        widget=forms.TextInput(attrs={"autocomplete": "tel", "placeholder": "+380"}),
    )
    delivery_method = forms.ChoiceField(label="Спосіб отримання", choices=SERVICES)
    city = forms.ChoiceField(
        label="Місто", choices=[("", "Оберіть місто")] + list(CITIES.items()), required=False
    )
    branch = forms.ChoiceField(label="Відділення / поштомат", choices=[], required=False)
    street = forms.CharField(label="Вулиця, будинок, квартира", max_length=200, required=False)
    payment_method = forms.ChoiceField(label="Спосіб оплати", choices=Order.PAYMENT_METHODS)
    notes = forms.CharField(
        label="Коментар (необов’язково)",
        max_length=1000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    consent = forms.BooleanField(
        label="Погоджуюся з умовами демонстраційного магазину та обробкою даних цього замовлення"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        data = self.data or self.initial
        branches = provider.branches(data.get("city", ""), data.get("delivery_method", "warehouse"))
        self.fields["branch"].choices = [("", "Оберіть локацію")] + [
            (b["Ref"], b["Description"]) for b in branches
        ]

    def clean_phone(self):
        return ukrainian_phone(self.cleaned_data["phone"])

    def clean(self):
        data = super().clean()
        if "delivery_method" in data and "payment_method" in data:
            validate_destination(data)
        return data


class RegisterForm(forms.Form):
    name = forms.CharField(label="Ваше ім’я", max_length=120)
    email = forms.EmailField(label="Email")
    password1 = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput,
        help_text="Щонайменше 10 символів; уникайте поширених паролів.",
    )
    password2 = forms.CharField(label="Повторіть пароль", widget=forms.PasswordInput)

    def clean(self):
        data = super().clean()
        password = data.get("password1")
        if password:
            validate_password(
                password, User(email=data.get("email", ""), first_name=data.get("name", ""))
            )
        if password != data.get("password2"):
            raise ValidationError("Паролі не збігаються.")
        return data


class ProfileForm(forms.Form):
    name = forms.CharField(label="Ім’я", max_length=120)
    phone = forms.CharField(label="Телефон", required=False)

    def clean_phone(self):
        return ukrainian_phone(self.cleaned_data["phone"]) if self.cleaned_data["phone"] else ""


class AddressForm(forms.ModelForm):
    class Meta:
        model = Address
        fields = ["label", "city", "street", "recipient", "phone"]
        labels = {
            "label": "Назва адреси",
            "city": "Місто",
            "street": "Адреса",
            "recipient": "Одержувач",
            "phone": "Телефон",
        }

    def clean_phone(self):
        return ukrainian_phone(self.cleaned_data["phone"])
