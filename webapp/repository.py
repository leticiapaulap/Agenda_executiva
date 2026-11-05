from datetime import date

from sqlalchemy import case, select

from .models import Event, Task, db
from .permissions import (new_record_owner, require_agenda_read, require_record_write,
                         require_task_write)


def _event_dict(event):
    return {
        'id': event.id,
        'owner_id': event.owner_id,
        'title': event.title,
        'event_date': event.event_date.isoformat(),
        'event_time': event.event_time.strftime('%H:%M'),
        'category': event.category,
        'location': event.location,
        'notes': event.notes,
        'reminder_minutes': event.reminder_minutes,
        'completed': event.completed,
        'description': event.description,
        'end_time': event.end_time.strftime('%H:%M') if event.end_time else '',
        'priority': event.priority,
    }


def _task_dict(task):
    return {
        'id': task.id,
        'owner_id': task.owner_id,
        'title': task.title,
        'description': task.description,
        'due_date': task.due_date.isoformat() if task.due_date else '',
        'priority': task.priority,
        'completed': task.completed,
    }


class PostgresRepository:
    """Repositório compatível com AgendaService, usando apenas PostgreSQL na web."""

    def list_events(self, search='', start=None, end=None, status='Todos',
                    category='Todas', priority='Todas'):
        require_agenda_read()
        statement = select(Event)
        if start:
            statement = statement.where(Event.event_date >= date.fromisoformat(str(start)))
        if end:
            statement = statement.where(Event.event_date <= date.fromisoformat(str(end)))
        if status in ('Pendentes', 'Pendente'):
            statement = statement.where(Event.completed == 0)
        elif status in ('Concluídos', 'Concluídas', 'Concluído'):
            statement = statement.where(Event.completed == 1)
        if category and category != 'Todas':
            statement = statement.where(Event.category == category)
        if priority and priority != 'Todas':
            statement = statement.where(Event.priority == priority)
        rows = [_event_dict(row) for row in db.session.scalars(
            statement.order_by(Event.event_date, Event.event_time, Event.id)).all()]
        needle = search.strip().casefold()
        if needle:
            rows = [row for row in rows if any(needle in str(row[field]).casefold()
                    for field in ('title', 'description', 'notes', 'location', 'category'))]
        return rows

    def get_event(self, event_id):
        require_agenda_read()
        event = db.session.get(Event, event_id)
        return _event_dict(event) if event else None

    def save_event(self, data, id=None):
        event = db.session.get(Event, id) if id is not None else Event()
        if event is None:
            raise ValueError('O compromisso não foi encontrado. Atualize a agenda.')
        require_record_write(event if id is not None else None)
        if id is None:
            event.owner_id = new_record_owner()
        fields = ('title', 'event_date', 'event_time', 'category', 'location',
                  'notes', 'reminder_minutes', 'completed', 'description', 'end_time', 'priority')
        from datetime import date as date_type, time
        for field in fields:
            if field not in data:
                continue
            value = data[field]
            if field == 'event_date' and not isinstance(value, date_type):
                value = date_type.fromisoformat(value)
            elif field in ('event_time', 'end_time'):
                value = time.fromisoformat(value) if value else None
            setattr(event, field, value)
        db.session.add(event)
        db.session.flush()
        return event.id

    def delete_event(self, event_id):
        event = db.session.get(Event, event_id)
        if event is None:
            raise ValueError('O compromisso não foi encontrado. Atualize a agenda.')
        require_record_write(event)
        db.session.delete(event)

    def toggle_event(self, event_id):
        event = db.session.get(Event, event_id)
        if event is None:
            raise ValueError('O compromisso não foi encontrado. Atualize a agenda.')
        require_record_write(event)
        event.completed = 0 if event.completed else 1

    def list_tasks(self, search='', status='Pendentes', priority='Todas'):
        require_agenda_read()
        statement = select(Task)
        if status in ('Pendentes', 'Pendente'):
            statement = statement.where(Task.completed == 0)
        elif status in ('Concluídas', 'Concluídos', 'Concluída'):
            statement = statement.where(Task.completed == 1)
        if priority and priority != 'Todas':
            statement = statement.where(Task.priority == priority)
        ordering = case((Task.priority == 'Urgente', 0), (Task.priority == 'Alta', 1),
                        (Task.priority == 'Normal', 2), else_=3)
        rows = [_task_dict(row) for row in db.session.scalars(
            statement.order_by(Task.completed, ordering, Task.due_date, Task.id)).all()]
        needle = search.strip().casefold()
        return [row for row in rows if not needle or needle in
                (row['title'] + ' ' + row['description']).casefold()]

    def get_task(self, task_id):
        require_agenda_read()
        task = db.session.get(Task, task_id)
        return _task_dict(task) if task else None

    def save_task(self, data, id=None):
        task = db.session.get(Task, id) if id is not None else Task()
        if task is None:
            raise ValueError('A tarefa não foi encontrada. Atualize a agenda.')
        require_task_write()
        if id is None:
            task.owner_id = new_record_owner()
        from datetime import date as date_type
        for field in ('title', 'description', 'priority', 'completed'):
            if field in data:
                setattr(task, field, data[field])
        if 'due_date' in data:
            task.due_date = date_type.fromisoformat(data['due_date']) if data['due_date'] else None
        db.session.add(task)
        db.session.flush()
        return task.id

    def toggle_task(self, task_id):
        task = db.session.get(Task, task_id)
        if task is None:
            raise ValueError('A tarefa não foi encontrada. Atualize a agenda.')
        require_task_write()
        task.completed = 0 if task.completed else 1

    def delete_task(self, task_id):
        task = db.session.get(Task, task_id)
        if task is None:
            raise ValueError('A tarefa não foi encontrada. Atualize a agenda.')
        require_record_write(task)
        db.session.delete(task)
