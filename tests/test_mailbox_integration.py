"""Synthetic HTTP/MCP boundaries for recruitment-mail ingestion."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from service.app import App, AppError
from service.http import LocalServer
from service.mcp import call_tool, tool_definitions


class MailIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='career-mail-integration-')
        base = Path(self.tmp.name)
        root = base / 'source'
        (root / 'web').mkdir(parents=True)
        (root / 'web' / 'index.html').write_text('<!doctype html><title>Synthetic</title>', encoding='utf-8')
        self.app = App(base / 'private', root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mcp_mail_contract_excludes_credentials_and_exposes_link_revision(self):
        tool = next(item for item in tool_definitions() if item['name'] == 'career_mail')
        self.assertFalse(tool['annotations']['readOnlyHint'])
        self.assertTrue(tool['annotations']['openWorldHint'])
        self.assertEqual({'mail.status', 'mail.list', 'mail.sync', 'mail.link'},
                         set(tool['inputSchema']['properties']['operation']['enum']))
        branches = {item['properties']['operation']['const']: item for item in tool['inputSchema']['oneOf']}
        self.assertEqual(['id', 'revision', 'application_id'],
                         branches['mail.link']['properties']['args']['required'])
        self.assertNotIn('authorization_code', json.dumps(tool))
        with self.assertRaises(AppError) as caught:
            call_tool(self.app, 'career_mail', {'operation': 'mail.configure', 'args': {'authorization_code': 'SYNTHETIC_NO_ECHO'}})
        self.assertEqual('operation_not_allowed', caught.exception.code)
        self.assertNotIn('SYNTHETIC_NO_ECHO', str(caught.exception))

    def test_unconfigured_reads_are_account_scoped_and_have_no_network_requirement(self):
        other = self.app.execute('account.create', {'name': 'Synthetic second mailbox'})
        for account_id in ('default', other['id']):
            status = call_tool(self.app, 'career_mail', {'operation': 'mail.status', 'user_id': account_id})
            self.assertFalse(status['configured'])
            self.assertFalse(status['authorization_code_set'])
            notices = call_tool(self.app, 'career_mail', {'operation': 'mail.list', 'user_id': account_id,
                                                         'args': {'limit': 1, 'offset': 0}})
            self.assertEqual([], notices['items'])
            self.assertEqual(0, notices['total'])
            self.assertIsNone(notices['next_offset'])
        self.assertEqual('default', self.app.user_id)

    def test_direct_agent_calls_cannot_configure_credentials_or_probe(self):
        for operation in ('mail.configure', 'mail.test'):
            with self.subTest(operation=operation), self.assertRaises(AppError) as caught:
                self.app.execute(operation, {}, actor='codex')
            self.assertEqual('user_action_required', caught.exception.code)

    def test_http_origin_and_session_account_guards_apply_to_mail(self):
        server = LocalServer(self.app, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()

        try:
            status, headers, _ = request('GET', '/')
            self.assertEqual(200, status)
            cookie = headers['Set-Cookie'].split(';')[0]
            body = json.dumps({'operation': 'mail.status', 'args': {}, 'user_id': 'default'})
            status, _, _ = request('POST', '/api/action', body,
                                    {'Cookie': cookie, 'Origin': 'https://invalid.example', 'Content-Type': 'application/json'})
            self.assertEqual(403, status)
            status, _, payload = request('POST', '/api/action', body,
                                          {'Cookie': cookie, 'Origin': origin, 'Content-Type': 'application/json'})
            self.assertEqual(200, status)
            self.assertFalse(json.loads(payload)['data']['configured'])
            other = self.app.execute('account.create', {'name': 'Synthetic HTTP mailbox'})
            switch = json.dumps({'operation': 'account.select', 'args': {'id': other['id']}, 'user_id': 'default'})
            status, _, _ = request('POST', '/api/action', switch,
                                    {'Cookie': cookie, 'Origin': origin, 'Content-Type': 'application/json'})
            self.assertEqual(200, status)
            status, _, payload = request('POST', '/api/action', body,
                                          {'Cookie': cookie, 'Origin': origin, 'Content-Type': 'application/json'})
            self.assertEqual(409, status)
            self.assertEqual('account_changed', json.loads(payload)['error']['code'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
