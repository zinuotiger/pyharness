"""Local account authentication and team membership; secrets never appear in public records."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager, closing
from contextvars import ContextVar
from pathlib import Path

from pyharness.core.tenant_settings import normalize_tenant_id

# Request submission identity only; queued runners rebind it from their own
# server-authored task event, never from the queue pump's inherited context.
current_actor = ContextVar('pyharness_account_actor', default=None)


def public_actor(account):
    return ({'user_id':account['user_id'],'tenant':account['tenant']}
            if account else None)


class AccountError(ValueError):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


class Accounts:
    session_seconds = 12 * 60 * 60

    @staticmethod
    def manages_tenant(root: Path, tenant: str) -> bool:
        path = root / 'accounts.sqlite3'
        if not path.exists():
            return False
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            return db.execute('SELECT 1 FROM teams WHERE id=?', (tenant,)).fetchone() is not None

    def __init__(self, root: Path):
        self.path = root / 'accounts.sqlite3'
        root.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    salt BLOB NOT NULL, password_hash BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS members (
                    user_id TEXT NOT NULL, tenant TEXT NOT NULL, role TEXT NOT NULL,
                    PRIMARY KEY(user_id,tenant));
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    tenant TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS failures (
                    username TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def password_hash(password, salt):
        return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1, dklen=32)

    @staticmethod
    def username(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', value):
            raise AccountError('invalid_username')
        return value.lower()

    def provision(self, username, password, tenant):
        username = self.username(username)
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise AccountError('password_length')
        tenant = normalize_tenant_id(tenant)
        if tenant == 'default':
            raise AccountError('operator_workspace_reserved')
        salt, ident = secrets.token_bytes(16), 'u-'+uuid.uuid4().hex
        digest = self.password_hash(password, salt)
        try:
            with self.db() as db:
                db.execute('INSERT INTO users VALUES (?,?,?,?)', (ident, username, salt, digest))
                db.execute('INSERT OR IGNORE INTO teams VALUES (?,?)', (tenant, tenant))
                # Provisioning is operator-only; assigning ownership is explicit.
                db.execute('INSERT INTO members VALUES (?,?,?)', (ident, tenant, 'owner'))
        except sqlite3.IntegrityError:
            raise AccountError('account_exists', 409) from None
        return {'user_id':ident, 'username':username, 'tenant':tenant, 'role':'owner'}

    def login(self, username, password, tenant=None):
        username = self.username(username)
        if not isinstance(password, str) or len(password) > 256:
            raise AccountError('invalid_credentials', 401)
        now = time.time()
        with self.db() as db:
            db.execute('DELETE FROM failures WHERE expires < ?', (now,))
            failure = db.execute('SELECT count FROM failures WHERE username=?', (username,)).fetchone()
            if failure and failure['count'] >= 5:
                raise AccountError('login_rate_limited', 429)
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        expected = row['password_hash'] if row else bytes(32)
        actual = self.password_hash(password, row['salt'] if row else bytes(16))
        if row is None or not hmac.compare_digest(actual, expected):
            with self.db() as db:
                if db.execute('SELECT count(*) FROM failures').fetchone()[0] >= 4096:
                    raise AccountError('login_rate_limited', 429)
                db.execute('INSERT INTO failures VALUES (?,1,?) ON CONFLICT(username) '
                           'DO UPDATE SET count=count+1', (username, now+900))
            raise AccountError('invalid_credentials', 401)
        with self.db() as db:
            memberships = db.execute('SELECT tenant,role FROM members WHERE user_id=? ORDER BY tenant',
                                     (row['id'],)).fetchall()
            membership = next((m for m in memberships if tenant is None or m['tenant'] == tenant), None)
            if membership is None:
                raise AccountError('invalid_credentials', 401)
            db.execute('DELETE FROM failures WHERE username=?', (username,))
            db.execute('DELETE FROM sessions WHERE expires < ?', (now,))
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                       (self.digest(token), row['id'], membership['tenant'], now+self.session_seconds))
        return token

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    def authenticate(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 128:
            return None
        with self.db() as db:
            row = db.execute('''SELECT u.id AS user_id,u.username,s.tenant,m.role FROM sessions s
                JOIN users u ON u.id=s.user_id
                JOIN members m ON m.user_id=s.user_id AND m.tenant=s.tenant
                WHERE s.digest=? AND s.expires>?''', (self.digest(token),time.time())).fetchone()
        return dict(row) if row else None

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE digest=?', (self.digest(token),))

    def teams(self, user_id):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT t.id,t.name,m.role FROM teams t '
                'JOIN members m ON m.tenant=t.id WHERE m.user_id=? ORDER BY t.id', (user_id,))]

    def membership(self, tenant, username, role):
        if role not in ('owner','member','viewer','remove'):
            raise AccountError('invalid_role')
        username = self.username(username)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            user = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
            if user is None: raise AccountError('account_not_found',404)
            old = db.execute('SELECT role FROM members WHERE user_id=? AND tenant=?', (user['id'],tenant)).fetchone()
            owners = db.execute("SELECT count(*) FROM members WHERE tenant=? AND role='owner'", (tenant,)).fetchone()[0]
            if old and old['role']=='owner' and role!='owner' and owners<=1:
                raise AccountError('last_owner_required',409)
            if role=='remove':
                db.execute('DELETE FROM members WHERE user_id=? AND tenant=?',(user['id'],tenant))
            else:
                db.execute('INSERT INTO members VALUES (?,?,?) ON CONFLICT(user_id,tenant) DO UPDATE SET role=excluded.role',
                           (user['id'],tenant,role))
        return {'username':username,'role':role,'tenant':tenant}
