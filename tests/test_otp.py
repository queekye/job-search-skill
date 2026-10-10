import http.client
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from service.app import App, AppError
from service.http import LocalServer
from service import otp


class OtpConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(data_dir=self.tmp.name)
        self.path = Path(self.tmp.name, 'otp.json')
        self.args = {'provider': 'gotify', 'enabled': True,
                     'url': 'http://127.0.0.1:12345', 'token': 'synthetic-reading-token'}

    def configure(self, args=None):
        return self.app.execute('otp.configure', self.args if args is None else args, actor='user')

    def save_synthetic_config(self):
        with patch.object(otp, '_protect', return_value='synthetic-protected-token'):
            self.configure()

    def test_save_and_blank_token_survive_new_app(self):
        self.save_synthetic_config()
        saved = self.path.read_bytes()
        with patch.object(otp, '_protect', side_effect=AssertionError('Blank token must preserve the saved ciphertext')):
            self.configure(dict(self.args, token='', url=self.args['url'] + '/'))
        self.assertEqual(saved, self.path.read_bytes())
        status = App(data_dir=self.tmp.name).execute('otp.status')
        self.assertEqual('gotify', status['provider'])
        self.assertEqual(self.args['url'], status['url'])
        self.assertTrue(status['enabled'])
        self.assertTrue(status['token_configured'])
        self.assertNotIn('synthetic', json.dumps(status))

    def test_new_source_needs_token_and_preserves_previous_config(self):
        self.save_synthetic_config()
        saved = self.path.read_bytes()
        with self.assertRaises(AppError) as ctx:
            self.configure(dict(self.args, url='https://other-source.example', token=''))
        self.assertEqual('token_required', ctx.exception.code)
        self.assertEqual(saved, self.path.read_bytes())

    def test_protection_failure_preserves_previous_config(self):
        self.save_synthetic_config()
        saved = self.path.read_bytes()
        with patch.object(otp, '_protect', side_effect=AppError('secret_storage_failed', 'Synthetic failure.')):
            with self.assertRaises(AppError) as ctx:
                self.configure()
        self.assertEqual('secret_storage_failed', ctx.exception.code)
        self.assertEqual(saved, self.path.read_bytes())

    def test_failed_replace_preserves_previous_config_and_cleans_temporary_file(self):
        self.save_synthetic_config()
        saved = self.path.read_bytes()
        with patch.object(otp.os, 'replace', side_effect=OSError('Synthetic failure.')):
            with self.assertRaises(AppError) as ctx:
                self.configure(dict(self.args, token='', enabled=False))
        self.assertEqual('otp_storage_error', ctx.exception.code)
        self.assertEqual(saved, self.path.read_bytes())
        self.assertFalse(list(Path(self.tmp.name).glob('otp-*.tmp')))

    def test_manual_mode_clears_saved_source_and_token(self):
        self.save_synthetic_config()
        self.configure({'provider': 'manual'})
        status = App(data_dir=self.tmp.name).execute('otp.status')
        self.assertEqual('manual', status['provider'])
        self.assertFalse(status['enabled'])
        self.assertFalse(status['token_configured'])
        self.assertEqual('', status['url'])
        self.assertNotIn('protected_token', json.loads(self.path.read_text(encoding='utf-8')))

    @unittest.skipUnless(os.name == 'nt', 'CurrentUser DPAPI requires Windows')
    def test_windows_dpapi_save_and_read_in_another_process(self):
        self.configure()
        saved = self.path.read_text(encoding='utf-8')
        self.assertNotIn(self.args['token'], saved)
        protected = json.loads(saved)['protected_token']
        self.assertEqual(self.args['token'], otp._unprotect(protected))
        self.configure(dict(self.args, token=''))
        self.assertEqual(saved, self.path.read_text(encoding='utf-8'))
        # This is a fresh process with its own App and no in-memory configuration.
        code = '''import json,sys
from service.app import App
from service import otp
app=App(data_dir=sys.argv[1])
config=otp._config(app)
status=app.execute('otp.status')
print(json.dumps({'status':status,'token_matches':otp._unprotect(config['protected_token'])==sys.stdin.read()}))
'''
        child = subprocess.run([sys.executable, '-B', '-c', code, self.tmp.name],
                               input=self.args['token'], text=True, capture_output=True,
                               timeout=15, check=True)
        result = json.loads(child.stdout)
        self.assertTrue(result['token_matches'])
        self.assertTrue(result['status']['token_configured'])
        self.assertTrue(result['status']['enabled'])
        self.assertEqual(self.args['url'], result['status']['url'])
        self.assertNotIn(self.args['token'], child.stdout)

    @unittest.skipUnless(os.name == 'nt', 'CurrentUser DPAPI requires Windows')
    def test_windows_http_save_persists_and_status_redacts_token(self):
        server = LocalServer(self.app, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request('GET', '/')
            response = connection.getresponse()
            cookie = response.getheader('Set-Cookie').split(';', 1)[0]
            response.read()
            headers = {'Cookie': cookie, 'Content-Type': 'application/json',
                       'Origin': f'http://127.0.0.1:{server.server_port}'}
            with patch.object(otp, '_messages', side_effect=AssertionError('Saving must not contact an OTP source')):
                for token in [self.args['token'], '']:
                    body = json.dumps({'operation': 'otp.configure', 'args': dict(self.args, token=token)})
                    connection.request('POST', '/api/action', body=body, headers=headers)
                    response = connection.getresponse()
                    result = json.loads(response.read())
                    self.assertEqual(200, response.status)
                    self.assertTrue(result['ok'])
                    self.assertTrue(result['data']['configured'])
                    self.assertNotIn(self.args['token'], json.dumps(result))
                connection.request('POST', '/api/action',
                                   body=json.dumps({'operation': 'otp.status', 'args': {}}), headers=headers)
                response = connection.getresponse()
                result = json.loads(response.read())
                self.assertEqual(200, response.status)
                self.assertTrue(result['data']['token_configured'])
                self.assertNotIn(self.args['token'], json.dumps(result))
            status = App(data_dir=self.tmp.name).execute('otp.status')
            self.assertEqual(result['data'], status)
            stored = json.loads(self.path.read_text(encoding='utf-8'))
            self.assertEqual(self.args['token'], otp._unprotect(stored['protected_token']))
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class OtpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(data_dir=self.tmp.name)
        self.config = {'provider': 'gotify', 'enabled': True, 'url': 'http://127.0.0.1:12345', 'protected_token': 'not-a-real-secret'}
        Path(self.tmp.name, 'otp.json').write_text(json.dumps(self.config), encoding='utf-8')
        self.messages = []
        self.mock = patch.object(otp, '_messages', side_effect=lambda _: self.messages)
        self.mock.start()
    def tearDown(self):
        self.mock.stop()
        self.tmp.cleanup()
    def call(self, op, args=None, actor='codex'):
        return otp.execute(self.app, 'otp.' + op, args or {}, actor)
    def message(self, ident, body='ExampleCorp 验证码：123456，三分钟有效。'):
        return {'id': ident, 'title': '短信', 'message': body, 'date': datetime.now(timezone.utc).isoformat()}
    def begin(self):
        return self.call('begin', {'service': 'ExampleCorp', 'origin': 'https://careers.example.com'})['window_id']
    def test_baseline_service_and_consumption(self):
        self.messages = [self.message(1)]
        wid = self.begin()
        self.messages += [self.message(2, 'OtherCorp 验证码：999999')]
        self.assertEqual(self.call('poll', {'window_id': wid})['status'], 'pending')
        self.messages += [self.message(3)]
        result = self.call('poll', {'window_id': wid})
        self.assertEqual(result['code'], '123456')
        with self.assertRaises(AppError) as ctx:
            self.call('poll', {'window_id': wid})
        self.assertEqual(ctx.exception.code, 'otp_consumed')
        self.assertNotIn('123456', Path(self.tmp.name, 'otp.json').read_text())
    def test_ambiguity_and_expiry(self):
        wid = self.begin()
        self.messages = [self.message(1), self.message(2, 'ExampleCorp 验证码 654321')]
        with self.assertRaises(AppError) as ctx:
            self.call('poll', {'window_id': wid})
        self.assertEqual(ctx.exception.code, 'otp_ambiguous')
        wid = self.begin()
        with self.app._connect() as db:
            db.execute('UPDATE otp_windows SET expires=? WHERE id=?', (time.time() - 1, wid))
        with self.assertRaises(AppError) as ctx:
            self.call('poll', {'window_id': wid})
        self.assertEqual(ctx.exception.code, 'otp_expired')
    def test_no_credentials_in_status_and_no_model_configure(self):
        result = self.call('status')
        self.assertNotIn('not-a-real-secret', json.dumps(result))
        with self.assertRaises(AppError):
            self.call('configure', {'provider': 'manual'})
    def test_unsupported_source_and_bad_url(self):
        with self.assertRaises(AppError):
            self.call('configure', {'provider': 'kde'}, 'user')
        for value in ['http://example.com', 'https://x.example/?token=abc', 'https://user:pass@x.example']:
            with self.assertRaises(AppError):
                otp._url(value)
    def test_single_window_and_cancel(self):
        wid = self.begin()
        with self.assertRaises(AppError):
            self.begin()
        self.call('cancel', {'window_id': wid})
        self.assertIsInstance(self.begin(), str)

    def test_changed_source_blocks_before_read_and_allows_new_window(self):
        wid = self.begin()
        changed = dict(self.config, url='https://other-source.example')
        Path(self.tmp.name, 'otp.json').write_text(json.dumps(changed), encoding='utf-8')
        with patch.object(otp, '_messages', side_effect=AssertionError('Must not read a changed source')):
            with self.assertRaises(AppError) as ctx:
                self.call('poll', {'window_id': wid})
            self.assertEqual('otp_source_changed', ctx.exception.code)
        self.assertNotEqual(wid, self.begin())

    def test_configure_invalidates_another_app_window(self):
        wid = self.begin()
        second = App(data_dir=self.tmp.name)
        otp.execute(second, 'otp.configure', {'provider': 'manual'}, 'user')
        with self.assertRaises(AppError) as ctx:
            self.call('poll', {'window_id': wid})
        self.assertEqual('otp_source_changed', ctx.exception.code)

    def child(self, operation, args, messages):
        code = '''import json,sys
from service.app import App,AppError
from service import otp
payload=json.loads(sys.stdin.read())
otp._messages=lambda config:payload['messages']
try:
    result=otp.execute(App(sys.argv[1]),payload['operation'],payload['args'],'codex')
except AppError as error:
    result={'error':error.code}
print(json.dumps(result))
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, self.tmp.name],
                                input=json.dumps({'operation': operation, 'args': args, 'messages': messages}),
                                text=True, capture_output=True, timeout=15, check=True)
        return json.loads(result.stdout)

    def test_cross_process_active_window_and_one_time_consumption(self):
        wid = self.begin()
        conflict = self.child('otp.begin', {'service': 'ExampleCorp', 'origin': 'https://careers.example.com'}, [])
        self.assertEqual('otp_window_active', conflict['error'])
        self.messages = [self.message(1)]
        received = self.child('otp.poll', {'window_id': wid}, self.messages)
        self.assertEqual('123456', received['code'])
        with self.assertRaises(AppError) as ctx:
            self.call('poll', {'window_id': wid})
        self.assertEqual('otp_consumed', ctx.exception.code)
        with self.app._connect() as db:
            persisted = '\n'.join(db.iterdump())
        self.assertNotIn('123456', persisted)
        self.assertNotIn('三分钟有效', persisted)
        self.assertNotIn('not-a-real-secret', persisted)

    def test_boolean_message_id_and_invalid_origin(self):
        wid = self.begin()
        self.messages = [self.message(True)]
        self.assertEqual('pending', self.call('poll', {'window_id': wid})['status'])
        self.call('cancel', {'window_id': wid})
        for origin in ['https://careers.example.com:invalid', 'https://careers.example.com:0', 'https://careers.example.com\n']:
            with self.assertRaises(AppError) as ctx:
                self.call('begin', {'service': 'ExampleCorp', 'origin': origin})
            self.assertEqual('invalid_origin', ctx.exception.code)
        with self.assertRaises(AppError) as ctx:
            self.call('configure', {'provider': []}, 'user')
        self.assertEqual('provider_unavailable', ctx.exception.code)


if __name__ == '__main__':
    unittest.main()
