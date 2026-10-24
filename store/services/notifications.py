from typing import Protocol

from django.core.mail import send_mail
from django.utils import timezone

from store.models import Notification, OutboxEvent


class NotificationProvider(Protocol):
    def send(self, notification: Notification) -> None: ...


class DemoEmailProvider:
    def send(self, notification):
        send_mail(notification.subject, notification.body, None, [notification.recipient])


def queue_mail(key, recipient, subject, body):
    notification, _ = Notification.objects.get_or_create(
        key=key, defaults={"recipient": recipient, "subject": subject, "body": body}
    )
    OutboxEvent.objects.get_or_create(
        key="mail:" + key,
        defaults={
            "topic": "mail",
            "payload": {"notification_id": notification.pk},
            "available_at": timezone.now(),
        },
    )


def queue_event(key, topic, payload, available_at=None):
    return OutboxEvent.objects.get_or_create(
        key=key,
        defaults={
            "topic": topic,
            "payload": payload,
            "available_at": available_at or timezone.now(),
        },
    )[0]
