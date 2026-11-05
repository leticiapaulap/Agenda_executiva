"""Administrative user management with server-side authorization and reauthentication."""
from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import func, select

from .auth import (create_user, reset_password, set_active, update_user,
                   verify_current_password)
from .models import AuditLog, User, db


bp = Blueprint('users', __name__, url_prefix='/users')
ROLE_LABELS = {'admin': 'Administrador', 'manager': 'Gestor', 'reader': 'Leitor'}


@bp.before_request
@login_required
def require_administrator():
    if current_user.role != 'admin' or not current_user.active:
        abort(403)


@bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store, private'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def _get_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    return user


def _confirmation():
    if request.form.get('confirm') != '1':
        return 400, 'Confirme explicitamente a operação administrativa.'
    return verify_current_password(request.form.get('reauth_password', ''), namespace='admin')


def _values(user=None):
    """Only permitted metadata may be reflected back after a validation error."""
    if request.method == 'POST':
        return {key: request.form.get(key, '') for key in ('name', 'email', 'role')} | {
            key: request.form.get(key) == '1' for key in ('agenda_access', 'finance_access', 'vault_access')}
    if user is None:
        return {'name': '', 'email': '', 'role': 'reader', 'agenda_access': False,
                'finance_access': False, 'vault_access': False}
    return {key: getattr(user, key) for key in ('name', 'email', 'role', 'agenda_access',
                                               'finance_access', 'vault_access')}


def _failure(message, status=400):
    flash(message, 'error')
    return status


def _error():
    db.session.rollback()
    current_app.logger.error('Falha em operação administrativa de usuários.')
    return _failure('Não foi possível concluir a operação. Tente novamente.')


@bp.get('')
@bp.get('/')
def index():
    users = db.session.scalars(select(User).order_by(User.name, User.id)).all()
    return render_template('users.html', users=users, roles=ROLE_LABELS,
                           active_count=sum(user.active for user in users),
                           admin_count=sum(user.active and user.role == 'admin' for user in users))


@bp.route('/new', methods=['GET', 'POST'])
def new_user():
    status = 200
    if request.method == 'POST':
        try:
            failure = _confirmation()
            if failure:
                status, message = failure
                _failure(message, status)
            else:
                data = _values()
                data['password'] = request.form.get('password', '')
                create_user(data, current_user)
                flash('Usuário cadastrado com sucesso.', 'success')
                return redirect(url_for('users.index'))
        except ValueError as error:
            db.session.rollback()
            status = _failure(str(error))
        except Exception:
            status = _error()
    return render_template('user_form.html', values=_values(), editing=False,
                           managed_user=None, roles=ROLE_LABELS), status


@bp.route('/<user_id>/edit', methods=['GET', 'POST'])
def edit_user(user_id):
    managed_user = _get_user(user_id)
    status = 200
    if request.method == 'POST':
        try:
            failure = _confirmation()
            if failure:
                status, message = failure
                _failure(message, status)
            else:
                update_user(user_id, _values(), current_user)
                flash('Usuário atualizado. Sessões anteriores foram invalidadas quando necessário.', 'success')
                return redirect(url_for('users.index'))
        except ValueError as error:
            db.session.rollback()
            status = _failure(str(error))
        except Exception:
            status = _error()
    return render_template('user_form.html', values=_values(managed_user), editing=True,
                           managed_user=managed_user, roles=ROLE_LABELS), status


@bp.route('/<user_id>/password', methods=['GET', 'POST'])
def reset_user_password(user_id):
    managed_user = _get_user(user_id)
    status = 200
    if request.method == 'POST':
        try:
            failure = _confirmation()
            if failure:
                status, message = failure
                _failure(message, status)
            else:
                reset_password(user_id, request.form.get('password', ''), current_user)
                flash('Senha redefinida. Todas as sessões anteriores desse usuário foram invalidadas.', 'success')
                return redirect(url_for('users.index'))
        except ValueError as error:
            db.session.rollback()
            status = _failure(str(error))
        except Exception:
            status = _error()
    return render_template('user_password.html', managed_user=managed_user, operation='password'), status


@bp.route('/<user_id>/status', methods=['GET', 'POST'])
def status_user(user_id):
    managed_user = _get_user(user_id)
    status = 200
    if request.method == 'POST':
        try:
            failure = _confirmation()
            if failure:
                status, message = failure
                _failure(message, status)
            else:
                target = request.form.get('active')
                if target not in ('0', '1'):
                    raise ValueError('Selecione um status válido.')
                set_active(user_id, target == '1', current_user)
                flash('Status atualizado. Sessões anteriores desse usuário foram invalidadas.', 'success')
                return redirect(url_for('users.index'))
        except ValueError as error:
            db.session.rollback()
            status = _failure(str(error))
        except Exception:
            status = _error()
    return render_template('user_password.html', managed_user=managed_user, operation='status'), status


@bp.get('/audit')
def audit():
    page = request.args.get('page', 1, type=int)
    if page is None or page < 1 or page > 100000:
        abort(400)
    rows = db.session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                              .limit(50).offset((page - 1) * 50)).all()
    count = db.session.scalar(select(func.count()).select_from(AuditLog)) or 0
    return render_template('audit.html', rows=rows, page=page, has_next=page * 50 < count)
