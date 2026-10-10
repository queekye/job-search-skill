"""Optional local Gotify OTP reader. No SMS sending or website mutations."""
from __future__ import annotations
import base64
import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime, timezone
from http.client import HTTPException
import json
import hashlib
import os
import re
import secrets
import sqlite3
import ssl
import threading
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .app import AppError
from .locking import gate

_LOCK = threading.RLock()
TTL = 180
_TEST_MESSAGES = {
    'otp_test_passed': 'Gotify 连接及消息读取权限测试通过；尚未验证手机转发或招聘网站登录。',
    'otp_unauthorized': 'Gotify 拒绝认证（401）；请检查客户端读取 Token 是否正确或已失效。',
    'otp_forbidden': 'Gotify 拒绝读取消息（403）；请使用有读取权限的客户端 Token。',
    'otp_timeout': '连接 Gotify 超时；请检查服务地址和网络后重试。',
    'otp_unreachable': '无法连接 Gotify；请检查服务是否启动、地址端口和网络。',
    'otp_invalid_response': '服务未返回有效的 Gotify 消息响应；请确认填写的是 Gotify 服务根地址。',
    'otp_redirect_blocked': '服务返回了重定向，测试已停止；请填写 Gotify 的最终服务地址。',
    'otp_tls_error': 'Gotify HTTPS 证书或 TLS 连接验证失败；请检查服务证书和地址。',
    'otp_source_error': 'Gotify 返回服务错误；请检查服务器状态后重试。',
    'secret_unavailable': '无法解密已保存的 Token；请重新填写客户端读取 Token 后保存。',
    'platform_unavailable': '读取加密 Token 目前需要 Windows。',
    'otp_source_changed': '测试期间验证码配置已改变，本次结果已丢弃；请测试当前配置。',
}


def _protect(raw: str) -> str:
    if os.name != 'nt':
        raise AppError('platform_unavailable', 'Token storage currently requires Windows user encryption.')
    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]
    data = raw.encode('utf-8')
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = Blob(len(data), buf), Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise AppError('secret_storage_failed', 'Could not protect the local token.')
    try:
        return base64.b64encode(ctypes.string_at(target.pbData, target.cbData)).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _unprotect(encoded: str) -> str:
    if os.name != 'nt':
        raise AppError('platform_unavailable', 'Token storage currently requires Windows user encryption.')
    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]
    try:
        data = base64.b64decode(encoded, validate=True)
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source, target = Blob(len(data), buf), Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise ValueError()
        try:
            return ctypes.string_at(target.pbData, target.cbData).decode('utf-8')
        finally:
            ctypes.windll.kernel32.LocalFree(target.pbData)
    except Exception:
        raise AppError('secret_unavailable', 'Re-enter the Gotify client token in the local settings page.') from None


def _config(app):
    path = app.data_dir / 'otp.json'
    if not path.exists():
        return {'provider': 'manual', 'enabled': False}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('provider') not in {'manual', 'gotify'} or not isinstance(value.get('enabled'), bool):
            raise ValueError()
        if value['provider'] == 'gotify':
            value['url'] = _url(value.get('url'))
            if not isinstance(value.get('protected_token'), str) or len(value['protected_token']) > 16384:
                raise ValueError()
        return value
    except Exception:
        raise AppError('otp_config_invalid', 'The local OTP configuration could not be read.') from None


def _url(value):
    try:
        parsed = urlsplit(value)
        loopback = parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and loopback):
            raise ValueError()
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in value):
            raise ValueError()
        parsed.port
        return value.rstrip('/')
    except Exception:
        raise AppError('invalid_url', 'Use a Gotify HTTPS address or HTTP loopback address without credentials or query parameters.') from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AppError('otp_redirect_blocked', 'Gotify redirected; verify the configured server address.')


def _messages(config):
    if config.get('provider') != 'gotify' or not config.get('enabled'):
        raise AppError('otp_unavailable', 'Configure an enabled Gotify reader in local settings first.')
    token = _unprotect(config.get('protected_token', ''))
    address = _url(config.get('url', ''))
    req = Request(address + '/message?limit=100', headers={'X-Gotify-Key': token, 'Accept': 'application/json'})
    try:
        # Avoid environment proxy routing for the user-configured local/private source.
        from urllib.request import ProxyHandler
        with build_opener(ProxyHandler({}), _NoRedirect()).open(req, timeout=8) as response:
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError()
            payload = json.loads(data)
        items = payload.get('messages')
        if not isinstance(items, list) or len(items) > 100 or any(not isinstance(x, dict) for x in items):
            raise ValueError()
        return items
    except AppError:
        raise
    except Exception:
        raise AppError('otp_source_unavailable', 'Gotify messages could not be read. Check the address and client reading token.') from None


def _stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except Exception:
        return None


def _fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _test_result(code, checked_at=None):
    return {'provider': 'gotify', 'status': 'passed' if code == 'otp_test_passed' else 'failed',
            'checked_at': checked_at or datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
            'code': code, 'message': _TEST_MESSAGES[code], 'live_login_verified': False}


@contextmanager
def _test_db(app):
    with app._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS otp_connection_tests (
          user_id TEXT PRIMARY KEY, config_digest TEXT NOT NULL,
          checked_at TEXT NOT NULL, code TEXT NOT NULL)''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(otp_connection_tests)')}
        if 'user_id' not in columns:
            db.execute('ALTER TABLE otp_connection_tests RENAME TO otp_connection_tests_legacy')
            db.execute('''CREATE TABLE otp_connection_tests (
              user_id TEXT PRIMARY KEY, config_digest TEXT NOT NULL,
              checked_at TEXT NOT NULL, code TEXT NOT NULL)''')
            db.execute("INSERT INTO otp_connection_tests SELECT 'default',config_digest,checked_at,code FROM otp_connection_tests_legacy")
            db.execute('DROP TABLE otp_connection_tests_legacy')
        yield db


def _last_test(app, config):
    with _test_db(app) as db:
        row = db.execute('SELECT checked_at,code FROM otp_connection_tests WHERE user_id=? AND config_digest=?',
                         (app.user_id, _fingerprint(config))).fetchone()
    return _test_result(row['code'], row['checked_at']) if row and row['code'] in _TEST_MESSAGES else None


def _probe_connection(config):
    # Inspect only the response shape. Never extract, return or persist message bodies.
    try:
        token = _unprotect(config.get('protected_token', ''))
        request = Request(_url(config['url']) + '/message?limit=1',
                          headers={'X-Gotify-Key': token, 'Accept': 'application/json'})
        from urllib.request import ProxyHandler
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=8) as response:
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                return 'otp_invalid_response'
            payload = json.loads(data)
        messages = payload.get('messages') if isinstance(payload, dict) else None
        if not isinstance(messages, list) or len(messages) > 1 or any(
                not isinstance(message, dict) or isinstance(message.get('id'), bool)
                or not isinstance(message.get('id'), int) or message['id'] < 0 for message in messages):
            return 'otp_invalid_response'
        return 'otp_test_passed'
    except HTTPError as error:
        try:
            if error.code == 401:
                return 'otp_unauthorized'
            if error.code == 403:
                return 'otp_forbidden'
            if 300 <= error.code < 400:
                return 'otp_redirect_blocked'
            return 'otp_source_error' if error.code >= 500 else 'otp_invalid_response'
        finally:
            error.close()
    except URLError as error:
        if isinstance(error.reason, ssl.SSLError):
            return 'otp_tls_error'
        return 'otp_timeout' if isinstance(error.reason, TimeoutError) else 'otp_unreachable'
    except ssl.SSLError:
        return 'otp_tls_error'
    except TimeoutError:
        return 'otp_timeout'
    except (ValueError, UnicodeError, HTTPException, RecursionError):
        return 'otp_invalid_response'
    except OSError:
        return 'otp_unreachable'
    except AppError as error:
        return error.code if error.code in _TEST_MESSAGES else 'otp_invalid_response'


def _test_connection(app, args, actor):
    if actor != 'user':
        raise AppError('user_action_required', 'Test OTP access through the local user interface.', 403)
    if args:
        raise AppError('invalid_arguments', 'OTP connection testing accepts no fields; save the source first.')
    with gate(app.base_data_dir), _LOCK:
        config = _config(app)
        if config.get('provider') != 'gotify' or not config.get('enabled'):
            raise AppError('otp_unavailable', 'Configure an enabled Gotify reader in local settings first.')
    # Neither the module lock nor a database transaction spans the network wait.
    result = _test_result(_probe_connection(config))
    with gate(app.base_data_dir), _LOCK:
        with _test_db(app) as db:
            # The write lock also serializes against configuration saves in other processes.
            if _fingerprint(config) != _fingerprint(_config(app)):
                return _test_result('otp_source_changed')
            db.execute('''INSERT INTO otp_connection_tests VALUES(?,?,?,?)
                          ON CONFLICT(user_id) DO UPDATE SET config_digest=excluded.config_digest,
                          checked_at=excluded.checked_at,code=excluded.code
                          WHERE excluded.config_digest<>otp_connection_tests.config_digest
                             OR excluded.checked_at>=otp_connection_tests.checked_at''',
                       (app.user_id, _fingerprint(config), result['checked_at'], result['code']))
    return result


@contextmanager
def _db(app):
    with app._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS otp_windows (
          id TEXT PRIMARY KEY, config_digest TEXT NOT NULL, max_id INTEGER NOT NULL,
          baseline_ids TEXT NOT NULL, service TEXT NOT NULL, origin TEXT NOT NULL,
          sender_hint TEXT NOT NULL, started REAL NOT NULL, expires REAL NOT NULL,
          consumed INTEGER NOT NULL DEFAULT 0, user_id TEXT NOT NULL DEFAULT 'default')''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(otp_windows)')}
        if 'user_id' not in columns:
            db.execute("ALTER TABLE otp_windows ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")
        db.execute('CREATE INDEX IF NOT EXISTS otp_windows_user ON otp_windows(user_id,consumed,expires)')
        yield db


def _window(db, window_id, config, user_id):
    row = db.execute('SELECT * FROM otp_windows WHERE id=? AND user_id=?', (window_id, user_id)).fetchone()
    if row is None:
        raise AppError('otp_window_missing', 'OTP window is missing or belongs to another local profile.')
    window = dict(row)
    if window['config_digest'] != _fingerprint(config):
        raise AppError('otp_source_changed', 'The OTP source configuration changed. Begin a new login window.')
    if window['consumed']:
        raise AppError('otp_consumed', 'This OTP window has already been consumed or cancelled.')
    if time.time() > window['expires']:
        raise AppError('otp_expired', 'The OTP window has expired. Do not reuse an old code.')
    window['baseline_ids'] = set(json.loads(window['baseline_ids']))
    return window


def _configure(app, args, actor):
    if actor != 'user':
        raise AppError('user_action_required', 'Configure OTP credentials through the local user interface.', 403)
    if set(args) - {'provider', 'url', 'token', 'enabled'}:
        raise AppError('invalid_arguments', 'Unknown OTP configuration field.')
    provider = args.get('provider', 'manual')
    if not isinstance(provider, str) or provider not in {'manual', 'gotify'}:
        raise AppError('provider_unavailable', 'This OTP provider has not been implemented and verified in this release.')
    enabled = args.get('enabled', provider != 'manual')
    if not isinstance(enabled, bool):
        raise AppError('invalid_arguments', 'enabled must be boolean.')
    # Serialize config replacement with other process window creation/consumption.
    with _db(app) as db:
        config = _config(app)
        new = {'provider': provider, 'enabled': enabled and provider == 'gotify'}
        if provider == 'gotify':
            new['url'] = _url(args.get('url', ''))
            token = args.get('token', '')
            if not isinstance(token, str) or len(token) > 1024 or any(not 33 <= ord(c) <= 126 for c in token):
                raise AppError('invalid_token', 'Enter a valid Gotify client token.')
            if token:
                new['protected_token'] = _protect(token)
            elif config.get('url') == new['url'] and config.get('provider') == 'gotify':
                new['protected_token'] = config.get('protected_token', '')
            if not new.get('protected_token'):
                raise AppError('token_required', 'A Gotify client reading token is required for this server.')
        path = app.data_dir / 'otp.json'
        pending = path.with_name('otp-' + secrets.token_hex(8) + '.tmp')
        try:
            pending.write_text(json.dumps(new), encoding='utf-8')
            os.replace(pending, path)
            db.execute('UPDATE otp_windows SET consumed=1 WHERE consumed=0 AND user_id=?', (app.user_id,))
        finally:
            if pending.exists():
                pending.unlink()
    return {'configured': True, 'provider': provider, 'enabled': new['enabled']}


def _begin(app, config, args):
    if set(args) - {'service', 'origin', 'sender_hint'}:
        raise AppError('invalid_arguments', 'Unknown OTP request field.')
    service, origin = args.get('service'), args.get('origin')
    if not isinstance(service, str) or not 2 <= len(service.strip()) <= 80 or any(ord(c) < 32 for c in service):
        raise AppError('invalid_service', 'Use a specific company/service label expected in the SMS.')
    try:
        u = urlsplit(origin)
        if not isinstance(origin, str) or any(c.isspace() for c in origin) or u.scheme != 'https' or not u.hostname or u.username or u.password or u.path not in {'', '/'} or u.query or u.fragment or u.port == 0:
            raise ValueError()
    except Exception:
        raise AppError('invalid_origin', 'Specify the exact verified HTTPS recruiting origin.') from None
    hint = args.get('sender_hint') or ''
    if not isinstance(hint, str) or len(hint) > 100 or any(ord(c) < 32 for c in hint):
        raise AppError('invalid_service', 'Sender hint is invalid.')
    messages = _messages(config)
    ids = [m.get('id') for m in messages]
    if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in ids):
        raise AppError('otp_source_shape', 'Message identifiers could not be verified.')
    current = time.time()
    wid = secrets.token_urlsafe(20)
    with _db(app) as db:
        if _fingerprint(config) != _fingerprint(_config(app)):
            raise AppError('otp_source_changed', 'The OTP source configuration changed. Begin a new login window.')
        db.execute('DELETE FROM otp_windows WHERE expires < ? AND user_id=?', (current - TTL, app.user_id))
        db.execute('UPDATE otp_windows SET consumed=1 WHERE config_digest<>? AND user_id=?', (_fingerprint(config), app.user_id))
        if db.execute('SELECT 1 FROM otp_windows WHERE consumed=0 AND expires>=? AND user_id=?', (current, app.user_id)).fetchone():
            raise AppError('otp_window_active', 'An OTP window is already active. Finish or cancel it before beginning another.')
        db.execute('''INSERT INTO otp_windows
                   (id,config_digest,max_id,baseline_ids,service,origin,sender_hint,started,expires,consumed,user_id)
                   VALUES(?,?,?,?,?,?,?,?,?,0,?)''',
                   (wid, _fingerprint(config), max(ids, default=0), json.dumps(ids), service.strip().casefold(), origin, hint.casefold(), current, current + TTL, app.user_id))
    return {'window_id': wid, 'expires_at': datetime.fromtimestamp(current + TTL, timezone.utc).isoformat(), 'next_action': 'Request one code on the verified website, then poll this window.'}


def _poll(app, config, args, cancel=False):
    if set(args) != {'window_id'} or not isinstance(args.get('window_id'), str) or len(args['window_id']) > 100:
        raise AppError('invalid_arguments', 'Supply only the active window_id.')
    with _db(app) as db:
        window = _window(db, args['window_id'], config, app.user_id)
    # No network traffic while holding a database write lock.
    messages = [] if cancel else _messages(config)
    candidates = []
    for message in messages:
        ident = message.get('id')
        if isinstance(ident, bool) or not isinstance(ident, int) or ident <= window['max_id'] or ident in window['baseline_ids']:
            continue
        stamp = _stamp(message.get('date'))
        if stamp is None or stamp < window['started'] - 3 or stamp > time.time() + 30:
            continue
        body = str(message.get('title', '')) + '\n' + str(message.get('message', ''))
        lower = body.casefold()
        if window['service'] not in lower or (window['sender_hint'] and window['sender_hint'] not in lower):
            continue
        # Numbers must be attached to an OTP label, not arbitrary phone numbers/dates.
        found = re.findall(r'(?:验证码|校验码|动态码|verification\s+code|security\s+code|otp)\D{0,16}(\d{4,8})(?!\d)', body, re.I)
        if not found:
            found = re.findall(r'(?<!\d)(\d{4,8})\D{0,16}(?:为您的验证码|is your (?:verification |security )?code)', body, re.I)
        for code in set(found):
            candidates.append((ident, code))
    ambiguous = len(candidates) > 1
    with _db(app) as db:
        # Re-read after the network wait: another process may consume or configure.
        current = _window(db, args['window_id'], _config(app), app.user_id)
        if cancel or candidates:
            db.execute('UPDATE otp_windows SET consumed=1 WHERE id=? AND user_id=?', (args['window_id'], app.user_id))
    # Raise ambiguity only after the consumed marker is committed.
    if cancel:
        return {'cancelled': True}
    if ambiguous:
        raise AppError('otp_ambiguous', 'More than one matching code arrived. User review is required; this window is closed.')
    if not candidates:
        return {'status': 'pending', 'expires_at': datetime.fromtimestamp(current['expires'], timezone.utc).isoformat()}
    return {'status': 'received', 'code': candidates[0][1], 'origin': current['origin'], 'consumed': True}


def execute(app, operation, args, actor):
    args = args or {}
    if not isinstance(args, dict):
        raise AppError('invalid_arguments', 'OTP arguments must be an object.')
    try:
        if operation == 'otp.test':
            return _test_connection(app, args, actor)
        with _LOCK:
            config = _config(app)
            if operation == 'otp.status':
                return {'provider': config.get('provider', 'manual'), 'enabled': bool(config.get('enabled')), 'url': config.get('url', ''), 'token_configured': bool(config.get('protected_token')), 'supported': ['manual', 'gotify'], 'kde': 'not_implemented', 'phone_link': 'not_implemented', 'live_login_verified': False, 'connection_test': _last_test(app, config)}
            if operation == 'otp.configure':
                return _configure(app, args, actor)
            if operation == 'otp.begin':
                return _begin(app, config, args)
            if operation in {'otp.poll', 'otp.cancel'}:
                return _poll(app, config, args, cancel=operation == 'otp.cancel')
            raise AppError('unknown_operation', 'Unknown OTP operation.')
    except (OSError, sqlite3.Error):
        raise AppError('otp_storage_error', 'Local OTP metadata could not be accessed.') from None
