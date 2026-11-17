import hashlib
import hmac
import json
import os
from unittest.mock import patch, MagicMock
import unittest
import test_http
import app


class MultiAccountTests(unittest.TestCase):
    request = test_http.HttpTests.request
    setup_owner = test_http.HttpTests.setup_owner
    tearDown = test_http.HttpTests.tearDown
    def setUp(self):
        test_http.HttpTests.setUp(self)
        self.setup_owner()
        with app.connect() as db:
            db.execute('INSERT INTO accounts VALUES (2,?,?,?)', ('222', 'second_account', 'second-test-token'))
            db.execute('INSERT INTO settings VALUES (2,?)', (json.dumps({**app.DEFAULTS, 'enabled': False}),))
            db.execute('INSERT INTO library VALUES (2,?,?)', (json.dumps({'account': {'id': '222', 'username': 'second_account'}, 'posts': [{'id': 'second-post'}]}), 123))

    def test_account_data_and_settings_are_separate(self):
        headers = {'X-Account-ID': '2'}
        self.assertFalse(self.request('/api/state', headers=headers)[2]['settings']['enabled'])
        self.assertTrue(self.request('/api/state')[2]['settings']['enabled'])
        with patch.object(app, 'MODE', 'live'):
            self.assertEqual(self.request('/api/library', headers=headers)[2]['posts'][0]['id'], 'second-post')
            self.assertEqual(self.request('/api/library')[2]['posts'], [])
        config = {**app.DEFAULTS, 'fallback': 'Second account only'}
        self.assertEqual(self.request('/api/settings', config, headers)[0], 200)
        self.assertEqual(self.request('/api/state', headers=headers)[2]['settings']['fallback'], 'Second account only')
        self.assertNotEqual(self.request('/api/state')[2]['settings']['fallback'], 'Second account only')
        self.request('/api/simulate', {'text': 'Hello'}, headers)
        self.assertEqual(len(self.request('/api/state', headers=headers)[2]['comments']), 1)
        self.assertEqual(len(self.request('/api/state')[2]['comments']), 0)
        self.assertEqual(self.request('/api/state', headers={'X-Account-ID': '999'})[0], 404)
        self.assertEqual(self.request('/api/settings', config, {'X-Account-ID': '-1'})[0], 404)
        self.cookie = ''
        self.assertEqual(self.request('/api/accounts')[0], 401)
        self.assertEqual(self.request('/api/accounts', {'instagram_id': '333', 'token': 'never-store-this'})[0], 401)

    def test_worker_uses_event_account_not_current_account(self):
        app.ACCOUNT.set(2)
        with app.connect() as db:
            db.execute('UPDATE settings SET value=? WHERE id=2', (json.dumps(app.DEFAULTS),))
        app.ingest('12345', 'customer', 'price', 'instagram')
        app.ACCOUNT.set(1)
        response = MagicMock()
        response.__enter__.return_value.read.side_effect = [b'{"message_id":"dm-2"}', b'{"id":"reply-2"}']
        with patch.object(app, 'MODE', 'live'), patch.object(app, 'urlopen', return_value=response) as send:
            app.process('12345')
        self.assertIn('/222/messages', send.call_args_list[0].args[0].full_url)
        for call in send.call_args_list:
            self.assertEqual(call.args[0].get_header('Authorization'), 'Bearer second-test-token')
        self.assertEqual(self.request('/api/state')[2]['comments'], [])
        self.assertEqual(self.request('/api/state', headers={'X-Account-ID': '2'})[2]['comments'][0]['status'], 'sent')

    def test_verified_add_does_not_expose_credentials(self):
        response = MagicMock()
        response.__enter__.return_value.read.side_effect = [b'{"id":"333","username":"third_account"}', b'{"success":true}']
        with patch.object(app, 'urlopen', return_value=response):
            status, _, data = self.request('/api/accounts', {'instagram_id': '333', 'token': 'third-test-token'})
        self.assertEqual(status, 201)
        self.assertNotIn('third-test-token', json.dumps(data))
        self.assertNotIn('second-test-token', json.dumps(self.request('/api/accounts')[2]))
        self.assertFalse(self.request('/api/state', headers={'X-Account-ID': str(data['id'])})[2]['settings']['enabled'])
        self.assertEqual(self.request('/api/accounts', {'instagram_id': '333', 'token': 'third-test-token'})[0], 400)
        with patch.object(app, 'urlopen', side_effect=TimeoutError('secret-token')):
            status, _, data = self.request('/api/accounts', {'instagram_id': '444', 'token': 'secret-token'})
        self.assertEqual(status, 400)
        self.assertNotIn('secret-token', json.dumps(data))
        self.assertEqual(len(self.request('/api/accounts')[2]['accounts']), 3)

    def test_signed_webhook_routes_known_accounts_only(self):
        payload = {'object': 'instagram', 'entry': [
            {'id': '222', 'changes': [{'field': 'comments', 'value': {'id': '555', 'text': 'Hi', 'from': {'id': 'customer'}, 'media': {'id': 'second-post'}}}]},
            {'id': '999', 'changes': [{'field': 'comments', 'value': {'id': '666', 'text': 'Hi'}}]}]}
        signature = 'sha256=' + hmac.new(b'test-secret', json.dumps(payload).encode(), hashlib.sha256).hexdigest()
        with patch.object(app, 'MODE', 'live'), patch.dict(os.environ, {'META_APP_SECRET': 'test-secret'}):
            self.assertEqual(self.request('/webhook', payload, {'X-Hub-Signature-256': signature})[0], 200)
        self.assertEqual(self.request('/api/state')[2]['comments'], [])
        rows = self.request('/api/state', headers={'X-Account-ID': '2'})[2]['comments']
        self.assertEqual([r['id'] for r in rows], ['555'])
        self.assertEqual(rows[0]['status'], 'skipped')
        app.init()
        self.assertEqual(len(app.accounts()), 2)
