from calendar import Calendar
from datetime import date, timedelta
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
from .models import Event, LoginAttempt, Task, db
from .permissions import (can_edit_record, can_read_finance, can_write_agenda,
                         require_task_write)
from .repository import PostgresRepository


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
    try:
        month = date.fromisoformat(request.args.get('month', date.today().replace(day=1).isoformat()))
        month = month.replace(day=1)
        if not 1900 <= month.year <= 2199:
            abort(400)
    except ValueError:
        abort(400)
    first = month
    last = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    events_by_day = {}
    for event in PostgresRepository().list_events(start=first.isoformat(), end=last.isoformat()):
        events_by_day.setdefault(event['event_date'], []).append(event)
    bills_by_day = {}
    if can_read_finance():
        try:
            calendar_bills = FinanceService(WebFinanceRepository()).calendar_items(first, last)
        except ValueError:
            abort(400)
    else:
        calendar_bills = []
    for bill in calendar_bills:
        bills_by_day.setdefault(bill['due_date'], []).append(bill)
    weeks = Calendar(firstweekday=0).monthdayscalendar(month.year, month.month)
    previous = (month - timedelta(days=1)).replace(day=1)
    next_month = (last + timedelta(days=1)).replace(day=1)
    return render_template('calendar.html', month=month, month_name=MONTHS[month.month - 1],
                           weeks=weeks,
                           events_by_day=events_by_day, today=date.today(),
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
    today = date.today()
    upcoming = PostgresRepository().list_events(start=today.isoformat(), status='Pendentes')
    pending_tasks = PostgresRepository().list_tasks(status='Pendentes')
    tasks_due = [task for task in pending_tasks if task['due_date'] and task['due_date'] <= today.isoformat()]
    bill_reminders = (FinanceService(WebFinanceRepository()).reminders(today)
                      if can_read_finance() else [])
    return render_template('reminders.html', events=upcoming, tasks=tasks_due, today=today,
                           bill_reminders=bill_reminders)


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
