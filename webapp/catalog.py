"""Directory and opt-in encrypted personal vault. Never serialize ciphertext."""
import time
from uuid import uuid4

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import login_fresh, login_required
from sqlalchemy import CheckConstraint, case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from werkzeug.security import check_password_hash

from agenda.catalog import AccessCatalog
from agenda.vault import VaultCipher, VaultError
from .models import LoginAttempt, db


class AccessEntry(db.Model):
    __tablename__ = 'access_catalog'
    __table_args__ = (
        CheckConstraint('is_corporate IN (0,1)', name='ck_access_catalog_is_corporate'),
        CheckConstraint('is_corporate=0 OR password_ciphertext IS NULL', name='ck_access_catalog_password_scope'),
    )
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(1000), nullable=False, default='')
    category = db.Column(db.String(80), nullable=False, default='')
    notes = db.Column(db.Text, nullable=False, default='')
    updated_at = db.Column(db.String(40), nullable=False)
    password_ciphertext = db.Column(db.Text, nullable=True)
    is_corporate = db.Column(db.Integer, nullable=False, default=1, server_default='1')


PUBLIC_FIELDS = ('id', 'name', 'url', 'category', 'notes', 'updated_at', 'is_corporate')


class WebCatalogRepository:
    @staticmethod
    def _public_query():
        return select(*(getattr(AccessEntry, key) for key in PUBLIC_FIELDS),
                      case((AccessEntry.password_ciphertext.is_not(None), True), else_=False).label('has_password'))

    def list_entries(self):
        return [dict(row) for row in db.session.execute(
            self._public_query().order_by(AccessEntry.name, AccessEntry.id)).mappings()]

    def get_entry(self, entry_id):
        row = db.session.execute(self._public_query().where(AccessEntry.id == entry_id)).mappings().first()
        return dict(row) if row else None

    def get_secret(self, entry_id):
        return db.session.scalar(select(AccessEntry.password_ciphertext).where(AccessEntry.id == entry_id))

    def save_entry(self, values, entry_id=None):
        if entry_id:
            row = db.session.scalar(select(AccessEntry).where(AccessEntry.id == entry_id).with_for_update()
                                    .execution_options(populate_existing=True))
            if row is None:
                raise ValueError('Acesso não encontrado.')
        else:
            row = AccessEntry(id=values.get('id') or str(uuid4()))
        for key, value in values.items():
            setattr(row, key, value)
        # Check the final policy under the row lock, including concurrent edits.
        if row.is_corporate and row.password_ciphertext:
            raise ValueError('Acessos corporativos permanecem somente no catálogo.')
        db.session.add(row)
        db.session.commit()
        return row.id

    def compare_and_swap_secret(self, entry_id, expected, replacement):
        from sqlalchemy import update
        result = db.session.execute(update(AccessEntry).where(
            AccessEntry.id == entry_id, AccessEntry.password_ciphertext == expected,
            AccessEntry.is_corporate == 0).values(password_ciphertext=replacement))
        db.session.commit()
        return result.rowcount == 1

    def delete_entry(self, entry_id):
        row = db.session.get(AccessEntry, entry_id)
        if row is None:
            raise ValueError('Acesso não encontrado.')
        db.session.delete(row)
        db.session.commit()


bp = Blueprint('catalog', __name__)


def catalog_service():
    return AccessCatalog(WebCatalogRepository(), VaultCipher(current_app.config))


def reauthenticate(password):
    """Persistent account-wide limiter. Each operation checks again."""
    if not login_fresh():
        return 401, 'Entre novamente na agenda antes de acessar a senha.'
    now = int(time.time())
    dialect = db.session.get_bind().dialect.name
    insert = pg_insert if dialect == 'postgresql' else sqlite_insert
    db.session.execute(insert(LoginAttempt).values(client_key='vault:manager', failures=0,
                        window_started_at=now, locked_until=0).on_conflict_do_nothing(index_elements=['client_key']))
    attempt = db.session.scalar(select(LoginAttempt).where(
        LoginAttempt.client_key == 'vault:manager').with_for_update().execution_options(populate_existing=True))
    if attempt.locked_until > now:
        db.session.commit()
        return 429, 'Muitas tentativas. Aguarde 15 minutos.'
    if now - attempt.window_started_at >= 900:
        attempt.failures, attempt.window_started_at, attempt.locked_until = 0, now, 0
    try:
        valid = bool(isinstance(password, str) and 0 < len(password) <= 1024 and
                     check_password_hash(current_app.config['ADMIN_PASSWORD_HASH'], password))
    except (ValueError, TypeError):
        valid = False
    if valid:
        attempt.failures, attempt.locked_until, attempt.window_started_at = 0, 0, now
        db.session.commit()
        return None
    attempt.failures += 1
    if attempt.failures >= 5:
        attempt.locked_until = now + 900
    locked = attempt.locked_until > now
    db.session.commit()
    return (429, 'Muitas tentativas. Aguarde 15 minutos.') if locked else (401, 'Reautenticação inválida.')


@bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@bp.route('/vault', methods=['GET', 'POST'])
@login_required
def entries():
    service = catalog_service()
    entry = None
    status = 200
    if request.method == 'POST':
        entry_id = request.form.get('entry_id') or None
        if entry_id and not service.get_entry(entry_id):
            abort(404)
        allowed = {'csrf_token', 'entry_id', 'name', 'url', 'category', 'notes',
                   'password', 'is_corporate', 'clear_password', 'reauth_password'}
        try:
            if set(request.form) - allowed:
                raise ValueError('O formulário contém campos não permitidos.')
            values = {key: request.form.get(key, '') for key in ('name', 'url', 'category', 'notes')}
            for key in ('password', 'is_corporate', 'clear_password'):
                if key in request.form:
                    values[key] = request.form[key]
            if values.get('password') or str(values.get('clear_password', '')).lower() in ('1', 'true'):
                if not service.cipher.available:
                    raise VaultError('O cofre está indisponível. O catálogo continua disponível.')
                failure = reauthenticate(request.form.get('reauth_password', ''))
                if failure:
                    status, message = failure
                    return _render_entries(service, status=status, message=message)
            service.save_entry(values, entry_id)
            flash('Acesso salvo.', 'success')
            return redirect(url_for('catalog.entries'))
        except ValueError as error:
            db.session.rollback()
            flash(str(error), 'error')
            status = 400
        except Exception:
            db.session.rollback()
            flash('Não foi possível salvar o acesso.', 'error')
            status = 400
    elif request.args.get('edit'):
        entry = service.get_entry(request.args['edit'])
        if entry is None:
            abort(404)
    return _render_entries(service, entry, status)


def _render_entries(service, entry=None, status=200, message=None):
    if message:
        flash(message, 'error')
    return render_template('catalog.html', entries=service.list_entries(request.args.get('q', '')),
                           entry=entry or {}, notice=service.cipher.notice,
                           vault_available=service.cipher.available), status


@bp.post('/vault/<entry_id>/secret')
@login_required
def secret(entry_id):
    """The sole HTTP operation returning plaintext, after per-operation reauth."""
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    if not isinstance(data, dict) or data.get('action') not in ('reveal', 'copy'):
        return jsonify(error='Operação inválida.'), 400
    try:
        service = catalog_service()
        entry = service.get_entry(entry_id)
        if not entry:
            return jsonify(error='Acesso não encontrado.'), 404
        if entry['is_corporate']:
            return jsonify(error='Acessos corporativos permanecem somente no catálogo.'), 403
        if not service.cipher.available:
            return jsonify(error='O cofre está indisponível. Verifique a configuração com o administrador.'), 503
        failure = reauthenticate(data.get('reauth_password', ''))
        if failure:
            status, message = failure
            return jsonify(error=message), status
        password = service.reveal_password(entry_id)
        return jsonify(password=password)
    except VaultError:
        db.session.rollback()
        return jsonify(error='Não foi possível acessar a senha. Verifique a chave e o backup do cofre.'), 503
    except Exception:
        db.session.rollback()
        return jsonify(error='Não foi possível acessar a senha.'), 503


@bp.post('/vault/<entry_id>/delete')
@login_required
def delete_entry(entry_id):
    service = catalog_service()
    if not service.get_entry(entry_id):
        abort(404)
    try:
        service.delete_entry(entry_id)
        flash('Acesso excluído.', 'success')
    except Exception:
        db.session.rollback()
        flash('Não foi possível excluir o acesso.', 'error')
    return redirect(url_for('catalog.entries'))
