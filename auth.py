"""Single-owner dashboard authentication. Credentials and sessions stay server-side."""
import hashlib
import hmac
import os
import secrets
import time
from http.cookies import SimpleCookie


def init(db):
    with db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS owner (id INTEGER PRIMARY KEY, password_hash TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires REAL NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS login_attempts (address TEXT, created REAL)')


def initialized(db):
    with db() as conn:
        return conn.execute('SELECT 1 FROM owner WHERE id=1').fetchone() is not None


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
    return salt + ':' + digest


def cookie_token(header):
    try:
        cookies = SimpleCookie(header or '')
        return cookies['reply_session'].value if 'reply_session' in cookies else ''
    except Exception:
        return ''


def authenticated(db, header):
    token = cookie_token(header)
    if not token:
        return False
    with db() as conn:
        return conn.execute('SELECT 1 FROM sessions WHERE token_hash=? AND expires>?',
                            (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone() is not None


def login(db, body, address, setup=False):
    password = body.get('password', '')
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError('Use a password with at least 12 characters.')
    now = time.time()
    with db() as conn:
        conn.execute('DELETE FROM login_attempts WHERE created<?', (now - 900,))
        count = conn.execute('SELECT COUNT(*) FROM login_attempts WHERE address=?', (address,)).fetchone()[0]
        if count >= 5:
            raise PermissionError('Too many attempts. Try again in 15 minutes.')
        conn.execute('INSERT INTO login_attempts VALUES (?,?)', (address, now))
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        owner = conn.execute('SELECT password_hash FROM owner WHERE id=1').fetchone()
        if setup:
            expected = os.getenv('DASHBOARD_SETUP_TOKEN', '')
            supplied = body.get('setup_token', '')
            if owner or not expected or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
                raise PermissionError('Setup is unavailable or the setup code is incorrect.')
            conn.execute('INSERT INTO owner VALUES (1,?)', (password_hash(password),))
        elif not owner or not hmac.compare_digest(owner[0], password_hash(password, owner[0].split(':')[0])):
            raise PermissionError('Incorrect password.')
        token = secrets.token_urlsafe(32)
        conn.execute('DELETE FROM sessions WHERE expires<?', (now,))
        conn.execute('INSERT INTO sessions VALUES (?,?)', (hashlib.sha256(token.encode()).hexdigest(), now + 43200))
        conn.execute('DELETE FROM login_attempts WHERE address=?', (address,))
    return token


def logout(db, header):
    token = cookie_token(header)
    with db() as conn:
        conn.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))


def session_cookie(token):
    secure = '; Secure' if os.getenv('COOKIE_SECURE', 'false').lower() == 'true' else ''
    age = '43200' if token else '0'
    return f'reply_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}'
