from django.core.management.base import BaseCommand

from store.tasks import expire_reservations, poll_shipments, process_outbox


class Command(BaseCommand):
    help = "Process due demo events locally without a Celery broker. Safe to repeat."

    def handle(self, *args, **options):
        expire_reservations()
        poll_shipments()
        total = 0
        for _ in range(10):
            count = process_outbox()
            total += count
            if not count:
                break
        self.stdout.write(self.style.SUCCESS(f"Processed {total} due events"))
