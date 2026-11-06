"""Authenticated self-service password change; never accepts a target user ID."""
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, logout_user
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from .auth import _lock_security_state, _validated_password, verify_current_password
from .models import AuditLog, User, db


bp = Blueprint('account', __name__, url_prefix='/account')


@bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@bp.route('/password', methods=['GET', 'POST'])
@login_required
def change_password():
    status = 200
    if request.method == 'POST':
        try:
            # The session identity is the sole permitted target, including for admins.
            if request.form.keys() - {'csrf_token', 'current_password', 'password', 'password_confirm'}:
                raise ValueError('O formulário de alteração de senha contém campos inválidos.')
            password = _validated_password(request.form.get('password'))
            if password != request.form.get('password_confirm'):
                raise ValueError('A confirmação da nova senha não corresponde.')
            user_id, expected_version = current_user.id, current_user.session_version
            failure = verify_current_password(request.form.get('current_password', ''), namespace='account')
            if failure:
                status, message = failure
                flash(message, 'error')
            else:
                # Recheck identity/version after reauthentication, which commits its rate limit.
                # A concurrent reset or deactivation must not be overwritten by this request.
                _lock_security_state()
                user = db.session.scalar(select(User).where(User.id == user_id)
                                         .with_for_update().execution_options(populate_existing=True))
                if not user or not user.active or user.session_version != expected_version:
                    db.session.rollback()
                    logout_user()
                    session.clear()
                    flash('Sua sessão foi alterada. Entre novamente antes de trocar a senha.', 'error')
                    return redirect(url_for('web.login'))
                user.password_hash = generate_password_hash(password)
                user.session_version += 1
                db.session.add(AuditLog(actor_id=user.id, target_user_id=user.id,
                                        action='own_password_changed'))
                db.session.commit()
                logout_user()
                session.clear()
                flash('Senha alterada. Entre com a nova senha; suas sessões anteriores foram encerradas.', 'success')
                return redirect(url_for('web.login'))
        except ValueError as error:
            db.session.rollback()
            flash(str(error), 'error')
            status = 400
        except Exception:
            db.session.rollback()
            current_app.logger.error('account_password_change_failed')
            flash('Não foi possível alterar a senha. Tente novamente.', 'error')
            status = 503
    return render_template('account_password.html'), status
