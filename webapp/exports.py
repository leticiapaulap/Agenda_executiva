"""Private in-memory exports for authorized agenda events."""
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event as ICalEvent, Timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)


SAO_PAULO = ZoneInfo('America/Sao_Paulo')
PRIORITY_ICONS = {'Urgente': 1, 'Alta': 3, 'Normal': 5, 'Baixa': 7}
PRIORITY_COLORS = {
    'Urgente': '#c4584e', 'Alta': '#bd8424', 'Normal': '#5074ad', 'Baixa': '#4f8e65',
}


def export_pdf(events, start, end, issued_at, manager_name=''):
    output = BytesIO()
    styles = getSampleStyleSheet()
    font_dir = Path(__import__('reportlab').__file__).parent / 'fonts'
    if 'AgendaVera' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('AgendaVera', str(font_dir / 'Vera.ttf')))
        pdfmetrics.registerFont(TTFont('AgendaVera-Bold', str(font_dir / 'VeraBd.ttf')))
    title_style = ParagraphStyle(
        'AgendaTitle', parent=styles['Title'], fontName='AgendaVera-Bold',
        fontSize=20, leading=24, textColor=colors.HexColor('#10233f'), alignment=TA_LEFT,
    )
    meta_style = ParagraphStyle(
        'AgendaMeta', parent=styles['Normal'], fontName='AgendaVera',
        fontSize=9, leading=13, textColor=colors.HexColor('#627187'),
    )
    cell_style = ParagraphStyle(
        'AgendaCell', parent=styles['Normal'], fontName='AgendaVera',
        fontSize=8, leading=11, textColor=colors.HexColor('#293b53'),
    )
    story = [
        Paragraph('Agenda Executiva', title_style),
        Spacer(1, 3 * mm),
        Paragraph(
            f'Período: {start.strftime("%d/%m/%Y")} a {end.strftime("%d/%m/%Y")}'
            + (f' · Gestor: {xml_escape(manager_name)}' if manager_name else '')
            + f' · Emitido em {issued_at.strftime("%d/%m/%Y às %H:%M")}',
            meta_style,
        ),
        Spacer(1, 7 * mm),
    ]
    rows = [[
        Paragraph('<b>Data e horário</b>', cell_style),
        Paragraph('<b>Compromisso</b>', cell_style),
        Paragraph('<b>Categoria</b>', cell_style),
        Paragraph('<b>Local</b>', cell_style),
        Paragraph('<b>Prioridade</b>', cell_style),
    ]]
    for event in events:
        day = date.fromisoformat(event['event_date'])
        time_range = event['event_time']
        if event['end_time']:
            time_range += f"–{event['end_time']}"
        rows.append([
            Paragraph(f"{day.strftime('%d/%m/%Y')}<br/>{time_range}", cell_style),
            Paragraph(xml_escape(event['title']), cell_style),
            Paragraph(xml_escape(event['category']), cell_style),
            Paragraph(xml_escape(event['location'] or '—'), cell_style),
            Paragraph(
                f'<font color="{PRIORITY_COLORS.get(event["priority"], "#67758a")}">●</font> '
                f'{xml_escape(event["priority"])}',
                cell_style,
            ),
        ])
    if len(rows) == 1:
        rows.append([Paragraph('Nenhum compromisso no período selecionado.', cell_style),
                     '', '', '', ''])
    doc = SimpleDocTemplate(
        output, pagesize=landscape(A4), rightMargin=15 * mm, leftMargin=15 * mm,
        topMargin=23 * mm, bottomMargin=19 * mm, title='Agenda Executiva',
        author='Agenda Executiva',
    )
    table = Table(rows, colWidths=[35 * mm, 78 * mm, 38 * mm, 58 * mm, 30 * mm],
                  repeatRows=1, splitByRow=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#edf2f8')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#243b59')),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#dce3ec')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1),
         [colors.white, colors.HexColor('#f8fafc')]),
    ]))
    story.append(table)

    def page_chrome(canvas, document):
        canvas.saveState()
        width, height = landscape(A4)
        canvas.setFillColor(colors.HexColor('#10233f'))
        canvas.setFont('AgendaVera-Bold', 8)
        canvas.drawString(15 * mm, height - 12 * mm, 'AGENDA EXECUTIVA')
        canvas.setStrokeColor(colors.HexColor('#dce3ec'))
        canvas.line(15 * mm, height - 15 * mm, width - 15 * mm, height - 15 * mm)
        canvas.setFillColor(colors.HexColor('#7c8999'))
        canvas.setFont('AgendaVera', 8)
        canvas.drawString(15 * mm, 10 * mm, 'Documento privado · Agenda Executiva')
        canvas.drawRightString(width - 15 * mm, 10 * mm, f'Página {document.page}')
        canvas.restoreState()

    doc.build(story, onFirstPage=page_chrome, onLaterPages=page_chrome)
    output.seek(0)
    return output


def _spreadsheet_text(value):
    text = str(value or '')
    if text.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')):
        text = "'" + text
    return text


def export_xlsx(events):
    output = BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Compromissos'
    headers = ('Data', 'Horário inicial', 'Horário final', 'Compromisso',
               'Categoria', 'Local', 'Prioridade', 'Situação')
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='10233F')
        cell.alignment = Alignment(vertical='center')
    for event in events:
        row = [
            date.fromisoformat(event['event_date']),
            time.fromisoformat(event['event_time']),
            time.fromisoformat(event['end_time']) if event['end_time'] else None,
            _spreadsheet_text(event['title']),
            _spreadsheet_text(event['category']),
            _spreadsheet_text(event['location']),
            _spreadsheet_text(event['priority']),
            'Concluído' if event['completed'] else 'Pendente',
        ]
        sheet.append(row)
        for cell in sheet[sheet.max_row]:
            cell.alignment = Alignment(vertical='top')
            if cell.column >= 4:
                cell.data_type = 's'
        sheet.cell(sheet.max_row, 1).number_format = 'dd/mm/yyyy'
        sheet.cell(sheet.max_row, 2).number_format = 'hh:mm'
        sheet.cell(sheet.max_row, 3).number_format = 'hh:mm'
    widths = (14, 18, 18, 42, 24, 32, 16, 16)
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    sheet.row_dimensions[1].height = 25
    workbook.save(output)
    output.seek(0)
    return output


def export_ics(events, issued_at, start=None, end=None):
    calendar = Calendar()
    calendar.add('prodid', '-//Agenda Executiva//Compromissos//PT-BR')
    calendar.add('version', '2.0')
    calendar.add('calscale', 'GREGORIAN')
    calendar.add('x-wr-timezone', 'America/Sao_Paulo')
    calendar.add('method', 'PUBLISH')
    zone_start = start or issued_at.date()
    zone_end = (end or issued_at.date()) + timedelta(days=1)
    calendar.add_component(Timezone.from_tzinfo(
        SAO_PAULO, tzid='America/Sao_Paulo', first_date=zone_start, last_date=zone_end,
    ))
    for row in events:
        event = ICalEvent()
        event.add('uid', f"event-{row['id']}@agenda-executiva")
        event.add('dtstamp', issued_at.astimezone(timezone.utc))
        start = datetime.combine(
            date.fromisoformat(row['event_date']), time.fromisoformat(row['event_time']),
            tzinfo=SAO_PAULO,
        )
        event.add('dtstart', start)
        if row['end_time']:
            event.add('dtend', datetime.combine(
                start.date(), time.fromisoformat(row['end_time']), tzinfo=SAO_PAULO,
            ))
        event.add('summary', row['title'])
        if row['location']:
            event.add('location', row['location'])
        event.add('categories', row['category'])
        event.add('priority', PRIORITY_ICONS.get(row['priority'], 5))
        event.add('status', 'CONFIRMED')
        event.add('x-agenda-status', 'CONCLUIDO' if row['completed'] else 'PENDENTE')
        calendar.add_component(event)
    return BytesIO(calendar.to_ical())


def resolve_period(values, today):
    period = values.get('period', 'month')
    if period == 'today':
        return today, today
    if period == 'week':
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if period == 'month':
        start = today.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, next_month - timedelta(days=1)
    if period != 'custom':
        raise ValueError('Selecione um período válido.')
    try:
        start = date.fromisoformat(values.get('start_date', ''))
        end = date.fromisoformat(values.get('end_date', ''))
    except ValueError as exc:
        raise ValueError('Informe datas válidas para o intervalo personalizado.') from exc
    if end < start or (end - start).days > 365:
        raise ValueError('O intervalo deve ser válido e ter no máximo 366 dias.')
    return start, end
