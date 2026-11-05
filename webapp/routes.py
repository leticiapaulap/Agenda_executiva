from calendar import Calendar
from datetime import date, datetime, time as datetime_time, timedelta
from urllib.parse import urlparse
import time

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import delete, func, select

from agenda.services import AgendaService, CATEGORIES, MONTHS, PRIORITIES, REMINDERS
from agenda.finance import FinanceService
from .auth import authenticate, bootstrap_legacy_admin, login_attempt_for
from .finance_repository import WebFinanceRepository
from .models import (Event, LoginAttempt, ManualReminder, ReminderState, Task,
                     User, db, utc_now)
from .permissions import (can_edit_record, can_read_finance, can_write_agenda,
                         can_edit_task, can_write_finance, require_agenda_read,
                         require_task_write)
from .repository import PostgresRepository
from .reminders import (SAO_PAULO, bill_alert, event_alert, from_utc_naive,
                       local_now, task_alert, to_utc_naive)


bp = Blueprint('web', __name__)


def _service():
    return AgendaService(PostgresRepository())


def _safe_next(target):
    if not target:
        return None
    parsed = urlparse(target)
    if (parsed.scheme or parsed.netloc or not target.startswith('/')
            or target.startswith('//') or '\\' in target):
        return None
    return target


def _default_destination(user):
    if user.can_view_agenda:
        return url_for('web.dashboard')
    if user.can_view_finance:
        return url_for('finance.bills')
    if user.can_access_vault:
        return url_for('catalog.entries')
    return url_for('web.login')


def _event_form_values(event=None):
    event = event or {}
    return {
        'title': event.get('title', ''),
        'description': event.get('description', ''),
        'event_date': event.get('event_date', date.today().isoformat()),
        'event_time': event.get('event_time', '09:00'),
        'end_time': event.get('end_time', ''),
        'category': event.get('category', 'Compromisso'),
        'priority': event.get('priority', 'Normal'),
        'location': event.get('location', ''),
        'notes': event.get('notes', ''),
        'reminder_minutes': str(event.get('reminder_minutes', 15)),
    }


def _save_event(event_id=None, initial=None):
    data = request.form.to_dict()
    try:
        limits = {'title': 200, 'category': 80, 'location': 200}
        for field, limit in limits.items():
            if len(data.get(field, '').strip()) > limit:
                raise ValueError(f'O campo {field} excede o limite de {limit} caracteres.')
        for field in ('description', 'notes'):
            if len(data.get(field, '')) > 5000:
                raise ValueError(f'O campo {field} não pode ultrapassar 5.000 caracteres.')
        service = _service()
        conflicts = service.conflicts(data, exclude_id=event_id)
        service.save_event(data, event_id=event_id)
        db.session.commit()
        flash('Compromisso salvo com sucesso.', 'success')
        if conflicts:
            names = ', '.join(event['title'] for event in conflicts[:3])
            flash(f'Atenção: há conflito de horário com {names}.', 'warning')
        return redirect(url_for('web.events'))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Falha ao salvar compromisso id=%s', event_id)
        flash('Não foi possível salvar o compromisso. O erro foi registrado.', 'error')
    return render_template('event_form.html', event=_event_form_values({**(initial or {}), **data}),
                           priorities=PRIORITIES, categories=CATEGORIES,
                           reminders=REMINDERS, editing=event_id is not None), 400


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(_default_destination(current_user))
    if request.method == 'POST':
        bootstrap_legacy_admin()
        now = int(time.time())
        client_key = (request.remote_addr or 'unknown')[:64]
        attempt = login_attempt_for(client_key, now)
        if attempt and attempt.locked_until > now:
            db.session.commit()
            flash('Muitas tentativas de acesso. Aguarde 15 minutos e tente novamente.', 'error')
            return render_template('login.html'), 429
        if attempt and now - attempt.window_started_at >= 900:
            attempt.failures = 0
            attempt.window_started_at = now
            attempt.locked_until = 0
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = authenticate(username, password)
        if user is not None:
            db.session.delete(attempt)
            db.session.commit()
            session.clear()
            session.permanent = True
            login_user(user, remember=False, fresh=True)
            target = _safe_next(request.args.get('next'))
            return redirect(target or _default_destination(user))
        db.session.execute(delete(LoginAttempt).where(
            LoginAttempt.window_started_at < now - 86400,
            LoginAttempt.client_key != client_key,
        ))
        attempt.failures += 1
        if attempt.failures >= 5:
            attempt.locked_until = now + 900
        db.session.commit()
        if attempt.locked_until > now:
            flash('Muitas tentativas de acesso. Aguarde 15 minutos e tente novamente.', 'error')
            return render_template('login.html'), 429
        flash('Usuário ou senha inválidos.', 'error')
    return render_template('login.html')


@bp.post('/logout')
@login_required
def logout():
    logout_user()
    flash('Sessão encerrada.', 'info')
    return redirect(url_for('web.login'))


@bp.get('/')
def dashboard():
    today = date.today()
    repo = PostgresRepository()
    upcoming = repo.list_events(start=today.isoformat(), status='Pendentes')[:5]
    pending_tasks = repo.list_tasks(status='Pendentes')[:5]
    today_events = repo.list_events(start=today.isoformat(), end=today.isoformat())
    counts = {
        'today': len(today_events),
        'pending_events': len(repo.list_events(status='Pendentes')),
        'pending_tasks': len(repo.list_tasks(status='Pendentes')),
        'urgent': db.session.scalar(select(func.count(Event.id)).where(
            Event.completed == 0, Event.priority == 'Urgente')) or 0,
    }
    return render_template('dashboard.html', today=today, upcoming=upcoming,
                           tasks=pending_tasks, counts=counts)


@bp.route('/events', methods=['GET', 'POST'])
def events():
    if request.method == 'POST':
        if not can_write_agenda():
            abort(403)
        return _save_event()
    repo = PostgresRepository()
    query = request.args.get('q', '').strip()[:100]
    status = request.args.get('status', 'Todos')
    priority = request.args.get('priority', 'Todas')
    selected_date = request.args.get('date', '').strip()
    if selected_date:
        try:
            selected_date = date.fromisoformat(selected_date).isoformat()
        except ValueError:
            abort(400)
    rows = repo.list_events(search=query, status=status, priority=priority,
                            start=selected_date or None, end=selected_date or None)
    return render_template('events.html', events=rows, query=query, status=status,
                           priority=priority, priorities=PRIORITIES, selected_date=selected_date)


@bp.route('/events/new', methods=['GET', 'POST'])
def new_event():
    if not can_write_agenda():
        abort(403)
    if request.method == 'POST':
        return _save_event()
    requested_date = request.args.get('date')
    if requested_date:
        try:
            requested_date = date.fromisoformat(requested_date).isoformat()
        except ValueError:
            abort(400)
    return render_template('event_form.html', event=_event_form_values(
                               {'event_date': requested_date or date.today().isoformat()}),
                           priorities=PRIORITIES, categories=CATEGORIES,
                           reminders=REMINDERS, editing=False)


@bp.route('/events/<int:event_id>/edit', methods=['GET', 'POST'])
def edit_event(event_id):
    event = PostgresRepository().get_event(event_id)
    if event is None:
        abort(404)
    if not can_edit_record(event):
        abort(403)
    if request.method == 'POST':
        return _save_event(event_id=event_id, initial=event)
    return render_template('event_form.html', event=_event_form_values(event),
                           priorities=PRIORITIES, categories=CATEGORIES,
                           reminders=REMINDERS, editing=True)


@bp.post('/events/<int:event_id>/delete')
def delete_event(event_id):
    event = PostgresRepository().get_event(event_id)
    if event is None:
        abort(404)
    if not can_edit_record(event):
        abort(403)
    try:
        PostgresRepository().delete_event(event_id)
        db.session.commit()
        flash('Compromisso excluído.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Falha ao excluir compromisso id=%s', event_id)
        flash('Não foi possível excluir o compromisso. O erro foi registrado.', 'error')
    return redirect(request.referrer if _safe_next(request.referrer) else url_for('web.events'))


@bp.post('/events/<int:event_id>/toggle')
def toggle_event(event_id):
    try:
        PostgresRepository().toggle_event(event_id)
        db.session.commit()
        flash('Situação do compromisso atualizada.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    return redirect(request.referrer if _safe_next(request.referrer) else url_for('web.events'))


@bp.post('/events/<int:event_id>/reschedule')
def reschedule_event(event_id):
    event = PostgresRepository().get_event(event_id)
    if event is None:
        abort(404)
    data = {**event, **request.form}
    try:
        service = _service()
        conflicts = service.conflicts(data, exclude_id=event_id)
        service.save_event(data, event_id=event_id)
        db.session.commit()
        flash('Compromisso reagendado.', 'success')
        if conflicts:
            flash(f'Atenção: há conflito de horário com {conflicts[0]["title"]}.', 'warning')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    return redirect(url_for('web.events'))


@bp.post('/events/<int:event_id>/duplicate')
def duplicate_event(event_id):
    event = PostgresRepository().get_event(event_id)
    if event is None:
        abort(404)
    if not can_edit_record(event):
        abort(403)
    duplicate = {**event, 'title': f"{event['title']} (cópia)", 'completed': 0}
    try:
        _service().save_event(duplicate)
        db.session.commit()
        flash('Cópia do compromisso criada.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    return redirect(url_for('web.events'))


@bp.get('/calendar')
def calendar():
    if not (current_user.can_view_agenda or can_read_finance()):
        abort(403)
    try:
        today = local_now().date()
        month = date.fromisoformat(request.args.get('month', today.replace(day=1).isoformat()))
        month = month.replace(day=1)
        if not 1900 <= month.year <= 2199:
            abort(400)
    except ValueError:
        abort(400)
    first = month
    last = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    events_by_day = {}
    tasks_by_day = {}
    if current_user.can_view_agenda:
        repo = PostgresRepository()
        for event in repo.list_events(start=first.isoformat(), end=last.isoformat()):
            events_by_day.setdefault(event['event_date'], []).append(
                {'type': 'event', 'time': event['event_time'], 'title': event['title'],
                 'priority': event['priority'], 'url': url_for(
                     'web.edit_event', event_id=event['id']) if can_edit_record(event)
                     else url_for('web.event_details', event_id=event['id'])})
        for task in repo.list_tasks(status='Todos'):
            if task['due_date'] and first.isoformat() <= task['due_date'] <= last.isoformat():
                tasks_by_day.setdefault(task['due_date'], []).append(
                    {'type': 'task', 'time': '', 'title': task['title'],
                     'priority': task['priority'], 'url': url_for(
                         'web.tasks', edit=task['id']) if can_edit_task(task)
                         else url_for('web.task_details', task_id=task['id'])})
    bills_by_day = {}
    if can_read_finance():
        try:
            calendar_bills = FinanceService(WebFinanceRepository()).calendar_items(
                first, last, today=today)
        except ValueError:
            abort(400)
    else:
        calendar_bills = []
    for bill in calendar_bills:
        bills_by_day.setdefault(bill['due_date'], []).append(
            {'type': 'bill', 'time': '', 'title': bill['title'],
             'priority': 'Alta' if bill['status'] == 'Vencida' else 'Normal',
             'url': url_for('finance.edit_bill', bill_id=bill['id']),
             'amount': bill['amount']})
    items_by_day = {}
    for collection in (events_by_day, tasks_by_day, bills_by_day):
        for day_key, items in collection.items():
            items_by_day.setdefault(day_key, []).extend(items)
    for items in items_by_day.values():
        items.sort(key=lambda item: (item['time'], item['type'], item['title'].casefold()))
    weeks = Calendar(firstweekday=0).monthdayscalendar(month.year, month.month)
    previous = (month - timedelta(days=1)).replace(day=1)
    next_month = (last + timedelta(days=1)).replace(day=1)
    return render_template('calendar.html', month=month, month_name=MONTHS[month.month - 1],
                           weeks=weeks,
                           items_by_day=items_by_day,
                           today=today,
                           bills_by_day=bills_by_day,
                           previous=previous, next_month=next_month)


@bp.route('/tasks', methods=['GET', 'POST'])
def tasks():
    task = None
    if request.method == 'POST':
        require_task_write()
        task_id = request.form.get('task_id', '').strip()
        try:
            if len(request.form.get('title', '').strip()) > 200:
                raise ValueError('O título da tarefa não pode ultrapassar 200 caracteres.')
            if len(request.form.get('description', '')) > 5000:
                raise ValueError('A descrição da tarefa não pode ultrapassar 5.000 caracteres.')
            _service().save_task(request.form.to_dict(), task_id=int(task_id) if task_id else None)
            db.session.commit()
            flash('Tarefa salva com sucesso.', 'success')
            return redirect(url_for('web.tasks'))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            task = {**request.form.to_dict(), 'id': task_id}
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Falha ao salvar tarefa')
            flash('Não foi possível salvar a tarefa. O erro foi registrado.', 'error')
            task = {**request.form.to_dict(), 'id': task_id}
    if task is None:
        task_id = request.args.get('edit', type=int)
        task = PostgresRepository().get_task(task_id) if task_id else None
        if task_id and task is None:
            abort(404)
    query = request.args.get('q', '').strip()[:100]
    status = request.args.get('status', 'Pendentes')
    priority = request.args.get('priority', 'Todas')
    rows = PostgresRepository().list_tasks(query, status, priority)
    return render_template('tasks.html', tasks=rows, task=task or {
        'title': '', 'description': '', 'due_date': '', 'priority': 'Normal'},
        query=query, status=status, priority=priority, priorities=PRIORITIES)


@bp.post('/tasks/<int:task_id>/toggle')
def toggle_task(task_id):
    try:
        PostgresRepository().toggle_task(task_id)
        db.session.commit()
        flash('Situação da tarefa atualizada.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    return redirect(request.referrer if _safe_next(request.referrer) else url_for('web.tasks'))


@bp.post('/tasks/<int:task_id>/delete')
def delete_task(task_id):
    if current_user.role != 'admin':
        abort(403)
    try:
        PostgresRepository().delete_task(task_id)
        db.session.commit()
        flash('Tarefa excluída.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    return redirect(url_for('web.tasks'))


@bp.get('/reminders')
def reminders():
    if not (current_user.can_view_agenda or can_read_finance()):
        abort(403)
    now = local_now()
    today = now.date()
    selected_filter = request.args.get('filter', 'all')
    filter_values = {'all', 'today', 'upcoming', 'overdue', 'completed'}
    if selected_filter not in filter_values:
        abort(400)
    alerts = []
    repo = PostgresRepository()
    if current_user.can_view_agenda:
        for event in repo.list_events(status='Todos'):
            item_key = f"{event['id']}:{event['event_date']}T{event['event_time']}"
            alert_at = event_alert(event, now)
            if alert_at is None and not (selected_filter == 'completed' and event['completed']):
                continue
            due_date = date.fromisoformat(event['event_date'])
            alerts.append({
                'type': 'event', 'item_id': item_key, 'title': event['title'],
                'priority': event['priority'], 'due_at': alert_at or datetime.combine(
                    due_date, datetime_time.fromisoformat(event['event_time']), SAO_PAULO),
                'due_date': due_date, 'completed': bool(event['completed']),
                'url': url_for('web.event_details', event_id=event['id']),
                'actions': can_edit_record(event),
            })
        for task in repo.list_tasks(status='Todos'):
            task_due = task_alert(task, today)
            if task_due is None and not (selected_filter == 'completed' and task['completed']):
                continue
            due_at = datetime.combine(task_due or today, datetime_time(9), SAO_PAULO)
            alerts.append({
                'type': 'task', 'item_id': str(task['id']), 'title': task['title'],
                'priority': task['priority'], 'due_at': due_at,
                'due_date': task_due or today, 'completed': bool(task['completed']),
                'url': url_for('web.task_details', task_id=task['id']),
                'actions': can_edit_task(task),
            })
    states = {
        (state.item_type, state.item_id): state
        for state in db.session.scalars(select(ReminderState).where(
            ReminderState.user_id == current_user.id)).all()
    }
    if can_read_finance():
        through = min(today + timedelta(days=7), date(2199, 12, 31))
        bills = FinanceService(WebFinanceRepository()).list_bills(
            due_start='1900-01-01', due_end=through.isoformat(), today=today)
        for bill in bills:
            bill_due = bill_alert(bill, today)
            if bill_due is not None:
                alerts.append({
                    'type': 'bill', 'item_id': bill['id'], 'title': bill['title'],
                    'priority': 'Urgente' if bill['status'] == 'Vencida' else 'Alta',
                    'amount': bill['amount'],
                    'due_at': datetime.combine(bill_due, datetime_time(9), SAO_PAULO),
                    'due_date': bill_due, 'completed': False,
                    'url': url_for('finance.edit_bill', bill_id=bill['id']),
                    'actions': can_write_finance(),
                })
    manual_rows = db.session.scalars(select(ManualReminder).where(
        ManualReminder.owner_id == current_user.id)).all()
    for reminder in manual_rows:
        due_at = from_utc_naive(reminder.due_at)
        if not (reminder.completed or due_at.date() <= today + timedelta(days=7)):
            continue
        alerts.append({
            'type': 'manual', 'item_id': reminder.id, 'title': reminder.title,
            'priority': reminder.priority, 'due_at': due_at, 'due_date': due_at.date(),
            'completed': reminder.completed, 'url': '',
            'actions': True, 'is_read': reminder.is_read,
            'snoozed_until': from_utc_naive(reminder.snoozed_until)
            if reminder.snoozed_until else None,
        })
    visible = []
    for alert in alerts:
        state = states.get((alert['type'], alert['item_id']))
        alert['is_read'] = alert.get('is_read', state.is_read if state else False)
        alert['snoozed_until'] = alert.get(
            'snoozed_until', from_utc_naive(state.snoozed_until)
            if state and state.snoozed_until else None)
        if alert['snoozed_until'] and alert['snoozed_until'] > now:
            continue
        if selected_filter == 'completed':
            if not alert['completed']:
                continue
        elif alert['completed']:
            continue
        elif selected_filter == 'today' and alert['due_date'] != today:
            continue
        elif selected_filter == 'upcoming' and not today < alert['due_date'] <= today + timedelta(days=7):
            continue
        elif selected_filter == 'overdue' and alert['due_date'] >= today:
            continue
        alert['bucket'] = ('completed' if alert['completed'] else
                           'overdue' if alert['due_date'] < today else
                           'today' if alert['due_date'] == today else 'upcoming')
        visible.append(alert)
    visible.sort(key=lambda alert: (alert['due_at'], alert['priority'], alert['title'].casefold()))
    return render_template('reminders.html', alerts=visible, today=today,
                           selected_filter=selected_filter,
                           active_count=sum(not alert['completed'] for alert in visible),
                           can_add_reminder=current_user.can_view_agenda,
                           priorities=PRIORITIES)


@bp.post('/reminders/manual')
def create_manual_reminder():
    require_agenda_read()
    title = request.form.get('title', '').strip()
    notes = request.form.get('notes', '').strip()
    priority = request.form.get('priority', 'Normal')
    try:
        if not title or len(title) > 200 or len(notes) > 5000:
            raise ValueError('Informe um título válido e observações de até 5.000 caracteres.')
        if priority not in PRIORITIES:
            raise ValueError('Selecione uma prioridade válida.')
        due_at = datetime.fromisoformat(request.form.get('due_at', ''))
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=SAO_PAULO)
        reminder = ManualReminder(
            owner_id=current_user.id, title=title, notes=notes,
            due_at=to_utc_naive(due_at), priority=priority)
        db.session.add(reminder)
        db.session.commit()
        flash('Lembrete criado com sucesso.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Falha ao criar lembrete manual')
        flash('Não foi possível criar o lembrete. O erro foi registrado.', 'error')
    return redirect(url_for('web.reminders'))


@bp.post('/reminders/<item_type>/<item_id>/<action>')
def update_reminder(item_type, item_id, action):
    if item_type == 'manual':
        reminder = db.session.get(ManualReminder, item_id)
        if not reminder or reminder.owner_id != current_user.id:
            abort(404)
        if action == 'delete':
            db.session.delete(reminder)
        elif action == 'complete':
            reminder.completed = True
            reminder.snoozed_until = None
        elif action == 'read':
            reminder.is_read = True
        elif action == 'snooze':
            reminder.snoozed_until = to_utc_naive(local_now() + timedelta(days=1))
        else:
            abort(404)
    else:
        if item_type not in ('event', 'task', 'bill') or action not in (
                'read', 'snooze', 'complete'):
            abort(404)
        if item_type in ('event', 'task') and not current_user.can_view_agenda:
            abort(403)
        if item_type == 'bill' and not can_read_finance():
            abort(403)
        item_key = item_id
        record = None
        if item_type == 'event':
            try:
                event_id = int(item_id.split(':', 1)[0])
            except ValueError:
                abort(404)
            record = PostgresRepository().get_event(event_id)
            if not record or item_id != f"{event_id}:{record['event_date']}T{record['event_time']}":
                abort(404)
            if action == 'complete':
                if not can_edit_record(record):
                    abort(403)
                if record['completed']:
                    abort(409)
        elif item_type == 'task':
            record = PostgresRepository().get_task(int(item_id)) if item_id.isdigit() else None
            if not record:
                abort(404)
            if action == 'complete':
                require_task_write()
                if record['completed']:
                    abort(409)
        elif item_type == 'bill':
            record = FinanceService(WebFinanceRepository()).get_bill(item_id)
            if not record:
                abort(404)
            if action == 'complete':
                if not can_write_finance():
                    abort(403)
                FinanceService(WebFinanceRepository()).pay_bill(
                    item_id, local_now().date().isoformat())
                db.session.commit()
                flash('Pagamento registrado. O alerta foi concluído.', 'success')
                return redirect(url_for('web.reminders'))
        state = db.session.scalar(select(ReminderState).where(
            ReminderState.user_id == current_user.id,
            ReminderState.item_type == item_type,
            ReminderState.item_id == item_key))
        if state is None:
            state = ReminderState(user_id=current_user.id, item_type=item_type, item_id=item_key)
            db.session.add(state)
        if action == 'read':
            state.is_read = True
            state.read_at = utc_now()
        elif action == 'snooze':
            state.is_read = True
            state.read_at = utc_now()
            state.snoozed_until = to_utc_naive(local_now() + timedelta(days=1))
        elif item_type == 'event':
            state.snoozed_until = None
            PostgresRepository().toggle_event(record['id'])
        elif item_type == 'task':
            state.snoozed_until = None
            PostgresRepository().toggle_task(record['id'])
    db.session.commit()
    flash('Alerta atualizado.', 'success')
    return redirect(url_for('web.reminders'))


@bp.get('/calendar/date/<date_value>')
def calendar_date(date_value):
    if not (current_user.can_view_agenda or can_read_finance()):
        abort(403)
    try:
        selected = date.fromisoformat(date_value)
    except ValueError:
        abort(404)
    repo = PostgresRepository()
    events = repo.list_events(start=date_value, end=date_value) if current_user.can_view_agenda else []
    tasks = [task for task in repo.list_tasks(status='Todos')
             if task['due_date'] == date_value] if current_user.can_view_agenda else []
    bills = (FinanceService(WebFinanceRepository()).calendar_items(
        selected, selected, today=local_now().date())
             if can_read_finance() else [])
    entries = ([{'type': 'Compromisso', 'time': row['event_time'], 'title': row['title'],
                 'url': url_for('web.edit_event', event_id=row['id']) if can_edit_record(row)
                 else url_for('web.event_details', event_id=row['id'])} for row in events]
               + [{'type': 'Tarefa', 'time': '', 'title': row['title'],
                   'url': url_for('web.tasks', edit=row['id']) if can_edit_task(row)
                   else url_for('web.task_details', task_id=row['id'])} for row in tasks]
               + [{'type': 'Conta', 'time': '', 'title': f"{row['title']} · R$ {row['amount']}",
                   'url': url_for('finance.edit_bill', bill_id=row['id'])} for row in bills])
    return render_template('calendar_date.html', selected=selected, entries=entries)


@bp.get('/search')
def search():
    query = request.args.get('q', '').strip()[:100]
    if not query:
        return render_template('search.html', query=query, events=[], tasks=[])
    repo = PostgresRepository()
    return render_template('search.html', query=query,
                           events=repo.list_events(search=query, status='Todos'),
                           tasks=repo.list_tasks(search=query, status='Todos'))


@bp.get('/events/<int:event_id>')
def event_details(event_id):
    event = PostgresRepository().get_event(event_id)
    if event is None:
        abort(404)
    return render_template('record_detail.html', title=event['title'], section='Compromisso',
                           values=(('Data', event['event_date']), ('Horário', event['event_time']),
                                   ('Término', event['end_time'] or '—'), ('Categoria', event['category']),
                                   ('Local', event['location'] or '—'), ('Prioridade', event['priority']),
                                   ('Descrição', event['description'] or '—'), ('Observações', event['notes'] or '—')),
                           edit_url=url_for('web.edit_event', event_id=event_id)
                           if can_edit_record(event) else None,
                           back_url=url_for('web.events'))


@bp.get('/tasks/<int:task_id>')
def task_details(task_id):
    task = PostgresRepository().get_task(task_id)
    if task is None:
        abort(404)
    return render_template('record_detail.html', title=task['title'], section='Tarefa',
                           values=(('Prazo', task['due_date'] or 'Sem prazo'),
                                   ('Prioridade', task['priority']),
                                   ('Situação', 'Concluída' if task['completed'] else 'Pendente'),
                                   ('Descrição', task['description'] or '—')),
                           edit_url=url_for('web.tasks', edit=task_id)
                           if current_user.role in ('admin', 'manager') else None,
                           back_url=url_for('web.tasks'))
