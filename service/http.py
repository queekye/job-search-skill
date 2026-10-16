"""Loopback-only local UI with session and exact-origin checks."""
from __future__ import annotations

import argparse
import base64
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit

from .app import App, AppError
from .locking import gate

MAX_REQUEST_BYTES = 21 * 1024 * 1024
SESSION_SECONDS = 8 * 60 * 60
COOKIE_NAME = "career_session"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/forms.js": ("forms.js", "text/javascript; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8"),
          "/catalog.html": ("catalog.html", "text/html; charset=utf-8"),
          "/catalog.js": ("catalog.js", "text/javascript; charset=utf-8")}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, app, host="127.0.0.1", port=8766):
        if host != "127.0.0.1":
            raise AppError("unsafe_host", "The local service binds only to 127.0.0.1.")
        self.app = app
        self.sessions = {}
        self.session_accounts = {}
        self.session_lock = threading.Lock()
        super().__init__((host, port), Handler)
        from .sync import start_worker
        self.sync_worker = start_worker(app)
        from .mailbox import start_worker as start_mail_worker
        self.mail_worker = start_mail_worker(app)

    def server_close(self):
        self.mail_worker.stop()
        self.sync_worker.stop()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = "CareerLocal/0.1"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, format, *args):
        # Access logs may contain private identifiers and must remain disabled.
        pass

    def _host(self):
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if len(hosts) != 1 or hosts[0] not in allowed:
            raise AppError("invalid_host", "Use the loopback service address.", 403)
        return hosts[0]

    def _session(self, required=True):
        try:
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            item = cookie.get(COOKIE_NAME)
            token = item.value if item else ""
        except CookieError:
            token = ""
        instant = time.monotonic()
        with self.server.session_lock:
            self.server.sessions = {key: expiry for key, expiry in self.server.sessions.items() if expiry > instant}
            self.server.session_accounts = {key: account for key, account in self.server.session_accounts.items()
                                            if key in self.server.sessions}
            valid = token in self.server.sessions
        if required and not valid:
            raise AppError("session_required", "Open the local home page to establish a session.", 403)
        return token if valid else None

    def _new_session(self):
        token = secrets.token_urlsafe(32)
        with self.server.session_lock:
            if len(self.server.sessions) >= 64:
                oldest = min(self.server.sessions, key=self.server.sessions.get)
                del self.server.sessions[oldest]
                self.server.session_accounts.pop(oldest, None)
            self.server.sessions[token] = time.monotonic() + SESSION_SECONDS
            self.server.session_accounts[token] = self.server.app.user_id
        return token

    def _bound_app(self, token, expected=None, require_context=False):
        """Capture a request's account without mutating the shared server App."""
        with self.server.session_lock:
            user_id = self.server.session_accounts.get(token, self.server.app.user_id)
        if expected is not None and (not isinstance(expected, str) or expected != user_id):
            raise AppError("account_changed", "The local account changed. Reload before continuing.", 409)
        if require_context and expected is None and user_id != "default":
            raise AppError("account_context_required", "Read the selected account and include user_id before continuing.", 409)
        try:
            return self.server.app.for_user(user_id)
        except AppError as error:
            if error.code != "not_found":
                raise
            with self.server.session_lock:
                self.server.session_accounts[token] = "default"
            raise AppError("account_changed", "同步后账号已变化，请重新读取当前账号。", 409) from None

    def _headers(self, status, content_type, length, session=None, download=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        if session:
            self.send_header("Set-Cookie", f"{COOKIE_NAME}={session}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_SECONDS}")
        if download:
            self.send_header("Content-Disposition", "attachment; filename=\"document\"; filename*=UTF-8''" + quote(download, safe=""))
        self.end_headers()

    def _json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _error(self, error):
        self._json({"ok": False, "error": {"code": error.code, "message": error.message}}, error.status)

    def do_GET(self):
        try:
            host = self._host()
            parsed = urlsplit(self.path)
            path = parsed.path
            if path.startswith("/packages/"):
                match = re.fullmatch(r"/packages/([a-z0-9][a-z0-9.-]{0,80})\.zip", path)
                if not match:
                    raise AppError("not_found", "Public company package was not found.", 404)
                result = self.server.app.execute("resource.export", {"id": match.group(1), "source": "bundled"}, actor="user")
                content = base64.b64decode(result["content_base64"], validate=True)
                self._headers(200, "application/zip", len(content), download=result["filename"])
                self.wfile.write(content)
                return
            if path.startswith("/api/"):
                token = self._session()
                origin = self.headers.get("Origin")
                if origin is not None and origin != "http://" + host:
                    raise AppError("origin_rejected", "Request origin does not match the local service.", 403)
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) - {"user_id"} or len(query.get("user_id", [])) > 1:
                    raise AppError("invalid_request", "API query contains unknown or repeated fields.")
                expected = query.get("user_id", [None])[0]
                app = self._bound_app(token, expected)
                if path == "/api/accounts":
                    self._json({"ok": True, "data": app.execute("account.list")})
                    return
                if path == "/api/state":
                    self._json({"ok": True, "data": app.execute("state")})
                    return
                prefix = "/api/documents/"
                if path.startswith(prefix):
                    document_id = path[len(prefix):]
                    with gate(self.server.app.base_data_dir):
                        source, metadata = self._bound_app(token, expected, require_context=True).document_download(document_id)
                        content = source.read_bytes()
                    self._headers(200, metadata["mime_type"], len(content), download=metadata["original_name"])
                    self.wfile.write(content)
                    return
                raise AppError("not_found", "API endpoint was not found.", 404)
            if path not in ASSETS:
                raise AppError("not_found", "Page was not found.", 404)
            filename, content_type = ASSETS[path]
            asset = self.server.app.root_dir / "web" / filename
            if not asset.is_file():
                raise AppError("not_found", "The local page asset is unavailable.", 404)
            content = asset.read_bytes()
            session = None
            if path in {"/", "/index.html", "/catalog.html"} and not self._session(False):
                session = self._new_session()
            self._headers(200, content_type, len(content), session=session)
            self.wfile.write(content)
        except AppError as error:
            self._error(error)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception:
            self._error(AppError("internal_error", "The local service could not complete this request.", 500))

    def do_POST(self):
        self._body_consumed = False
        try:
            host = self._host()
            token = self._session()
            if self.headers.get_all("Origin", []) != ["http://" + host]:
                raise AppError("origin_rejected", "Request origin does not match the local service.", 403)
            if self.path != "/api/action":
                raise AppError("not_found", "API endpoint was not found.", 404)
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                raise AppError("invalid_request", "A single Content-Length header is required.")
            try:
                length = int(lengths[0])
            except ValueError:
                raise AppError("invalid_request", "Content-Length is invalid.") from None
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise AppError("request_too_large", "Request body is too large.", 413)
            if self.headers.get_content_type() != "application/json":
                raise AppError("invalid_request", "Requests must use application/json.", 415)
            raw = self.rfile.read(length)
            self._body_consumed = True
            if len(raw) != length:
                raise AppError("invalid_request", "Request body was incomplete.")
            try:
                payload = json.loads(raw.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            except (ValueError, RecursionError):
                raise AppError("invalid_request", "Request body must be valid JSON.") from None
            if not isinstance(payload, dict) or set(payload) - {"operation", "args", "user_id"}:
                raise AppError("invalid_request", "Request contains unknown fields.")
            app = self._bound_app(token, payload.get("user_id"), require_context=True)
            if payload.get("operation") == "account.select":
                args = payload.get("args")
                if not isinstance(args, dict) or set(args) != {"id"}:
                    raise AppError("invalid_request", "Account selection requires only an account id.")
                selected = self.server.app.for_user(args["id"])
                result = selected.execute("account.get")
                with self.server.session_lock:
                    # A concurrent tab switch must not silently replace a newer selection.
                    if self.server.session_accounts.get(token) != app.user_id:
                        raise AppError("account_changed", "The local account changed. Reload before continuing.", 409)
                    self.server.session_accounts[token] = selected.user_id
            else:
                result = app.execute(payload.get("operation"), payload.get("args"), actor="user")
            self._json({"ok": True, "data": result})
        except AppError as error:
            # Closing a Windows socket with a small unread POST body can reset it
            # before the client receives the rejection. Drain bounded bodies only.
            if not self._body_consumed and not self.headers.get("Transfer-Encoding"):
                lengths = self.headers.get_all("Content-Length", [])
                try:
                    length = int(lengths[0]) if len(lengths) == 1 else -1
                    if 0 <= length <= MAX_REQUEST_BYTES:
                        self.rfile.read(length)
                except (ValueError, OSError):
                    pass
            self._error(error)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception:
            self._error(AppError("internal_error", "The local service could not complete this request.", 500))

    def do_OPTIONS(self):
        self._error(AppError("method_not_allowed", "Cross-origin requests are not supported.", 405))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the private Job Search Skill interface on loopback.")
    parser.add_argument("--data-dir")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", choices=["127.0.0.1"], default="127.0.0.1")
    options = parser.parse_args(argv)
    server = LocalServer(App(data_dir=options.data_dir, user_id=options.user_id), options.host, options.port)
    print(f"Local interface: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
