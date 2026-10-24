from django import template

register = template.Library()


@register.filter
def money(value):
    return f"{int(value) / 100:,.2f}".replace(",", " ").replace(".00", "") + " ₴"
