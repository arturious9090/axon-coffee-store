from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from store.models import (
    BrewingMethod,
    ContentPage,
    GrindOption,
    PickupLocation,
    Product,
    ProductImage,
    ProductVariant,
    PromoCode,
    ShopSettings,
    StockItem,
    User,
)

COFFEES = [
    (
        "Ранок біля моря",
        "ranok",
        "Бразилія",
        "Серрадо",
        "medium",
        "Натуральна",
        "Шоколад · фундук · карамель",
        36000,
        "sand",
        ["espresso", "moka"],
    ),
    (
        "Ефіопія Сідамо",
        "ethiopia-sidamo",
        "Ефіопія",
        "Сідамо",
        "light",
        "Мита",
        "Бергамот · жасмин · персик",
        46000,
        "coral",
        ["filter", "french"],
    ),
    (
        "Колумбія Уїла",
        "colombia-huila",
        "Колумбія",
        "Уїла",
        "medium",
        "Мита",
        "Червоне яблуко · мед · какао",
        42000,
        "sea",
        ["filter", "espresso"],
    ),
    (
        "Одеський бленд",
        "odesa-blend",
        "Бразилія / Колумбія",
        "Авторський бленд",
        "medium",
        "Змішана",
        "Темний шоколад · мигдаль · ірис",
        39000,
        "sand",
        ["espresso", "moka", "turkish"],
    ),
    (
        "Кенія Кіріньяга",
        "kenya-kirinyaga",
        "Кенія",
        "Кіріньяга",
        "light",
        "Мита",
        "Смородина · грейпфрут · тростинний цукор",
        52000,
        "coral",
        ["filter", "french"],
    ),
    (
        "Гватемала Антигуа",
        "guatemala-antigua",
        "Гватемала",
        "Антигуа",
        "medium",
        "Мита",
        "Какао · апельсин · горіх",
        44000,
        "sea",
        ["espresso", "moka"],
    ),
    (
        "Коста-Рика Тарразу",
        "costa-rica-tarrazu",
        "Коста-Рика",
        "Тарразу",
        "light",
        "Хані",
        "Мед · абрикос · молочний шоколад",
        49000,
        "sand",
        ["filter", "french"],
    ),
    (
        "Перу Кахамарка",
        "peru-cajamarca",
        "Перу",
        "Кахамарка",
        "medium",
        "Мита",
        "Карамель · груша · какао",
        41000,
        "coral",
        ["espresso", "filter"],
    ),
    (
        "Вечірній причал",
        "vechirniy-prychal",
        "Бразилія",
        "Мінас-Жерайс",
        "dark",
        "Натуральна",
        "Гіркий шоколад · прянощі · волоський горіх",
        37000,
        "sea",
        ["espresso", "turkish", "moka"],
    ),
    (
        "Руанда Ньямашеке",
        "rwanda-nyamasheke",
        "Руанда",
        "Ньямашеке",
        "light",
        "Мита",
        "Чорний чай · слива · цитрус",
        47000,
        "sand",
        ["filter", "french"],
    ),
    (
        "Ефіопія Гуджі",
        "ethiopia-guji",
        "Ефіопія",
        "Гуджі",
        "light",
        "Натуральна",
        "Полуниця · тропічні фрукти · какао",
        54000,
        "coral",
        ["filter"],
    ),
    (
        "Тиха хвиля · decaf",
        "tykha-khvylia",
        "Колумбія",
        "Демонстраційний лот decaf",
        "medium",
        "Декофеїнізована",
        "Шоколад · карамель · червоне яблуко",
        45000,
        "sea",
        ["espresso", "filter"],
    ),
]

PAGES = {
    "about": (
        "Свій темп. Своя кава.",
        "Плин — вигадана одеська обжарка, створена для демонстраційного магазину Axon.\n\nНас надихає повільний ранок, теплий камінь міста й море, до якого хочеться повертатися. Ми придумали цей бренд, щоб показати весь шлях кави в інтернет-магазині: від вибору зерна до отримання замовлення.\n\nУсі товари, лоти й історія бренду є демонстраційними. Реальних продажів тут немає.",
    ),
    "delivery": (
        "Доставка та оплата",
        "Демонстраційна доставка лише Україною: у відділення, поштомат або кур’єром Нової пошти. Оберіть місто та локацію з навчального довідника під час оформлення.\n\nТарифи вигадані: відділення від 75 грн, поштомат від 65 грн, кур’єр від 110 грн. Кожен додатковий кілограм понад перший додає 15 грн. Від 1500 грн після знижок доставка безкоштовна. Актуальний для демо підсумок показано перед підтвердженням.\n\nПісляплата: демонстраційна комісія 20 грн + 2% вартості товарів після знижок. Вона показується окремо. Онлайн-оплата включає доставку в суму замовлення; післяплату й доставку при цьому способі сплачують при отриманні.\n\nСамовивіз безкоштовний: Одеса, центр, вул. Кавових Мрій, 7 — вигадана адреса. Після готовності замовлення чекає 3 дні. Оплата онлайн, готівкою або картою на місці. Усі платежі симулюються, реальні кошти не списуються.",
    ),
    "contacts": (
        "Зустрінемось в Одесі",
        "Демо-точка самовивозу: Одеса, центр, вул. Кавових Мрій, 7. Адреса вигадана, фізичного магазину тут немає. Не використовуйте її для поїздок або відправлень.\n\nДемонстраційний графік: щодня 10:00–19:00.\n\nПроєкт портфоліо Axon. Усі контакти та замовлення навчальні; реальна підтримка магазину не надається.",
    ),
    "returns": (
        "Скасування та повернення",
        "Це опис роботи демонстраційного магазину, а не умови реальних продажів.\n\nПоки замовлення не відправлено, його можна скасувати зі сторінки замовлення. Зарезервований або виділений товар повертається до доступного залишку. Для оплаченої покупки створюється повне демонстраційне повернення коштів.\n\nЯкщо посилку не отримали, менеджер може підтвердити повернення від перевізника. Видача, повернення та рух товару зберігаються в історії.\n\nПеред реальним запуском продавець має затвердити застосовні умови повернення й обслуговування.",
    ),
    "privacy": (
        "Ваші дані в демо",
        "Проєкт є демонстраційним. Використовуйте вигадані контактні дані, не вводьте справжні карткові реквізити.\n\nДля показу оформлення зберігаються введені ім’я, email, телефон, адреса, склад і статуси замовлення. Дані передаються лише локальним симуляторам та локальному поштовому переглядачу, а не платіжним або логістичним сервісам.\n\nСесійні cookies потрібні для кошика, входу й захисту форм. Рекламної аналітики немає. Гостьове посилання на замовлення діє 7 днів; не передавайте його стороннім.\n\nПеред реальними продажами потрібні затверджені документи, строки зберігання, процеси доступу/видалення даних та реквізити продавця.",
    ),
    "terms": (
        "Умови демонстрації",
        "Це навчальний магазин Axon з вигаданим брендом, товарами, адресами та тарифами. Оформлення не створює реальної покупки, оплата не списує гроші, доставка не створює відправлення в Новій пошті.\n\nНе вводьте справжні карткові реквізити. Для контактів використовуйте синтетичні дані. Демонстраційні замовлення можуть бути видалені під час відновлення середовища.\n\nФункції призначені для перевірки користувацького сценарію й можливостей магазину.",
    ),
}


class Command(BaseCommand):
    help = "Create synthetic demo catalog and least-privilege staff groups; preserve existing data."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("Demo seeding is disabled in live mode")
        methods = {}
        for slug, name in [
            ("espresso", "Еспресо"),
            ("filter", "Фільтр"),
            ("moka", "Гейзер"),
            ("french", "Френч-прес"),
            ("turkish", "Турка"),
        ]:
            methods[slug], _ = BrewingMethod.objects.get_or_create(
                slug=slug, defaults={"name": name}
            )
        for index, (slug, name) in enumerate(
            [
                ("beans", "У зернах"),
                ("espresso", "Для еспресо"),
                ("moka", "Для гейзера"),
                ("filter", "Для фільтра"),
                ("french", "Для френч-преса"),
                ("turkish", "Для турки"),
            ]
        ):
            GrindOption.objects.get_or_create(slug=slug, defaults={"name": name, "position": index})
        for index, row in enumerate(COFFEES):
            name, slug, origin, region, roast, process, notes, price, color, brewing = row
            product, created = Product.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "origin": origin,
                    "region": region,
                    "roast": roast,
                    "process": process,
                    "notes": notes,
                    "color": color,
                    "featured": index < 4,
                    "description": f"{notes}. Виразна кава для вашого щоденного ритуалу. Оберіть фасовку та помол, а ми підготуємо замовлення. Це вигаданий демонстраційний лот бренду Плин.",
                },
            )
            if created:
                product.methods.set([methods[key] for key in brewing])
            image, _ = ProductImage.objects.get_or_create(
                product=product,
                position=0,
                defaults={
                    "static_path": "images/product-plyn-800.webp",
                    "alt": f"Демонстраційна упаковка Плин · {name}",
                },
            )
            if image.static_path == "images/product-plyn.png":
                image.static_path = "images/product-plyn-800.webp"
                image.save(update_fields=["static_path"])
            for weight, multiplier in [(250, 100), (500, 190), (1000, 360)]:
                variant, _ = ProductVariant.objects.get_or_create(
                    product=product,
                    weight=weight,
                    defaults={
                        "sku": f"PL-{slug.upper()}-{weight}",
                        "price": price * multiplier // 100,
                        "regular_price": (price + 6000) * multiplier // 100 if index == 3 else None,
                    },
                )
                StockItem.objects.get_or_create(
                    variant=variant, defaults={"on_hand": 0 if index == 11 else 25}
                )
        now = timezone.now()
        for code, kind, value, minimum, ends, uses in [
            ("PLYN10", "percent", 10, 50000, 365, 100),
            ("MORE100", "fixed", 10000, 100000, 365, 30),
            ("EXPIRED", "percent", 20, 0, -1, 100),
            ("LASTONE", "percent", 5, 0, 365, 1),
        ]:
            PromoCode.objects.get_or_create(
                code=code,
                defaults={
                    "kind": kind,
                    "value": value,
                    "minimum": minimum,
                    "starts_at": now - timedelta(days=10),
                    "ends_at": now + timedelta(days=ends),
                    "max_uses": uses,
                },
            )
        PickupLocation.objects.get_or_create(
            name="Плин · Одеса",
            defaults={
                "address": "Одеса, центр, вул. Кавових Мрій, 7",
                "hours": "Щодня 10:00–19:00",
            },
        )
        ShopSettings.objects.get_or_create(pk=1)
        for slug, (title, body) in PAGES.items():
            ContentPage.objects.get_or_create(slug=slug, defaults={"title": title, "body": body})
        manager, _ = Group.objects.get_or_create(name="Менеджер магазину")
        allowed_models = [
            "product",
            "productvariant",
            "productimage",
            "stockitem",
            "promocode",
            "order",
            "ordercomment",
            "brewingmethod",
            "grindoption",
            "pickuplocation",
            "contentpage",
        ]
        manager.permissions.set(
            Permission.objects.filter(content_type__app_label="store").filter(
                codename__in=[
                    f"{action}_{model}"
                    for model in allowed_models
                    for action in ["view", "add", "change"]
                ]
                + [
                    f"view_{model}"
                    for model in [
                        "auditlog",
                        "stockmovement",
                        "shipment",
                        "paymentattempt",
                        "paymentevent",
                        "refund",
                    ]
                ]
            )
        )
        viewer_group, _ = Group.objects.get_or_create(name="Демо · тільки перегляд")
        viewer_group.permissions.set(
            Permission.objects.filter(
                content_type__app_label="store",
                codename__in=[
                    f"view_{model}"
                    for model in [
                        "product",
                        "productvariant",
                        "productimage",
                        "stockitem",
                        "promocode",
                        "brewingmethod",
                        "grindoption",
                        "pickuplocation",
                        "contentpage",
                    ]
                ],
            )
        )
        viewer, created = User.objects.get_or_create(
            email="viewer@plyn.example",
            defaults={"is_staff": True, "email_verified": True, "first_name": "Демо · перегляд"},
        )
        if created:
            viewer.set_unusable_password()
            viewer.save(update_fields=["password"])
            viewer.groups.add(viewer_group)
        self.stdout.write(
            self.style.SUCCESS(
                "Demo ready: 12 coffees, 36 variants, 4 promo scenarios, readonly viewer. Existing catalog/stock preserved."
            )
        )
