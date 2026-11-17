import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import app


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_db = app.DATA, app.DB
        app.DATA = Path(self.temp.name)
        app.DB = app.DATA / 'http.db'
        app.init()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ''

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        app.DATA, app.DB = self.old_data, self.old_db
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        request_headers = {'Cookie': self.cookie, 'Content-Type': 'application/json', 'X-Local-App': '1'}
        request_headers.update(headers or {})
        conn.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, request_headers)
        response = conn.getresponse()
        status, cookie, raw = response.status, response.getheader('Set-Cookie'), response.read()
        conn.close()
        return status, cookie, json.loads(raw)

    def setup_owner(self):
        with patch.dict(os.environ, {'DASHBOARD_SETUP_TOKEN': 'test-only-setup-code'}):
            status, cookie, _ = self.request('/api/auth/setup', {'password': 'test-only-password-123', 'setup_token': 'test-only-setup-code'})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.cookie = cookie.split(';')[0]

    def test_private_api_login_and_logout(self):
        self.assertEqual(self.request('/api/state')[0], 401)
        self.assertEqual(self.request('/api/library')[0], 401)
        self.assertEqual(self.request('/api/settings', app.DEFAULTS)[0], 401)
        self.setup_owner()
        self.assertEqual(self.request('/api/state')[0], 200)
        self.assertEqual(self.request('/api/auth/logout', {})[0], 200)
        self.assertEqual(self.request('/api/state')[0], 401)

    def test_settings_simulator_and_media_scope(self):
        self.setup_owner()
        with patch.object(app, 'MODE', 'demo'):
            config = {**app.DEFAULTS, 'scope': 'selected', 'selected_media_ids': ['foreign-post']}
            self.assertEqual(self.request('/api/settings', config)[0], 400)
            config['selected_media_ids'] = ['demo-post-1']
            self.assertEqual(self.request('/api/settings', config)[0], 200)
            self.assertEqual(self.request('/api/simulate', {'text': 'Hi', 'media_id': 'demo-post-2'})[0], 201)
        state = self.request('/api/state')[2]
        self.assertEqual(state['comments'][0]['status'], 'skipped')

    def test_webhook_port_blocks_dashboard_and_auth(self):
        self.server.webhook_only = True
        self.assertEqual(self.request('/api/auth')[0], 404)
        self.assertEqual(self.request('/api/auth/setup', {})[0], 404)
        with patch.dict(os.environ, {'WEBHOOK_VERIFY_TOKEN': 'test-verify'}):
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
            conn.request('GET', '/webhook?hub.mode=subscribe&hub.verify_token=test-verify&hub.challenge=1234')
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b'1234')
            conn.close()
