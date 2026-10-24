from getpass import getpass

from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from store.models import User


class Command(BaseCommand):
    help = "Create a local owner or manager; password is read interactively, never from arguments."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--role", choices=["owner", "manager"], default="manager")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError("Account already exists; this command does not change existing privileges")
        user = User(email=email)
        password = getpass("Password: ")
        if password != getpass("Repeat password: "):
            raise CommandError("Passwords do not match")
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        if options["role"] == "owner":
            User.objects.create_superuser(email, password)
        else:
            group = Group.objects.filter(name="Менеджер магазину").first()
            if not group:
                raise CommandError("Run seed_demo first to create the manager group")
            user = User.objects.create_user(email, password, is_staff=True, email_verified=True)
            user.groups.add(group)
        self.stdout.write(self.style.SUCCESS(f"Created {options['role']} account {email}"))
