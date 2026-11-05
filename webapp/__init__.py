import os
from pathlib import Path
from datetime import date, timedelta
from urllib.parse import urlsplit, urlunsplit

from flask import Flask, redirect, request, url_for
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.middleware.proxy_fix import ProxyFix

from .models import User, db
from .permissions import can_edit_record, can_edit_task, can_read_agenda, can_read_finance, can_write_agenda


csrf = CSRFProtect()
login_manager = LoginManager()
migrate = Migrate()


def _database_url():
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        raise RuntimeError('DATABASE_URL precisa apontar para um PostgreSQL.')
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    parts = urlsplit(url)
    if parts.scheme not in ('postgresql', 'postgresql+psycopg'):
        raise RuntimeError('DATABASE_URL deve usar PostgreSQL; SQLite não é suportado na hospedagem.')
    if parts.scheme == 'postgresql':
        url = urlunsplit(('postgresql+psycopg', parts.netloc, parts.path, parts.query, parts.fragment))
    return url


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    test_config = test_config or {}
    database_uri = test_config.get('SQLALCHEMY_DATABASE_URI')
    if not database_uri and test_config.get('TESTING'):
        database_uri = 'sqlite://'
    if not database_uri:
        database_uri = _database_url()
    app.config.from_mapping(
        SECRET_KEY=os.environ.get('SECRET_KEY'),
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', 'true').lower() == 'true',
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SECURE=True,
        WTF_CSRF_TIME_LIMIT=3600,
        ADMIN_USERNAME=os.environ.get('ADMIN_USERNAME', ''),
        ADMIN_PASSWORD_HASH=os.environ.get('ADMIN_PASSWORD_HASH', ''),
        VAULT_ENABLED=os.environ.get('VAULT_ENABLED', 'false'),
        VAULT_KEYS=os.environ.get('VAULT_KEYS', ''),
        VAULT_ACTIVE_KEY_ID=os.environ.get('VAULT_ACTIVE_KEY_ID', ''),
        PREFERRED_URL_SCHEME='https',
        RECEIPT_STORAGE_DIR=os.environ.get('RECEIPT_STORAGE_DIR') or None,
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
        SQLALCHEMY_ENGINE_OPTIONS={'hide_parameters': True},
    )
    app.config.update(test_config)
    if app.config.get('RECEIPT_STORAGE_DIR'):
        receipt_root = Path(app.config['RECEIPT_STORAGE_DIR']).expanduser()
        if not receipt_root.is_absolute() or receipt_root.resolve().is_relative_to(Path(app.root_path).resolve()):
            raise RuntimeError('RECEIPT_STORAGE_DIR deve ser absoluto e ficar fora da pasta webapp.')
    if not app.config.get('TESTING') and not app.config['SQLALCHEMY_DATABASE_URI'].startswith(
            ('postgresql://', 'postgresql+psycopg://')):
        raise RuntimeError('SQLite não é aceito como armazenamento persistente da aplicação web.')
    if not app.config.get('TESTING'):
        if not app.config.get('SECRET_KEY'):
            raise RuntimeError('Configure uma SECRET_KEY forte nas variáveis de ambiente.')
    if app.config.get('TESTING') and not app.config.get('SECRET_KEY'):
        app.config['SECRET_KEY'] = 'test-secret-key-not-for-production'
    if app.config.get('TESTING'):
        app.config.setdefault('WTF_CSRF_SSL_STRICT', False)
        app.config.setdefault('SESSION_COOKIE_SECURE', False)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    login_manager.login_view = 'web.login'
    login_manager.login_message = 'Entre para acessar a agenda.'
    login_manager.login_message_category = 'info'
    login_manager.session_protection = 'strong'

    @login_manager.user_loader
    def load_user(user_id):
        try:
            user_uuid, version = user_id.rsplit(':', 1)
            version = int(version)
        except (AttributeError, ValueError):
            return None
        user = db.session.get(User, user_uuid)
        return user if user and user.active and user.session_version == version else None

    @app.context_processor
    def inject_csrf_token():
        def home_url():
            if can_read_agenda():
                return url_for('web.dashboard')
            if can_read_finance():
                return url_for('finance.bills')
            if current_user.is_authenticated and current_user.can_access_vault:
                return url_for('catalog.entries')
            return url_for('web.login')

        return {
            'csrf_token': generate_csrf,
            'today_label': date.today().strftime('%d/%m/%Y'),
            'home_url': home_url,
            'can_edit_record': can_edit_record,
            'can_edit_task': can_edit_task,
            'can_read_agenda': can_read_agenda,
            'can_read_finance': can_read_finance,
            'can_write_agenda': can_write_agenda,
        }

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        if request.is_secure:
            response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
        return response

    @app.before_request
    def require_login():
        if request.endpoint and request.endpoint != 'static' and not current_user.is_authenticated:
            if request.endpoint != 'web.login':
                return redirect(url_for('web.login', next=request.full_path if request.query_string else request.path))
    from .routes import bp
    app.register_blueprint(bp)
    from .user_routes import bp as users_bp
    app.register_blueprint(users_bp)
    from .catalog import bp as catalog_bp
    app.register_blueprint(catalog_bp)
    from .vault_commands import register_commands
    register_commands(app)
    from .finance_routes import bp as finance_bp
    app.register_blueprint(finance_bp)
    from .auth import register_recovery_command
    register_recovery_command(app)
    return app
