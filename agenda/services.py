"""Validação e regras da agenda, independentes da interface desktop."""
from datetime import date, datetime, timedelta
import logging
import re


DATE_FMT = '%d/%m/%Y'
HOUR_FMT = '%H:%M'
PRIORITIES = ['Baixa', 'Normal', 'Alta', 'Urgente']
CATEGORIES = ['Compromisso', 'Reunião', 'Tarefa', 'Prazo', 'Pessoal']
REMINDERS = {
    'Sem lembrete': -1, 'Na hora': 0, '5 minutos antes': 5,
    '10 minutos antes': 10, '15 minutos antes': 15, '30 minutos antes': 30,
    '1 hora antes': 60, '1 dia antes': 1440,
}
MONTHS = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
          'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']
WEEKDAYS = ['segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira',
            'sexta-feira', 'sábado', 'domingo']


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    value = str(value).strip()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return date.fromisoformat(value)
        if re.fullmatch(r'\d{2}/\d{2}/\d{4}', value):
            return datetime.strptime(value, DATE_FMT).date()
    except ValueError:
        pass
    raise ValueError('Informe uma data válida no formato DD/MM/AAAA.')


def parse_time(value):
    value = str(value).strip()
    try:
        if re.fullmatch(r'\d{2}:\d{2}', value):
            return datetime.strptime(value, HOUR_FMT).time()
    except ValueError:
        pass
    raise ValueError('Informe um horário válido no formato HH:MM (24 horas).')


def format_date(value):
    return parse_date(value).strftime(DATE_FMT) if value else ''


def long_date(value=None):
    value = parse_date(value or date.today())
    return f'{WEEKDAYS[value.weekday()]}, {value.day} de {MONTHS[value.month - 1]} de {value.year}'


class AgendaService:
    def __init__(self, db):
        self.db = db

    def validate_event(self, data):
        result = {key: str(data.get(key, '') or '').strip()
                  for key in ('title', 'description', 'location', 'notes')}
        if not result['title']:
            raise ValueError('Informe o título do compromisso.')
        result['event_date'] = parse_date(data.get('event_date', '')).isoformat()
        start = parse_time(data.get('event_time', ''))
        result['event_time'] = start.strftime(HOUR_FMT)
        result['end_time'] = str(data.get('end_time', '') or '').strip()
        if result['end_time']:
            end = parse_time(result['end_time'])
            if end <= start:
                raise ValueError('O horário de término deve ser posterior ao horário de início, no mesmo dia.')
            result['end_time'] = end.strftime(HOUR_FMT)
        result['category'] = str(data.get('category', 'Compromisso') or 'Compromisso').strip()
        result['priority'] = data.get('priority', 'Normal')
        if result['priority'] not in PRIORITIES:
            raise ValueError('Selecione uma prioridade válida: baixa, normal, alta ou urgente.')
        try:
            result['reminder_minutes'] = int(data.get('reminder_minutes', 15))
        except (TypeError, ValueError) as exc:
            raise ValueError('Selecione um lembrete válido.') from exc
        if result['reminder_minutes'] not in REMINDERS.values():
            raise ValueError('Selecione um lembrete válido.')
        if 'completed' in data:
            try:
                completed = int(data['completed'])
            except (TypeError, ValueError) as exc:
                raise ValueError('O estado do compromisso é inválido.') from exc
            if completed not in (0, 1):
                raise ValueError('O estado do compromisso é inválido.')
            result['completed'] = completed
        return result

    def save_event(self, data, event_id=None):
        if event_id is not None:
            existing = self.db.get_event(event_id)
            if existing is None:
                raise ValueError('O compromisso não foi encontrado. Atualize a agenda.')
            data = {**existing, **data}
        return self.db.save_event(self.validate_event(data), id=event_id)

    def conflicts(self, data, exclude_id=None):
        event = self.validate_event(data)
        start = parse_time(event['event_time'])
        end = parse_time(event['end_time']) if event['end_time'] else None
        result = []
        for other in self.db.list_events(start=event['event_date'], end=event['event_date'], status='Pendentes'):
            if other['id'] == exclude_id:
                continue
            try:
                other_start = parse_time(other['event_time'])
                other_end = parse_time(other['end_time']) if other['end_time'] else None
            except ValueError:
                continue
            if end and other_end:
                overlaps = start < other_end and other_start < end
            elif end:
                overlaps = start <= other_start < end
            elif other_end:
                overlaps = other_start <= start < other_end
            else:
                overlaps = start == other_start
            if overlaps:
                result.append(other)
        return result

    def save_task(self, data, task_id=None):
        if task_id is not None:
            existing = self.db.get_task(task_id)
            if existing is None:
                raise ValueError('A tarefa não foi encontrada. Atualize a agenda.')
            data = {**existing, **data}
        title = str(data.get('title', '') or '').strip()
        if not title:
            raise ValueError('Informe o título da tarefa.')
        priority = data.get('priority', 'Normal')
        if priority not in PRIORITIES:
            raise ValueError('Selecione uma prioridade válida: baixa, normal, alta ou urgente.')
        due_date = data.get('due_date', '')
        values = {'title': title, 'description': str(data.get('description', '') or '').strip(),
                  'due_date': parse_date(due_date).isoformat() if str(due_date or '').strip() else '',
                  'priority': priority}
        if 'completed' in data:
            try:
                completed = int(data['completed'])
            except (TypeError, ValueError) as exc:
                raise ValueError('O estado da tarefa é inválido.') from exc
            if completed not in (0, 1):
                raise ValueError('O estado da tarefa é inválido.')
            values['completed'] = completed
        return self.db.save_task(values, id=task_id)

    def due_reminders(self, now=None):
        now = now or datetime.now()
        if self.db.get_settings().get('reminders_enabled') != '1':
            return []
        events = self.db.list_events(start=now.date() - timedelta(days=1),
                                    end=now.date() + timedelta(days=1), status='Pendentes')
        due = []
        for event in events:
            try:
                minutes = int(event['reminder_minutes'])
                if minutes < 0:
                    continue
                start = datetime.combine(parse_date(event['event_date']), parse_time(event['event_time']))
                notify_at = start - timedelta(minutes=minutes)
                if notify_at <= now <= start + timedelta(minutes=1) and not self.db.reminder_claimed(event):
                    due.append(event)
            except (ValueError, TypeError):
                logging.getLogger(__name__).warning('Lembrete ignorado para compromisso %s: data/horário inválido.', event['id'])
        return due

    def claim_reminder(self, event, now=None):
        return self.db.claim_reminder(event, now=now)
