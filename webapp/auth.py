"""Authentication, one-time legacy-account import and user administration."""
import re
import time
from uuid import uuid4

from flask import current_app
from flask_login import current_user, login_fresh
import click
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from werkzeug.security import check_password_hash, generate_password_hash

from .models import AuditLog, LoginAttempt, SecurityState, User, db


PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024
_DUMMY_PASSWORD_HASH = generate_password_hash('invalid-login-placeholder')
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def normalize_login(value):
    return value.strip().casefold() if isinstance(value, str) else ''


def _lock_security_state():
    """Serialize account mutations across PostgreSQL workers and SQLite tests."""
    db.session.execute(update(SecurityState).where(SecurityState.id == 1).values(
        lock_version=SecurityState.lock_version + 1))
    return db.session.scalar(select(SecurityState).where(SecurityState.id == 1)
                             .with_for_update().execution_options(populate_existing=True))


def bootstrap_legacy_admin():
    """Import the configured administrator once; never recreate it after bootstrap."""
    legacy_username = current_app.config.get('ADMIN_USERNAME', '')
    username = normalize_login(legacy_username)
    password_hash = current_app.config.get('ADMIN_PASSWORD_HASH', '')
    if not username or not password_hash:
        return False
    state = _lock_security_state()
    if state is None:
        db.session.rollback()
        raise RuntimeError('Aplique as migrações do banco antes de autenticar.')
    if state.bootstrap_complete:
        db.session.commit()
        return False
    existing = db.session.scalar(select(User).where(func.lower(User.email) == username))
    if existing is None:
        admin = User(
            id=str(uuid4()),
            name=legacy_username.strip(),
            email=username,
            password_hash=password_hash,
            role='admin',
            active=True,
            agenda_access=True,
            finance_access=True,
            vault_access=False,
        )
        db.session.add(admin)
    state.bootstrap_complete = True
    db.session.commit()
    return existing is None


def _password_matches(password_hash, password):
    try:
        return bool(password_hash and isinstance(password, str)
                    and len(password) <= PASSWORD_MAX_LENGTH
                    and check_password_hash(password_hash, password))
    except (TypeError, ValueError):
        return False


def authenticate(username, password):
    username = normalize_login(username)
    if not username or len(username) > 254 or not isinstance(password, str):
        _password_matches(_DUMMY_PASSWORD_HASH, password)
        return None
    user = db.session.scalar(select(User).where(func.lower(User.email) == username))
    if user is None:
        _password_matches(_DUMMY_PASSWORD_HASH, password)
        return None
    if not user.active or not _password_matches(user.password_hash, password):
        return None
    from .models import utc_now
    user.last_login = utc_now()
    db.session.commit()
    return user


def _validated_password(value):
    if not isinstance(value, str) or not PASSWORD_MIN_LENGTH <= len(value) <= PASSWORD_MAX_LENGTH:
        raise ValueError(f'A senha deve ter entre {PASSWORD_MIN_LENGTH} e {PASSWORD_MAX_LENGTH} caracteres.')
    return value


def _validated_name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise ValueError('Informe um nome com até 200 caracteres.')
    return value.strip()


def _validated_email(value):
    email = normalize_login(value)
    if not email or len(email) > 254 or any(char.isspace() for char in email):
        raise ValueError('Informe um e-mail ou login válido com até 254 caracteres.')
    if '@' in email and not _EMAIL_RE.fullmatch(email):
        raise ValueError('Informe um e-mail ou login válido com até 254 caracteres.')
    return email


def _validated_role(value):
    if value not in ('admin', 'manager', 'reader'):
        raise ValueError('Selecione um perfil de acesso válido.')
    return value


def _validated_permissions(data, role):
    permissions = {}
    for key in ('agenda_access', 'finance_access', 'vault_access'):
        value = data.get(key, False)
        if not isinstance(value, bool):
            raise ValueError('Permissão inválida.')
        permissions[key] = value
    if role == 'admin':
        permissions['agenda_access'] = True
        permissions['finance_access'] = True
    if role == 'reader' and permissions['vault_access']:
        raise ValueError('O perfil Leitor não pode acessar o cofre.')
    if not any(permissions.values()):
        raise ValueError('Conceda ao menos um acesso antes de ativar o usuário.')
    return permissions


def _audit(actor, target, action):
    db.session.add(AuditLog(actor_id=actor.id, target_user_id=target.id, action=action))


def _assert_unique_email(email, exclude_id=None):
    query = select(User.id).where(func.lower(User.email) == email)
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    if db.session.scalar(query) is not None:
        raise ValueError('Já existe um usuário cadastrado com este e-mail ou login.')


def _keep_an_active_admin(user, *, becomes_admin):
    if user.active and user.role == 'admin' and not becomes_admin:
        active_admins = db.session.scalar(select(func.count(User.id)).where(
            User.active.is_(True), User.role == 'admin'))
        if active_admins <= 1:
            raise ValueError('O último administrador ativo não pode ser desativado ou rebaixado.')


def create_user(data, actor):
    name = _validated_name(data.get('name'))
    email = _validated_email(data.get('email'))
    password = _validated_password(data.get('password'))
    role = _validated_role(data.get('role'))
    permissions = _validated_permissions(data, role)
    _lock_security_state()
    _assert_unique_email(email)
    user = User(id=str(uuid4()), name=name, email=email,
                password_hash=generate_password_hash(password), role=role,
                active=True, session_version=1, **permissions)
    db.session.add(user)
    _audit(actor, user, 'user_created')
    db.session.commit()
    return user


def update_user(user_id, data, actor):
    name = _validated_name(data.get('name'))
    email = _validated_email(data.get('email'))
    role = _validated_role(data.get('role'))
    permissions = _validated_permissions(data, role)
    _lock_security_state()
    user = db.session.scalar(select(User).where(User.id == user_id).with_for_update()
                              .execution_options(populate_existing=True))
    if user is None:
        raise ValueError('Usuário não encontrado.')
    _assert_unique_email(email, exclude_id=user.id)
    _keep_an_active_admin(user, becomes_admin=(user.active and role == 'admin'))
    changed = (user.name != name or user.email != email or user.role != role
               or any(getattr(user, key) != value for key, value in permissions.items()))
    user.name, user.email, user.role = name, email, role
    for key, value in permissions.items():
        setattr(user, key, value)
    if changed:
        user.session_version += 1
    _audit(actor, user, 'user_updated')
    db.session.commit()
    return user


def reset_password(user_id, password, actor):
    password = _validated_password(password)
    _lock_security_state()
    user = db.session.scalar(select(User).where(User.id == user_id).with_for_update()
                              .execution_options(populate_existing=True))
    if user is None:
        raise ValueError('Usuário não encontrado.')
    user.password_hash = generate_password_hash(password)
    user.session_version += 1
    _audit(actor, user, 'user_password_reset')
    db.session.commit()


def set_active(user_id, active, actor):
    _lock_security_state()
    user = db.session.scalar(select(User).where(User.id == user_id).with_for_update()
                              .execution_options(populate_existing=True))
    if user is None:
        raise ValueError('Usuário não encontrado.')
    if user.active != active:
        if not active:
            _keep_an_active_admin(user, becomes_admin=False)
        user.active = active
        user.session_version += 1
        _audit(actor, user, 'user_activated' if active else 'user_deactivated')
    db.session.commit()
    return user


def login_attempt_for(key, now):
    dialect = db.session.get_bind().dialect.name
    insert = pg_insert if dialect == 'postgresql' else sqlite_insert
    db.session.execute(insert(LoginAttempt).values(
        client_key=key, failures=0, window_started_at=now, locked_until=0
    ).on_conflict_do_nothing(index_elements=['client_key']))
    return db.session.scalar(select(LoginAttempt).where(
        LoginAttempt.client_key == key).with_for_update().execution_options(populate_existing=True))


def verify_current_password(password, namespace='admin'):
    if not login_fresh() or not current_user.is_authenticated:
        return 401, 'Entre novamente antes de confirmar esta operação.'
    now = int(time.time())
    attempt = login_attempt_for(f'{namespace}:{current_user.id}', now)
    if attempt.locked_until > now:
        db.session.commit()
        return 429, 'Muitas tentativas. Aguarde 15 minutos.'
    if now - attempt.window_started_at >= 900:
        attempt.failures, attempt.window_started_at, attempt.locked_until = 0, now, 0
    if _password_matches(current_user.password_hash, password):
        attempt.failures, attempt.locked_until, attempt.window_started_at = 0, 0, now
        db.session.commit()
        return None
    attempt.failures += 1
    if attempt.failures >= 5:
        attempt.locked_until = now + 900
    locked = attempt.locked_until > now
    db.session.commit()
    return (429, 'Muitas tentativas. Aguarde 15 minutos.') if locked else (
        401, 'A senha de confirmação está incorreta.')


def register_recovery_command(app):
    @app.cli.command('users-reset-admin-password')
    @click.option('--email', prompt='Login do administrador', help='E-mail/login da conta administrativa.')
    def reset_admin_password(email):
        """Recover an existing administrator through authenticated operator access."""
        email = _validated_email(email)
        user = db.session.scalar(select(User).where(
            func.lower(User.email) == email, User.role == 'admin'))
        if user is None:
            raise click.ClickException('A conta administrativa não foi encontrada.')
        password = click.prompt('Nova senha', hide_input=True, confirmation_prompt=True)
        try:
            _validated_password(password)
        except ValueError as error:
            raise click.ClickException(str(error)) from None
        user.password_hash = generate_password_hash(password)
        user.session_version += 1
        db.session.add(AuditLog(actor_id=None, target_user_id=user.id,
                                action='admin_password_recovered'))
        db.session.commit()
        click.echo('Senha administrativa redefinida; sessões anteriores foram invalidadas.')
