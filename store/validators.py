from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError


def validate_product_image(value):
    if value.size > 10 * 1024 * 1024:
        raise ValidationError("Зображення має бути до 10 МБ.")
    try:
        image = Image.open(value)
        if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 20_000_000:
            raise ValidationError("Оберіть JPEG, PNG або WebP до 20 мегапікселів.")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError("Файл не є підтримуваним зображенням.") from exc
    finally:
        value.seek(0)
