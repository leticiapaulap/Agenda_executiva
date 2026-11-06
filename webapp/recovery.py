"""Explicit operator commands. Never called from startup, migrations or HTTP."""
from datetime import timedelta
from functools import wraps
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import subprocess

import click
from flask import current_app
from sqlalchemy import func, select
from werkzeug.security import generate_password_hash

from .auth import _lock_security_state, _validated_email, _validated_password
from .models import AdminRecoveryGrant, AuditLog, SecurityState, User, db, utc_now


GRANT_LIFETIME = timedelta(minutes=15)
BACKUP_MAX_AGE = timedelta(hours=24)
BACKUP_TABLES = (
    'access_catalog', 'admin_recovery_grants', 'audit_logs', 'bill_payments',
    'bill_reminder_log', 'bill_series', 'bills', 'events', 'login_attempts',
    'manual_reminders', 'reminder_states', 'security_state', 'tasks', 'users',
)


def _safe_command(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (click.ClickException, click.Abort):
            db.session.rollback()
            raise
        except ValueError as error:
            db.session.rollback()
            raise click.ClickException(str(error)) from None
        except Exception:
            db.session.rollback()
            # Database/driver exception strings may contain connection credentials.
            current_app.logger.error('admin_recovery_failed reason=operation_unavailable')
            raise click.ClickException('Operação não concluída. Verifique conexão, migrações e permissões.') from None
    return wrapped


def verify_backup(path):
    """Check a recent custom-format pg_dump for this database without restoring it."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError('Informe um backup PostgreSQL regular e verificável.')
    modified = path.stat().st_mtime
    age = utc_now().timestamp() - modified
    if age < -300 or age > BACKUP_MAX_AGE.total_seconds():
        raise ValueError('O backup deve ter sido criado nas últimas 24 horas.')
    with path.open('rb') as source:
        if source.read(5) != b'PGDMP':
            raise ValueError('Use um backup completo no formato custom do pg_dump (-Fc).')
        source.seek(0)
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    try:
        result = subprocess.run(
            ['pg_restore', '--list', str(path)], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=30, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('Não foi possível validar o backup. Instale pg_restore compatível no PATH.') from None
    database_name = db.engine.url.database
    names = [line.split('dbname:', 1)[1].strip() for line in result.stdout.splitlines()
             if line.startswith(';') and 'dbname:' in line]
    if (result.returncode != 0 or names != [database_name]
            or any(f'TABLE DATA public {table} ' not in result.stdout for table in BACKUP_TABLES)):
        raise ValueError('O backup não é completo ou não corresponde ao banco conectado.')
    return digest


def create_operator_backup(path):
    """Create and verify a local custom-format backup without exposing the URL."""
    path = Path(path)
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError('O arquivo de backup deve ser novo e sua pasta já deve existir.')
    url = db.engine.url
    if url.get_backend_name() not in ('postgres', 'postgresql') or not url.host or not url.database:
        raise ValueError('A conexão selecionada não é um PostgreSQL remoto válido.')
    if not url.password:
        raise ValueError('A conexão PostgreSQL não contém uma credencial para o backup.')
    environment = os.environ.copy()
    environment['PGPASSWORD'] = url.password
    environment['PGSSLMODE'] = str(url.query.get('sslmode', 'verify-full'))
    if url.query.get('sslrootcert'):
        environment['PGSSLROOTCERT'] = str(url.query['sslrootcert'])
    arguments = [
        'pg_dump', '--format=custom', '--no-owner', '--no-acl', '--no-password',
        '--host', url.host, '--username', url.username or '',
        '--dbname', url.database,
    ]
    if url.port:
        arguments.extend(['--port', str(url.port)])
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, 'wb') as output:
            result = subprocess.run(
                arguments, stdout=output, stderr=subprocess.PIPE, timeout=300,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                env=environment,
            )
        if result.returncode != 0:
            raise ValueError('pg_dump não conseguiu criar o backup. Confira a conexão e o cliente PostgreSQL.')
        digest = verify_backup(path)
        current_app.logger.info('admin_recovery_backup_created')
        return digest
    except (OSError, subprocess.TimeoutExpired):
        if created:
            path.unlink(missing_ok=True)
        raise ValueError('Não foi possível criar o backup. Confira pg_dump, conexão e permissões locais.') from None
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise


def _admin(email, *, locked=False):
    query = select(User).where(func.lower(User.email) == _validated_email(email))
    if locked:
        query = query.with_for_update().execution_options(populate_existing=True)
    user = db.session.scalar(query)
    if user is None or user.role != 'admin' or not user.active:
        raise ValueError('Selecione uma conta administrativa existente e ativa. A recuperação não cria nem reativa contas.')
    return user


def _confirmed_target(email):
    user = _admin(email)
    identity, version = user.id, user.session_version
    click.echo(f'Conta selecionada: {user.email} | ID: {identity} | Administrador ativo')
    db.session.rollback()  # Never hold a lock/transaction during operator prompts.
    answer = click.prompt('Confirme digitando o ID completo da conta')
    if not hmac.compare_digest(answer, identity):
        raise ValueError('Confirmação da conta recusada. Nenhuma senha foi alterada.')
    return identity, version


def _locked_target(email, identity, version):
    if _lock_security_state() is None:
        raise ValueError('Aplique as migrações antes da recuperação.')
    user = _admin(email, locked=True)
    if user.id != identity or user.session_version != version:
        raise ValueError('A conta mudou durante a operação. Inicie novamente a confirmação.')
    return user


def _revoke_grants(user_id, *, consumed=None):
    grants = db.session.scalars(select(AdminRecoveryGrant).where(
        AdminRecoveryGrant.target_user_id == user_id,
        AdminRecoveryGrant.token_digest.is_not(None)).with_for_update()).all()
    for grant in grants:
        grant.token_digest = None
        if grant is consumed:
            grant.consumed_at = utc_now()
        else:
            grant.revoked_at = utc_now()


def _new_password_hash():
    password = click.prompt('Nova senha', hide_input=True, confirmation_prompt=True)
    return generate_password_hash(_validated_password(password))


def _apply_reset(user, password_hash, *, consumed=None):
    user.password_hash = password_hash
    user.session_version += 1
    _revoke_grants(user.id, consumed=consumed)
    db.session.add(AuditLog(actor_id=None, target_user_id=user.id,
                            action='admin_password_recovered'))
    db.session.commit()
    current_app.logger.info('admin_recovery_success user_id=%s', user.id)
    click.echo('Senha administrativa redefinida; sessões anteriores foram invalidadas.')
    click.echo('Use o login normal. A recuperação não autentica automaticamente.')


def register_commands(app):
    @app.cli.command('admin-recovery-backup')
    @click.option('--backup-file', required=True, type=click.Path(dir_okay=False, path_type=Path))
    @_safe_command
    def recovery_backup(backup_file):
        """Create a verified local PostgreSQL backup before account recovery."""
        create_operator_backup(backup_file)
        click.echo('Backup PostgreSQL completo criado e verificado para o banco conectado.')

    @app.cli.command('auth-status')
    @click.option('--email', prompt='Login da conta')
    @_safe_command
    def auth_status(email):
        """Read-only account diagnostics, without hashes, passwords or database URLs."""
        state = db.session.get(SecurityState, 1)
        user = db.session.scalar(select(User).where(func.lower(User.email) == _validated_email(email)))
        click.echo(f'Importação inicial concluída: {bool(state and state.bootstrap_complete)}')
        click.echo(f'Credenciais de inicialização configuradas: {bool(app.config.get("ADMIN_USERNAME") and app.config.get("ADMIN_PASSWORD_HASH"))}')
        if user is None:
            click.echo('Conta não encontrada no banco conectado.')
        else:
            click.echo(f'ID: {user.id} | Perfil: {user.role} | Ativa: {user.active}')
            click.echo(f'Versão de sessão: {user.session_version} | Último acesso UTC: {user.last_login or "nenhum"}')
            click.echo('Fonte da senha: users.password_hash (valor não exibido).')
        db.session.rollback()

    @app.cli.command('reset-admin-password')
    @click.option('--email', prompt='Login do administrador')
    @click.option('--backup-file', required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @_safe_command
    def reset_admin_password(email, backup_file):
        """Interactive recovery for a trusted deployment/database operator."""
        verify_backup(backup_file)
        identity, version = _confirmed_target(email)
        password_hash = _new_password_hash()
        user = _locked_target(email, identity, version)
        _apply_reset(user, password_hash)

    # Backwards compatible name, with the same mandatory backup and confirmation.
    app.cli.add_command(reset_admin_password, 'users-reset-admin-password')

    @app.cli.command('admin-recovery-authorize')
    @click.option('--email', prompt='Login do administrador')
    @click.option('--backup-file', required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option('--token-file', required=True, type=click.Path(dir_okay=False, path_type=Path))
    @_safe_command
    def authorize(email, backup_file, token_file):
        """Issue a separate recovery authorization, valid once for fifteen minutes."""
        backup_digest = verify_backup(backup_file)
        identity, version = _confirmed_target(email)
        token = secrets.token_urlsafe(32)
        user = _locked_target(email, identity, version)
        _revoke_grants(user.id)
        now = utc_now()
        grant = AdminRecoveryGrant(target_user_id=user.id,
            token_digest=hashlib.sha256(token.encode('ascii')).hexdigest(),
            backup_digest=backup_digest, expected_session_version=version,
            issued_at=now, expires_at=now + GRANT_LIFETIME)
        db.session.add(grant)
        db.session.add(AuditLog(actor_id=None, target_user_id=user.id, action='admin_recovery_authorized'))
        # Exclusive creation avoids overwriting an existing authorization or symlink.
        created = False
        try:
            descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
                stream.write(token)
                stream.flush()
                os.fsync(stream.fileno())
            db.session.commit()
        except Exception:
            if created:
                token_file.unlink(missing_ok=True)
            raise
        click.echo('Autorização criada no arquivo privado indicado; válida por 15 minutos e uma única utilização.')
        click.echo('Nenhuma senha foi alterada. Não compartilhe nem envie esse arquivo ao GitHub.')

    @app.cli.command('reset-admin-password-once')
    @click.option('--email', prompt='Login do administrador')
    @click.option('--backup-file', required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option('--authorization-file', required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @_safe_command
    def reset_once(email, backup_file, authorization_file):
        """Manual Free-plan recovery, against the authorized external database."""
        backup_digest = verify_backup(backup_file)
        if authorization_file.is_symlink() or authorization_file.stat().st_size > 128:
            raise ValueError('Arquivo de autorização inválido.')
        token = authorization_file.read_text(encoding='ascii').strip()
        if len(token) != 43 or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for char in token):
            raise ValueError('Arquivo de autorização inválido.')
        digest = hashlib.sha256(token.encode('ascii')).hexdigest()
        identity, version = _confirmed_target(email)
        # Check before asking for a password, and again inside the locked transaction.
        def grant_for(user):
            grant = db.session.scalar(select(AdminRecoveryGrant).where(
                AdminRecoveryGrant.token_digest == digest).with_for_update()
                .execution_options(populate_existing=True))
            if (grant is None or grant.target_user_id != user.id
                    or grant.expected_session_version != user.session_version
                    or grant.expires_at <= utc_now() or grant.consumed_at or grant.revoked_at
                    or not hmac.compare_digest(grant.backup_digest, backup_digest)):
                raise ValueError('Autorização inválida, expirada, já utilizada ou vinculada a outra conta/backup.')
            return grant
        grant_for(_admin(email))
        db.session.rollback()
        password_hash = _new_password_hash()
        user = _locked_target(email, identity, version)
        grant = grant_for(user)
        _apply_reset(user, password_hash, consumed=grant)
        try:
            authorization_file.unlink()
        except OSError:
            # The database authorization is already irreversibly consumed.
            click.echo('Autorização consumida. Remova manualmente o arquivo local, que já não permite recuperação.')

    @app.cli.command('admin-recovery-revoke')
    @click.option('--email', prompt='Login do administrador')
    @_safe_command
    def revoke(email):
        """Revoke pending grants; does not change passwords or reactivate users."""
        identity, version = _confirmed_target(email)
        user = _locked_target(email, identity, version)
        _revoke_grants(user.id)
        db.session.add(AuditLog(actor_id=None, target_user_id=user.id, action='admin_recovery_revoked'))
        db.session.commit()
        click.echo('Autorizações pendentes revogadas. Remova os arquivos locais de autorização.')
