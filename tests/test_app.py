import hashlib
import hmac
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import app
import auth
import json
import time


class ReplyTests(unittest.TestCase):
    def test_unified_login_api_host_and_bearer_token(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"message_id":"dm-1"}'
        with patch.dict(os.environ, {'GRAPH_API_HOST': 'graph.facebook.com', 'INSTAGRAM_ACCESS_TOKEN': 'test-token'}), patch.object(app, 'urlopen', return_value=response) as request:
            app.graph_post('999/messages', {'recipient': {'comment_id': '123'}, 'message': {'text': 'Hello'}})
        sent = request.call_args.args[0]
        self.assertIn('https://graph.facebook.com/', sent.full_url)
        self.assertEqual(sent.get_header('Authorization'), 'Bearer test-token')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_db = app.DATA, app.DB
        app.DATA = Path(self.temp.name)
        app.DB = app.DATA / 'test.db'
        app.init()

    def tearDown(self):
        app.DATA, app.DB = self.old_data, self.old_db
        self.temp.cleanup()

    def test_auto_reply_and_duplicate_event(self):
        self.assertTrue(app.ingest('demo-1', 'alice', 'What is the PRICE?', 'demo'))
        self.assertFalse(app.ingest('demo-1', 'alice', 'What is the PRICE?', 'demo'))
        app.process('demo-1')
        app.process('demo-1')
        with app.connect() as db:
            rows = db.execute('SELECT * FROM comments').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'simulated')
        self.assertIn('pricing', rows[0]['reply'])
        self.assertEqual(rows[0]['dm_status'], 'simulated')
        self.assertEqual(rows[0]['public_status'], 'simulated')
        self.assertIn('DM sent', rows[0]['public_reply'])

    def test_live_sends_dm_before_public_confirmation_once(self):
        app.ingest('12345', 'alice', 'Price?', 'instagram')
        with patch.object(app, 'MODE', 'live'), patch.dict(os.environ, {'INSTAGRAM_ACCOUNT_ID': '999'}), patch.object(app, 'graph_post', side_effect=[{'message_id': 'dm-1'}, {'id': 'reply-1'}]) as send:
            app.process('12345')
            app.process('12345')
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[0], '999/messages')
        self.assertEqual(send.call_args_list[0].args[1]['recipient'], {'comment_id': '12345'})
        self.assertEqual(send.call_args_list[1].args[0], '12345/replies')
        with app.connect() as db:
            row = db.execute('SELECT * FROM comments').fetchone()
        self.assertEqual((row['dm_id'], row['public_id'], row['status']), ('dm-1', 'reply-1', 'sent'))

    def test_dm_failure_does_not_claim_dm_sent(self):
        app.ingest('12345', 'alice', 'Hi', 'instagram')
        with patch.object(app, 'MODE', 'live'), patch.dict(os.environ, {'INSTAGRAM_ACCOUNT_ID': '999'}), patch.object(app, 'graph_post', side_effect=TimeoutError) as send:
            app.process('12345')
        self.assertEqual(send.call_count, 1)
        with app.connect() as db:
            row = db.execute('SELECT * FROM comments').fetchone()
        self.assertEqual(row['dm_status'], 'uncertain')
        self.assertEqual(row['public_status'], 'not_attempted')

    def test_public_failure_preserves_confirmed_dm(self):
        app.ingest('12345', 'alice', 'Hi', 'instagram')
        with patch.object(app, 'MODE', 'live'), patch.dict(os.environ, {'INSTAGRAM_ACCOUNT_ID': '999'}), patch.object(app, 'graph_post', side_effect=[{'message_id': 'dm-1'}, TimeoutError]):
            app.process('12345')
        with app.connect() as db:
            row = db.execute('SELECT * FROM comments').fetchone()
        self.assertEqual((row['status'], row['dm_status'], row['public_status']), ('partial', 'sent', 'uncertain'))

    def test_pause_prevents_pending_reply(self):
        app.ingest('demo-2', 'alice', 'Hi', 'demo')
        config = app.settings()
        config['enabled'] = False
        import json
        with app.connect() as db:
            db.execute('UPDATE settings SET value=?', (json.dumps(config),))
        app.process('demo-2')
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM comments').fetchone()[0], 'skipped')

    def test_webhook_signature(self):
        raw = b'{"object":"instagram"}'
        with patch.dict(os.environ, {'META_APP_SECRET': 'test-secret'}):
            signature = 'sha256=' + hmac.new(b'test-secret', raw, hashlib.sha256).hexdigest()
            self.assertTrue(app.signature_valid(raw, signature))
            self.assertFalse(app.signature_valid(raw + b' ', signature))

    def test_restart_marks_inflight_uncertain(self):
        app.ingest('demo-3', 'alice', 'Hi', 'demo')
        with app.connect() as db:
            db.execute("UPDATE comments SET status='sending'")
        app.init()
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM comments').fetchone()[0], 'uncertain')

    def test_scope_and_keyword_skip_do_not_queue_messages(self):
        config = app.settings()
        config.update(scope='selected', selected_media_ids=['selected-post'], reply_unmatched=False)
        with app.connect() as db:
            db.execute('UPDATE settings SET value=?', (json.dumps(config),))
        app.ingest('wrong-post', 'alice', 'price', 'instagram', 'another-post')
        app.ingest('no-keyword', 'alice', 'Just browsing', 'instagram', 'selected-post')
        with app.connect() as db:
            rows = db.execute('SELECT * FROM comments').fetchall()
        self.assertTrue(all(row['status'] == 'skipped' for row in rows))
        self.assertIn('outside', rows[0]['detail'])
        self.assertIn('No keyword', rows[1]['detail'])

    def test_hi_does_not_match_shipping(self):
        config = {**app.settings(), 'rules': [{'keywords': 'hi', 'reply': 'Hello'}], 'reply_unmatched': False}
        self.assertIsNone(app.draft('shipping', config))
        self.assertEqual(app.draft('Hi!', config), 'Hello')

    def test_setup_requires_code_and_hashes_password(self):
        password = 'test-only-password-123'
        with patch.dict(os.environ, {'DASHBOARD_SETUP_TOKEN': 'test-only-setup-code'}):
            with self.assertRaises(PermissionError):
                auth.login(app.connect, {'password': password, 'setup_token': 'wrong'}, 'test-ip', setup=True)
            token = auth.login(app.connect, {'password': password, 'setup_token': 'test-only-setup-code'}, 'test-ip', setup=True)
            with self.assertRaises(PermissionError):
                auth.login(app.connect, {'password': password, 'setup_token': 'test-only-setup-code'}, 'test-ip', setup=True)
        self.assertTrue(auth.authenticated(app.connect, 'reply_session='+token))
        with app.connect() as db:
            self.assertNotIn(password, db.execute('SELECT password_hash FROM owner').fetchone()[0])
            self.assertNotEqual(token, db.execute('SELECT token_hash FROM sessions').fetchone()[0])

    def test_logout_expiry_and_login_throttle(self):
        with patch.dict(os.environ, {'DASHBOARD_SETUP_TOKEN': 'test-code'}):
            token = auth.login(app.connect, {'password': 'test-only-password-123', 'setup_token': 'test-code'}, 'test-ip', setup=True)
        auth.logout(app.connect, 'reply_session='+token)
        self.assertFalse(auth.authenticated(app.connect, 'reply_session='+token))
        token = auth.login(app.connect, {'password': 'test-only-password-123'}, 'test-ip')
        with app.connect() as db:
            db.execute('UPDATE sessions SET expires=?', (time.time()-1,))
        self.assertFalse(auth.authenticated(app.connect, 'reply_session='+token))
        for _ in range(5):
            with self.assertRaises(PermissionError):
                auth.login(app.connect, {'password': 'wrong-password-long'}, 'test-ip')
        with self.assertRaisesRegex(PermissionError, 'Too many'):
            auth.login(app.connect, {'password': 'test-only-password-123'}, 'test-ip')


if __name__ == '__main__':
    unittest.main()
