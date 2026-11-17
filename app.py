"""Dependency-free local Instagram comment auto-reply prototype."""
import contextvars
import hashlib
import hmac
import json
import os
import mimetypes
import re
import queue
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlencode
from urllib.request import Request, urlopen
import auth

MODE = os.getenv('MODE', 'demo')
DATA = Path(os.getenv('DATA_DIR', 'data'))
DB = DATA / 'replies.db'
JOBS = queue.Queue()
ACCOUNT = contextvars.ContextVar('account', default=1)
DEFAULTS = {
    'enabled': True,
    'scope': 'all',
    'selected_media_ids': [],
    'reply_unmatched': True,
    'public_reply': 'DM sent! Please check your inbox.',
    'fallback': 'Hi! Thanks for your comment. How can we help you today?',
    'rules': [
        {'keywords': 'price,cost,how much', 'reply': 'Hi! Thanks for your interest in our pricing. Which product would you like details about?'},
        {'keywords': 'delivery,shipping', 'reply': 'Hi! Happy to help with delivery. What is your delivery location?'},
        {'keywords': 'love,beautiful,amazing', 'reply': 'Thank you so much! We appreciate your support.'},
    ],
}


def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    return db


def init():
    ACCOUNT.set(1)
    DATA.mkdir(parents=True, exist_ok=True)
    auth.init(connect)
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO settings VALUES (1, ?)', (json.dumps(DEFAULTS),))
        db.execute('''CREATE TABLE IF NOT EXISTS comments (
            id TEXT PRIMARY KEY, username TEXT, text TEXT, reply TEXT,
            source TEXT, status TEXT, detail TEXT DEFAULT '', created REAL)''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(comments)')}
        for name, default in [('public_reply', ''), ('dm_status', 'not_attempted'), ('public_status', 'not_attempted'), ('dm_id', ''), ('public_id', ''), ('media_id', '')]:
            if name not in columns:
                db.execute(f"ALTER TABLE comments ADD COLUMN {name} TEXT DEFAULT '{default}'")
        config = json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
        if 'public_reply' not in config:
            config['public_reply'] = DEFAULTS['public_reply']
            replacements = {
                'Thanks for your interest! Please DM us for current pricing.': DEFAULTS['rules'][0]['reply'],
                'Happy to help with delivery! Please DM us your location.': DEFAULTS['rules'][1]['reply'],
            }
            for rule in config['rules']:
                rule['reply'] = replacements.get(rule['reply'], rule['reply'])
            if config['fallback'] == 'Thanks for your comment! Please send us a DM and our team will help you.':
                config['fallback'] = DEFAULTS['fallback']
        for key in ('scope', 'selected_media_ids', 'reply_unmatched'):
            config.setdefault(key, DEFAULTS[key])
        db.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(config),))
        db.execute('CREATE TABLE IF NOT EXISTS library (id INTEGER PRIMARY KEY, value TEXT, updated REAL)')
        db.execute("CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY, instagram_id TEXT UNIQUE NOT NULL, username TEXT NOT NULL, token TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO accounts VALUES (1,?,?,?)", (os.getenv('INSTAGRAM_ACCOUNT_ID', ''), 'Primary account', ''))
        columns = {r['name'] for r in db.execute('PRAGMA table_info(comments)')}
        if 'account_key' not in columns:
            db.execute('ALTER TABLE comments ADD COLUMN account_key INTEGER NOT NULL DEFAULT 1')
        db.execute('CREATE INDEX IF NOT EXISTS comments_account ON comments(account_key,created)')
        # A crash after submitting a request leaves its outcome unknown. Never retry blindly.
        db.execute("UPDATE comments SET dm_status='uncertain' WHERE dm_status='sending'")
        db.execute("UPDATE comments SET public_status='uncertain' WHERE public_status='sending'")
        db.execute("UPDATE comments SET status='uncertain', detail='Process stopped during sending; verify on Instagram before any manual action.' WHERE status='sending'")
        for row in db.execute("SELECT id FROM comments WHERE status='queued'"):
            JOBS.put(row['id'])


def settings():
    with connect() as db:
        return json.loads(db.execute('SELECT value FROM settings WHERE id=?', (ACCOUNT.get(),)).fetchone()[0])


def draft(text, config):
    for rule in config['rules']:
        if any(re.search(r'(?<!\w)' + re.escape(word.strip()) + r'(?!\w)', text, re.IGNORECASE) for word in rule['keywords'].split(',') if word.strip()):
            return rule['reply']
    return config['fallback'] if config.get('reply_unmatched', True) else None


def ingest(comment_id, username, text, source, media_id=''):
    config = settings()
    reply = draft(text, config)
    in_scope = config['scope'] == 'all' or media_id in config['selected_media_ids']
    allowed = config['enabled'] and in_scope and reply is not None
    reason = '' if allowed else ('Automation is paused.' if not config['enabled'] else 'Post is outside the selected scope.' if not in_scope else 'No keyword matched; fallback replies are off.')
    with connect() as db:
        result = db.execute('INSERT OR IGNORE INTO comments (id,username,text,reply,source,status,created,public_reply,media_id,detail,account_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (comment_id, username, text, reply or '', source,
                             'queued' if allowed else 'skipped', time.time(), config['public_reply'], media_id, reason, ACCOUNT.get()))
    if result.rowcount:
        if allowed:
            JOBS.put(comment_id)
        return True
    return False


def credentials():
    with connect() as db:
        row = db.execute('SELECT * FROM accounts WHERE id=?', (ACCOUNT.get(),)).fetchone()
    if not row:
        raise ValueError('Unknown account')
    result = dict(row)
    if result['id'] == 1:
        result['instagram_id'] = os.getenv('INSTAGRAM_ACCOUNT_ID', result['instagram_id'])
        result['token'] = os.getenv('INSTAGRAM_ACCESS_TOKEN', '')
    return result


def accounts():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT id,instagram_id,username FROM accounts ORDER BY id')]


def add_account(body):
    account_id, token = body.get('instagram_id', ''), body.get('token', '')
    if not isinstance(account_id, str) or not account_id.isdigit() or not isinstance(token, str) or not 10 <= len(token) <= 8192:
        raise ValueError('Enter a valid account ID and access token')
    with connect() as db:
        if db.execute('SELECT 1 FROM accounts WHERE instagram_id=?', (account_id,)).fetchone():
            raise ValueError('Account already connected')
    host = os.getenv('GRAPH_API_HOST', 'graph.facebook.com')
    if host not in ('graph.facebook.com', 'graph.instagram.com'):
        raise ValueError('Unsupported host')
    base = f'https://{host}/{os.getenv("GRAPH_API_VERSION", "v27.0")}/'
    headers = {'Authorization': 'Bearer ' + token}
    # Validate access and comment subscription before storing credentials.
    try:
        with urlopen(Request(base + account_id + '?fields=id,username', headers=headers), timeout=20) as response:
            profile = json.load(response)
        if str(profile.get('id')) != account_id or not profile.get('username'):
            raise ValueError('Account identity mismatch')
        with urlopen(Request(base + account_id + '/subscribed_apps', data=urlencode({'subscribed_fields': 'comments'}).encode(), headers=headers), timeout=20) as response:
            if not json.load(response).get('success'):
                raise ValueError('Subscription failed')
    except Exception:
        raise ValueError('Connection failed. Check the account ID, token, app access, and comments permissions.') from None
    with connect() as db:
        try:
            key = db.execute('INSERT INTO accounts (instagram_id,username,token) VALUES (?,?,?)', (account_id, profile['username'], token)).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError('Account already connected') from None
        db.execute('INSERT INTO settings VALUES (?,?)', (key, json.dumps({**DEFAULTS, 'enabled': False})))
    return key


def graph_get(path, params):
    host = os.getenv('GRAPH_API_HOST', 'graph.facebook.com')
    if host not in ('graph.facebook.com', 'graph.instagram.com'):
        raise ValueError('Unsupported Meta API host')
    request = Request(f'https://{host}/{os.getenv("GRAPH_API_VERSION", "v27.0")}/{path}?{urlencode(params)}',
                      headers={'Authorization': 'Bearer ' + credentials()['token']})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def library():
    with connect() as db:
        row = db.execute('SELECT value,updated FROM library WHERE id=?', (ACCOUNT.get(),)).fetchone()
    if row:
        return {**json.loads(row['value']), 'updated': row['updated']}
    return {'account': None, 'posts': [], 'updated': None}


def sync_library():
    if MODE != 'live':
        return {'account': {'id': 'demo-account', 'username': 'your_studio'}, 'posts': [
            {'id': 'demo-post-1', 'caption': 'Little details. Big feelings. Our new collection is here.', 'media_type': 'IMAGE', 'timestamp': '2026-11-15T10:00:00+00:00'},
            {'id': 'demo-post-2', 'caption': 'Made slowly, loved for a long time. A look behind the scenes.', 'media_type': 'VIDEO', 'timestamp': '2026-11-12T10:00:00+00:00'},
            {'id': 'demo-post-3', 'caption': 'Something special is coming. Say hello in the comments.', 'media_type': 'CAROUSEL_ALBUM', 'timestamp': '2026-11-09T10:00:00+00:00'},
        ]}
    account_id = credentials()['instagram_id']
    account = graph_get(account_id, {'fields': 'id,username'})
    posts = graph_get(account_id + '/media', {'fields': 'id,caption,media_type,media_url,thumbnail_url,permalink,timestamp', 'limit': 50})
    result = {'account': account, 'posts': posts.get('data', [])}
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO library VALUES (?,?,?)', (ACCOUNT.get(), json.dumps(result), time.time()))
        db.execute('UPDATE accounts SET username=? WHERE id=?', (account['username'], ACCOUNT.get()))
    return library()


def graph_post(path, payload):
    version = os.getenv('GRAPH_API_VERSION', 'v27.0')
    host = os.getenv('GRAPH_API_HOST', 'graph.facebook.com')
    if host not in ('graph.facebook.com', 'graph.instagram.com'):
        raise ValueError('Unsupported Meta API host')
    request = Request(f'https://{host}/{version}/{path}', data=json.dumps(payload).encode(),
                      headers={'Authorization': 'Bearer ' + credentials()['token'], 'Content-Type': 'application/json'})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def update_action(comment_id, action, status, result_id=''):
    if action not in ('dm', 'public'):
        raise ValueError('Invalid action')
    with connect() as db:
        db.execute(f'UPDATE comments SET {action}_status=?, {action}_id=? WHERE id=?', (status, result_id, comment_id))


def process(comment_id):
    with connect() as db:
        row = db.execute('SELECT * FROM comments WHERE id=?', (comment_id,)).fetchone()
        if not row or row['status'] != 'queued':
            return
        ACCOUNT.set(row['account_key'])
        current = settings()
        if not current['enabled'] or (current['scope'] == 'selected' and row['media_id'] not in current['selected_media_ids']):
            db.execute("UPDATE comments SET status='skipped', detail='Automation was paused before sending.' WHERE id=?", (comment_id,))
            return
        db.execute("UPDATE comments SET status='sending' WHERE id=?", (comment_id,))
    status, detail = 'simulated', 'DM and public reply simulated; nothing was sent to Instagram.'
    if row['source'] == 'demo':
        update_action(comment_id, 'dm', 'simulated')
        update_action(comment_id, 'public', 'simulated')
    if row['source'] == 'instagram':
        if MODE != 'live':
            status, detail = 'skipped', 'Live mode is disabled.'
        else:
            try:
                update_action(comment_id, 'dm', 'sending')
                result = graph_post(credentials()['instagram_id'] + '/messages',
                                    {'recipient': {'comment_id': comment_id}, 'message': {'text': row['reply']}})
                if not result.get('message_id'):
                    raise ValueError('DM response did not include a message ID')
                update_action(comment_id, 'dm', 'sent', str(result['message_id']))
                # Only claim "DM sent" after Meta confirms the private reply.
                update_action(comment_id, 'public', 'sending')
                result = graph_post(comment_id + '/replies', {'message': row['public_reply']})
                if not result.get('id'):
                    raise ValueError('Reply response did not include an ID')
                update_action(comment_id, 'public', 'sent', str(result['id']))
                status, detail = 'sent', 'Meta confirmed the private DM and public comment reply.'
            except Exception:
                # Response details can contain sensitive credentials; do not expose them.
                with connect() as db:
                    dm_sent = db.execute('SELECT dm_status FROM comments WHERE id=?', (comment_id,)).fetchone()[0] == 'sent'
                update_action(comment_id, 'public' if dm_sent else 'dm', 'uncertain')
                status = 'partial' if dm_sent else 'uncertain'
                detail = ('DM confirmed; public reply could not be confirmed.' if dm_sent else 'DM could not be confirmed; public reply was not attempted.') + ' Check Instagram and Meta permissions. No automatic retry.'
    with connect() as db:
        db.execute('UPDATE comments SET status=?, detail=? WHERE id=?', (status, detail, comment_id))


def worker():
    while True:
        item = JOBS.get()
        try:
            process(item)
        except Exception:
            with connect() as db:
                db.execute("UPDATE comments SET status='uncertain', detail='Processing failed; check before retrying.' WHERE id=?", (item,))
        finally:
            JOBS.task_done()


def signature_valid(raw, signature):
    secret = os.getenv('META_APP_SECRET', '')
    return bool(secret) and hmac.compare_digest('sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(), signature)


class Handler(BaseHTTPRequestHandler):
    def send(self, value, status=200, content_type='application/json', cookie=None):
        raw = json.dumps(value).encode() if content_type == 'application/json' else value
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' https: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(raw)

    def select_account(self):
        value = self.headers.get('X-Account-ID', '1')
        if not value.isdigit():
            self.send({'error': 'Unknown account'}, 404)
            return False
        key = int(value)
        with connect() as db:
            exists = db.execute('SELECT 1 FROM accounts WHERE id=?', (key,)).fetchone()
        if not exists:
            self.send({'error': 'Unknown account'}, 404)
            return False
        ACCOUNT.set(key)
        return True

    def do_GET(self):
        ACCOUNT.set(1)
        parsed = urlparse(self.path)
        if getattr(self.server, 'webhook_only', False) and parsed.path != '/webhook':
            return self.send({'error': 'Not found'}, 404)
        if parsed.path == '/health':
            return self.send({'ok': True})
        if parsed.path == '/webhook':
            params = parse_qs(parsed.query)
            token = os.getenv('WEBHOOK_VERIFY_TOKEN', '')
            if token and params.get('hub.mode') == ['subscribe'] and hmac.compare_digest(params.get('hub.verify_token', [''])[0], token):
                return self.send(params.get('hub.challenge', [''])[0].encode(), content_type='text/plain')
            return self.send({'error': 'Verification failed'}, 403)
        if parsed.path == '/api/auth':
            return self.send({'initialized': auth.initialized(connect), 'authenticated': auth.authenticated(connect, self.headers.get('Cookie'))})
        if parsed.path.startswith('/api/') and not auth.authenticated(connect, self.headers.get('Cookie')):
            return self.send({'error': 'Please sign in.'}, 401)
        if parsed.path == '/api/accounts':
            return self.send({'accounts': accounts()})
        if parsed.path.startswith('/api/') and not self.select_account():
            return
        if parsed.path == '/api/library':
            return self.send(sync_library() if MODE == 'demo' else library())
        if parsed.path == '/api/state':
            with connect() as db:
                rows = [dict(row) for row in db.execute('SELECT * FROM comments WHERE account_key=? ORDER BY created DESC LIMIT 100', (ACCOUNT.get(),))]
                totals = {row['status']: row['n'] for row in db.execute('SELECT status,COUNT(*) AS n FROM comments WHERE source="instagram" AND account_key=? GROUP BY status', (ACCOUNT.get(),))}
            return self.send({'mode': MODE, 'settings': settings(), 'comments': rows, 'totals': totals, 'account_id': credentials()['instagram_id'], 'account_key': ACCOUNT.get(), 'webhook_ready': bool(os.getenv('META_APP_SECRET') and os.getenv('WEBHOOK_VERIFY_TOKEN'))})
        base = Path(__file__).parent / 'static'
        asset = (base / ('index.html' if parsed.path == '/' else parsed.path.lstrip('/'))).resolve()
        if base.resolve() in asset.parents and asset.is_file():
            return self.send(asset.read_bytes(), content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
        self.send({'error': 'Not found'}, 404)

    def do_POST(self):
        ACCOUNT.set(1)
        if getattr(self.server, 'webhook_only', False) and self.path != '/webhook':
            return self.send({'error': 'Not found'}, 404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 1 or length > 1000000:
                return self.send({'error': 'Invalid body length'}, 413)
            raw = self.rfile.read(length)
            if self.path == '/webhook':
                if MODE != 'live' or not signature_valid(raw, self.headers.get('X-Hub-Signature-256', '')):
                    return self.send({'error': 'Invalid signature or live mode disabled'}, 403)
                payload = json.loads(raw)
                if payload.get('object') != 'instagram':
                    return self.send({'ok': True})
                for entry in payload.get('entry', []):
                    with connect() as db:
                        target = db.execute('SELECT id FROM accounts WHERE instagram_id=?', (str(entry.get('id')),)).fetchone()
                    if not target:
                        continue
                    ACCOUNT.set(target['id'])
                    for change in entry.get('changes', []):
                        value = change.get('value', {})
                        author = value.get('from', {})
                        if change.get('field') != 'comments' or value.get('parent_id') or str(author.get('id')) == credentials()['instagram_id']:
                            continue
                        cid, comment = str(value.get('id', '')), value.get('text', '')
                        if cid.isdigit() and comment:
                            ingest(cid, author.get('username', 'Instagram user'), comment[:5000], 'instagram', str(value.get('media', {}).get('id', '')))
                return self.send({'ok': True})
            # Browser requests use JSON + custom header; no CORS access is granted.
            if self.headers.get('X-Local-App') != '1' or self.headers.get('Content-Type') != 'application/json':
                return self.send({'error': 'Invalid application request'}, 403)
            body = json.loads(raw)
            if self.path in ('/api/auth/login', '/api/auth/setup'):
                try:
                    token = auth.login(connect, body, self.client_address[0], setup=self.path.endswith('/setup'))
                    return self.send({'ok': True}, cookie=auth.session_cookie(token))
                except PermissionError as error:
                    return self.send({'error': str(error)}, 403)
                except ValueError as error:
                    return self.send({'error': str(error)}, 400)
            if not auth.authenticated(connect, self.headers.get('Cookie')):
                return self.send({'error': 'Please sign in.'}, 401)
            if self.path == '/api/auth/logout':
                auth.logout(connect, self.headers.get('Cookie'))
                return self.send({'ok': True}, cookie=auth.session_cookie(''))
            if self.path == '/api/accounts':
                try:
                    return self.send({'id': add_account(body), 'accounts': accounts()}, 201)
                except ValueError as error:
                    return self.send({'error': str(error)}, 400)
            if not self.select_account():
                return
            if self.path == '/api/sync':
                try:
                    return self.send(sync_library())
                except Exception:
                    return self.send({'error': 'Instagram sync failed. Check account permissions and token expiry. Previously synced posts are kept.'}, 502)
            if self.path == '/api/simulate':
                comment = str(body.get('text', '')).strip()
                if not comment or len(comment) > 5000:
                    raise ValueError('Enter a comment of 1–5000 characters.')
                ingest('demo-' + uuid.uuid4().hex, str(body.get('username', 'demo_customer'))[:100], comment, 'demo', str(body.get('media_id', '')))
                return self.send({'ok': True}, 201)
            if self.path == '/api/settings':
                rules = body.get('rules')
                if not isinstance(body.get('enabled'), bool) or not isinstance(rules, list) or len(rules) > 25:
                    raise ValueError('Invalid settings.')
                for rule in rules:
                    if not isinstance(rule, dict) or not all(isinstance(rule.get(key), str) and 0 < len(rule[key]) <= 1000 for key in ('keywords', 'reply')):
                        raise ValueError('Each rule needs keywords and a reply, up to 1000 characters each.')
                if not isinstance(body.get('fallback'), str) or not 0 < len(body['fallback'].strip()) <= 1000:
                    raise ValueError('Enter a fallback reply of 1–1000 characters.')
                if not isinstance(body.get('public_reply'), str) or not 0 < len(body['public_reply'].strip()) <= 1000:
                    raise ValueError('Enter a public comment reply of 1–1000 characters.')
                if body.get('scope') not in ('all', 'selected') or not isinstance(body.get('selected_media_ids'), list) or not all(isinstance(value, str) for value in body['selected_media_ids']) or not isinstance(body.get('reply_unmatched'), bool):
                    raise ValueError('Invalid post scope.')
                known_posts = {post['id'] for post in (sync_library()['posts'] if MODE == 'demo' else library()['posts'])}
                if set(body['selected_media_ids']) - known_posts:
                    return self.send({'error': 'Sync Instagram and select posts from your account.'}, 400)
                if body['enabled'] and body['scope'] == 'selected' and not body['selected_media_ids']:
                    return self.send({'error': 'Select at least one post before enabling automation.'}, 400)
                clean = {key: body[key] for key in ('enabled', 'rules', 'fallback', 'public_reply', 'scope', 'selected_media_ids', 'reply_unmatched')}
                with connect() as db:
                    db.execute('UPDATE settings SET value=? WHERE id=?', (json.dumps(clean), ACCOUNT.get()))
                return self.send({'ok': True})
            self.send({'error': 'Not found'}, 404)
        except (ValueError, TypeError, KeyError, AttributeError):
            self.send({'error': 'Invalid request. Check the comment and rule fields.'}, 400)

    def log_message(self, format, *args):
        # Avoid logging verification tokens from query strings.
        print(self.command, urlparse(self.path).path, flush=True)


if __name__ == '__main__':
    if MODE not in ('demo', 'live'):
        raise SystemExit('MODE must be demo or live')
    if MODE == 'live' and not all(os.getenv(key) for key in ('INSTAGRAM_ACCOUNT_ID', 'INSTAGRAM_ACCESS_TOKEN', 'META_APP_SECRET', 'WEBHOOK_VERIFY_TOKEN')):
        raise SystemExit('Live mode requires all four Meta configuration values. See .env.example.')
    init()
    threading.Thread(target=worker, daemon=True).start()
    port = int(os.getenv('PORT', '8080'))
    print(f'Instagram replies: http://localhost:{port} ({MODE} mode)', flush=True)
    webhook_server = ThreadingHTTPServer(('0.0.0.0', 8081), Handler)
    webhook_server.webhook_only = True
    threading.Thread(target=webhook_server.serve_forever, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
