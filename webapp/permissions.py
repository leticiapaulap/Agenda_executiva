"""Shared organizational data permissions, enforced independently of templates."""
from flask import abort, has_request_context
from flask_login import current_user


def _granted(flag):
    return bool(current_user.is_authenticated and getattr(current_user, flag, False))


def can_read_agenda():
    return bool(_granted('agenda_access') or
                (current_user.is_authenticated and current_user.role == 'admin'))


def can_write_agenda():
    return bool(can_read_agenda() and current_user.role in ('admin', 'manager'))


def can_read_finance():
    return bool(_granted('finance_access') or
                (current_user.is_authenticated and current_user.role == 'admin'))


def can_write_finance():
    return bool(can_read_finance() and current_user.role in ('admin', 'manager'))


def can_edit_record(record):
    if not record or not can_write_agenda():
        return False
    owner = record.get('owner_id') if isinstance(record, dict) else record.owner_id
    return current_user.role == 'admin' or owner == current_user.id


def can_edit_task(task=None):
    return bool(can_read_agenda() and current_user.role in ('admin', 'manager'))


def require_agenda_read():
    # Trusted background maintenance/tests have no HTTP request identity.
    if has_request_context() and not can_read_agenda():
        abort(403)


def require_record_write(record=None):
    if has_request_context() and not (can_edit_record(record) if record else can_write_agenda()):
        abort(403)


def require_task_write():
    if has_request_context() and not can_edit_task():
        abort(403)


def new_record_owner():
    return current_user.id if has_request_context() and current_user.is_authenticated else None
