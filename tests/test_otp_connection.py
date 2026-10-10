import io
import json
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler

from service.app import App, AppError
from service import otp


class OtpConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(data_dir=self.tmp.name)
        self.token = 'synthetic-client-test-token'
        self.body = json.dumps({'messages': [{'id': 1, 'message': 'Synthetic private SMS code 839271'}]}).encode()
        self.response_status = 200
        self.response_headers = {}
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append({'path': self.path, 'method': self.command,
                                       'token_matches': self.headers.get('X-Gotify-Key') == owner.token})
                self.send_response(owner.response_status)
                self.send_header('Content-Length', str(len(owner.body)))
                for key, value in owner.response_headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(owner.body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.config_args = {'provider': 'gotify', 'url': self.url, 'token': self.token, 'enabled': True}
        self.protect = patch.object(otp, '_protect', return_value='synthetic-test-ciphertext')
        self.unprotect = patch.object(otp, '_unprotect', return_value=self.token)
        self.protect.start()
        self.unprotect.start()
        self.addCleanup(self.protect.stop)
        self.addCleanup(self.unprotect.stop)
        self.app.execute('otp.configure', self.config_args, actor='user')

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def run_probe(self):
        return self.app.execute('otp.test', {}, actor='user')

    def test_success_persists_redacted_result_without_consuming_a_window(self):
        with patch.object(otp, '_messages', return_value=[]):
            self.app.execute('otp.begin', {'service': 'ExampleCorp', 'origin': 'https://careers.example.com'})
        with self.app._connect() as db:
            windows_before = [tuple(row) for row in db.execute('SELECT * FROM otp_windows')]
        config_before = Path(self.tmp.name, 'otp.json').read_bytes()
        self.assertIsNone(self.app.execute('otp.status')['connection_test'])
        result = self.run_probe()
        self.assertEqual('passed', result['status'])
        self.assertEqual('otp_test_passed', result['code'])
        self.assertFalse(result['live_login_verified'])
        self.assertEqual([{'path': '/message?limit=1', 'method': 'GET', 'token_matches': True}], self.requests)
        self.assertEqual(config_before, Path(self.tmp.name, 'otp.json').read_bytes())
        status = App(data_dir=self.tmp.name).execute('otp.status')
        self.assertEqual(result, status['connection_test'])
        with self.app._connect() as db:
            self.assertEqual(windows_before, [tuple(row) for row in db.execute('SELECT * FROM otp_windows')])
            persisted = '\n'.join(db.iterdump())
        for private in [self.token, '839271', 'Synthetic private SMS', 'synthetic-test-ciphertext']:
            self.assertNotIn(private, persisted)
            self.assertNotIn(private, json.dumps(result))
            self.assertNotIn(private, json.dumps(status))

    def test_empty_inbox_is_a_valid_read_permission_test(self):
        self.body = b'{"messages":[]}'
        self.assertEqual('passed', self.run_probe()['status'])

    def test_http_failures_are_distinguished_and_do_not_unsave_configuration(self):
        config_before = Path(self.tmp.name, 'otp.json').read_bytes()
        for status, code in [(401, 'otp_unauthorized'), (403, 'otp_forbidden'),
                             (404, 'otp_invalid_response'), (503, 'otp_source_error')]:
            with self.subTest(status=status):
                self.response_status = status
                self.body = b'private diagnostic that must never be returned'
                result = self.run_probe()
                self.assertEqual('failed', result['status'])
                self.assertEqual(code, result['code'])
                saved = self.app.execute('otp.status')
                self.assertTrue(saved['enabled'])
                self.assertTrue(saved['token_configured'])
                self.assertEqual(result, saved['connection_test'])
                self.assertNotIn('private diagnostic', json.dumps(result))
                self.assertEqual(config_before, Path(self.tmp.name, 'otp.json').read_bytes())

    def test_redirect_does_not_forward_token_or_fetch_another_page(self):
        self.response_status = 302
        self.response_headers = {'Location': self.url + '/must-not-be-requested'}
        self.assertEqual('otp_redirect_blocked', self.run_probe()['code'])
        self.assertEqual(1, len(self.requests))
        self.assertEqual('/message?limit=1', self.requests[0]['path'])

    def test_wrong_service_malformed_or_oversize_payloads(self):
        for body in [b'<html>Another service</html>', b'{}', b'[]', b'{"messages":{}}',
                     b'{"messages":[{"id":true}]}', b'{"messages":[{"id":1},{"id":2}]}',
                     b'x' * (1024 * 1024 + 1)]:
            with self.subTest(length=len(body)):
                self.body = body
                self.assertEqual('otp_invalid_response', self.run_probe()['code'])

    def test_network_failures_have_safe_specific_results(self):
        cases = [(TimeoutError('private server details'), 'otp_timeout'),
                 (URLError(TimeoutError('private server details')), 'otp_timeout'),
                 (URLError(ConnectionRefusedError('private server details')), 'otp_unreachable'),
                 (ssl.SSLCertVerificationError('private certificate details'), 'otp_tls_error'),
                 (URLError(ssl.SSLCertVerificationError('private certificate details')), 'otp_tls_error'),
                 (HTTPError(self.url, 307, 'private redirect', {}, io.BytesIO(b'private')), 'otp_redirect_blocked')]
        for error, code in cases:
            with self.subTest(code=code):
                opener = Mock()
                opener.open.side_effect = error
                with patch.object(otp, 'build_opener', return_value=opener) as build:
                    result = self.run_probe()
                self.assertEqual(code, result['code'])
                self.assertEqual('failed', result['status'])
                self.assertNotIn('private', json.dumps(result))
                handlers = build.call_args.args
                self.assertTrue(any(isinstance(handler, ProxyHandler) and handler.proxies == {} for handler in handlers))
                self.assertTrue(any(isinstance(handler, otp._NoRedirect) for handler in handlers))
                self.assertEqual(8, opener.open.call_args.kwargs['timeout'])

    def test_user_only_and_saved_configuration_only(self):
        with patch.object(otp, '_probe_connection') as probe:
            for args, actor, code in [({}, 'codex', 'user_action_required'),
                                      ({'url': self.url}, 'user', 'invalid_arguments')]:
                with self.assertRaises(AppError) as ctx:
                    self.app.execute('otp.test', args, actor=actor)
                self.assertEqual(code, ctx.exception.code)
            self.app.execute('otp.configure', {'provider': 'manual'}, actor='user')
            with self.assertRaises(AppError) as ctx:
                self.run_probe()
            self.assertEqual('otp_unavailable', ctx.exception.code)
            probe.assert_not_called()

    def test_decryption_failure_returns_safe_failure_without_network(self):
        with patch.object(otp, '_unprotect', side_effect=AppError('secret_unavailable', 'private failure detail')):
            result = self.run_probe()
        self.assertEqual('secret_unavailable', result['code'])
        self.assertNotIn('private failure detail', json.dumps(result))
        self.assertFalse(self.requests)

    def test_configuration_changes_hide_previous_result(self):
        self.run_probe()
        self.app.execute('otp.configure', dict(self.config_args, token='', url=self.url + '/'), actor='user')
        self.assertIsNotNone(self.app.execute('otp.status')['connection_test'])
        self.app.execute('otp.configure', dict(self.config_args, url=self.url + '/another'), actor='user')
        self.assertIsNone(self.app.execute('otp.status')['connection_test'])

    def test_network_wait_allows_status_and_configuration_change_discards_result(self):
        started, release, status_read = threading.Event(), threading.Event(), threading.Event()
        result = []

        def probe(config):
            started.set()
            if not release.wait(5):
                raise AssertionError('Connection probe was not released')
            return 'otp_test_passed'

        def read_status():
            self.app.execute('otp.status')
            status_read.set()

        with patch.object(otp, '_probe_connection', side_effect=probe):
            worker = threading.Thread(target=lambda: result.append(self.run_probe()), daemon=True)
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                reader = threading.Thread(target=read_status, daemon=True)
                reader.start()
                self.assertTrue(status_read.wait(2), 'Status must not wait for the network probe')
                App(data_dir=self.tmp.name).execute('otp.configure', {'provider': 'manual'}, actor='user')
            finally:
                release.set()
                worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual('otp_source_changed', result[0]['code'])
        self.assertIsNone(self.app.execute('otp.status')['connection_test'])
        with self.app._connect() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM otp_connection_tests').fetchone()[0])

    def test_delayed_older_result_cannot_replace_a_newer_test(self):
        ready, release = threading.Event(), threading.Event()
        results = []
        original_result = otp._test_result
        worker = threading.Thread(target=lambda: results.append(self.run_probe()), daemon=True)

        def result_with_delay(code, checked_at=None):
            result = original_result(code, checked_at)
            if checked_at is None:
                is_old = threading.current_thread() is worker
                result['checked_at'] = '2026-01-01T00:00:01.000+00:00' if is_old else '2026-01-01T00:00:02.000+00:00'
                if is_old:
                    ready.set()
                    if not release.wait(5):
                        raise AssertionError('Delayed test result was not released')
            return result

        def probe(config):
            return 'otp_test_passed' if threading.current_thread() is worker else 'otp_timeout'

        with patch.object(otp, '_probe_connection', side_effect=probe), patch.object(otp, '_test_result', side_effect=result_with_delay):
            worker.start()
            try:
                self.assertTrue(ready.wait(2))
                latest = self.run_probe()
            finally:
                release.set()
                worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual('passed', results[0]['status'])
        self.assertEqual('otp_timeout', latest['code'])
        self.assertEqual(latest, self.app.execute('otp.status')['connection_test'])


if __name__ == '__main__':
    unittest.main()
