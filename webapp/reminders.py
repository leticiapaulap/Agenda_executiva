"""Alert scheduling helpers; database timestamps are naive UTC and display uses São Paulo."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


SAO_PAULO = ZoneInfo('America/Sao_Paulo')
UTC = timezone.utc
MANUAL_UPCOMING_DAYS = 7


def local_now():
    return datetime.now(SAO_PAULO)


def to_utc_naive(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=SAO_PAULO)
    return value.astimezone(UTC).replace(tzinfo=None)


def from_utc_naive(value):
    return value.replace(tzinfo=UTC).astimezone(SAO_PAULO)


def event_alert(event, now):
    if event['completed'] or event['reminder_minutes'] < 0:
        return None
    start = datetime.combine(
        date.fromisoformat(event['event_date']),
        time.fromisoformat(event['event_time']),
        tzinfo=SAO_PAULO,
    )
    alert_at = start - timedelta(minutes=event['reminder_minutes'])
    if not alert_at <= now <= start:
        return None
    return alert_at


def task_alert(task, today):
    if task['completed'] or not task['due_date']:
        return None
    due = date.fromisoformat(task['due_date'])
    return due if due <= today + timedelta(days=MANUAL_UPCOMING_DAYS) else None


def bill_alert(bill, today):
    if bill['status'] == 'Paga':
        return None
    due = date.fromisoformat(bill['due_date'])
    delta = (due - today).days
    return due if delta <= MANUAL_UPCOMING_DAYS else None
