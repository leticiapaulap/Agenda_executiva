"""Directory and authorized personal/corporate vault. Never serialize ciphertext."""
from uuid import uuid4

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import CheckConstraint, case, select

from agenda.catalog import AccessCatalog
from agenda.vault import VaultCipher, VaultError
from .auth import verify_current_password
from .models import db


class AccessEntry(db.Model):
    __tablename__ = 'access_catalog'
    __table_args__ = (
        CheckConstraint('is_corporate IN (0,1)', name='ck_access_catalog_is_corporate'),
    )
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(1000), nullable=False, default='')
    username = db.Column(db.String(254), nullable=False, default='', server_default='')
    category = db.Column(db.String(80), nullable=False, default='')
    notes = db.Column(db.Text, nullable=False, default='')
    updated_at = db.Column(db.String(40), nullable=False)
    password_ciphertext = db.Column(db.Text, nullable=True)
    is_corporate = db.Column(db.Integer, nullable=False, default=1, server_default='1')


PUBLIC_FIELDS = ('id', 'name', 'url', 'username', 'category', 'notes', 'updated_at', 'is_corporate')


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
        db.session.add(row)
        db.session.commit()
        return row.id

    def compare_and_swap_secret(self, entry_id, expected, replacement):
        from sqlalchemy import update
        result = db.session.execute(update(AccessEntry).where(
            AccessEntry.id == entry_id, AccessEntry.password_ciphertext == expected
        ).values(password_ciphertext=replacement))
        db.session.commit()
        return result.rowcount == 1

    def delete_entry(self, entry_id):
        row = db.session.get(AccessEntry, entry_id)
        if row is None:
            raise ValueError('Acesso não encontrado.')
        db.session.delete(row)
        db.session.commit()


bp = Blueprint('catalog', __name__)


@bp.before_request
def require_vault_permission():
    if not current_user.is_authenticated or not current_user.can_access_vault:
        abort(403)


def catalog_service():
    return AccessCatalog(WebCatalogRepository(), VaultCipher(current_app.config),
                         allow_corporate_passwords=True)


def reauthenticate(password):
    """Reauthenticate the current user before returning or changing a secret."""
    return verify_current_password(password, namespace='vault')


@bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


@bp.route('/vault', methods=['GET', 'POST'])
@login_required
def entries():
    service = catalog_service()
    entry = None
    status = 200
    if request.method == 'POST':
        entry_id = request.form.get('entry_id') or None
        entry = service.get_entry(entry_id) if entry_id else {}
        if entry_id and not entry:
            abort(404)
        # Retain only editable metadata after validation errors; never echo either
        # the stored password or the submitted passwords into the response HTML.
        entry = {**entry, **{key: request.form.get(key, '') for key in
                            ('name', 'url', 'username', 'category', 'notes')}}
        if request.form.get('is_corporate') in ('0', '1'):
            entry['is_corporate'] = request.form['is_corporate'] == '1'
        allowed = {'csrf_token', 'entry_id', 'name', 'url', 'username', 'category', 'notes',
                   'password', 'is_corporate', 'clear_password', 'reauth_password'}
        try:
            if set(request.form) - allowed:
                raise ValueError('O formulário contém campos não permitidos.')
            values = {key: request.form.get(key, '')
                      for key in ('name', 'url', 'username', 'category', 'notes')}
            for key in ('password', 'is_corporate', 'clear_password'):
                if key in request.form:
                    values[key] = request.form[key]
            if values.get('password') or str(values.get('clear_password', '')).lower() in ('1', 'true'):
                if not service.cipher.available:
                    raise VaultError('O cofre está indisponível. O catálogo continua disponível.')
                failure = reauthenticate(request.form.get('reauth_password', ''))
                if failure:
                    status, message = failure
                    return _render_entries(service, entry=entry, status=status, message=message)
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
                           entry=entry or {}, notice=(
                               'Cofre habilitado para acessos pessoais e corporativos autorizados.'
                               if service.cipher.available else service.cipher.notice),
                           vault_available=service.cipher.available,
                           can_read_archived_tasks=current_user.role == 'admin'), status


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


@bp.route('/vault/<entry_id>/delete', methods=['GET', 'POST'])
@login_required
def delete_entry(entry_id):
    service = catalog_service()
    entry = service.get_entry(entry_id)
    if not entry:
        abort(404)
    # Confirmation is a separate, non-destructive step even without JavaScript.
    if request.method == 'GET' or request.form.get('confirm') != '1':
        status = 200 if request.method == 'GET' else 400
        return render_template('catalog_delete.html', entry=entry), status
    try:
        service.delete_entry(entry_id)
        flash('Acesso excluído.', 'success')
    except Exception:
        db.session.rollback()
        flash('Não foi possível excluir o acesso.', 'error')
    return redirect(url_for('catalog.entries'))
