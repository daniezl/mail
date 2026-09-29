"""Local-only, read-only Gmail and Jev services. Secrets never go to the frontend."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from .parser import parse_message

MODEL = 'jev-1.13.0'
PROMPT_VERSION = 5
CACHE_LIMIT = 100
PROMPT_FILE = Path(__file__).resolve().parent.parent / '.local' / 'filter-prompt.txt'
PROMPT_EXAMPLE = Path(__file__).resolve().parent.parent / 'filter-prompt.example.txt'

def filter_prompt():
    return (PROMPT_FILE if PROMPT_FILE.exists() else PROMPT_EXAMPLE).read_text().strip()

def allowed_account(directory):
    try: value = json.loads((Path(directory) / 'personal.json').read_text())
    except (OSError, ValueError): value = {}
    return (os.environ.get('MAIL_ALLOWED_ACCOUNT') or value.get('allowed_account', '')).strip().lower()

SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'


class ServiceError(Exception):
    def __init__(self, message, status=400): super().__init__(message); self.status = status


def request_json(url, data=None, token=None, form=False, method=None, timeout=30):
    headers = {'Accept': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    if data is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
        data = (urlencode(data) if form else json.dumps(data)).encode()
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=timeout) as response:
            return json.load(response)
    except HTTPError as e:
        # Never reflect upstream response bodies (which may contain credentials/mail).
        if e.code == 400: raise ServiceError('The service rejected the request.', 400)
        if e.code == 404: raise ServiceError('Mail no longer exists in Gmail.', 404)
        if e.code == 401: raise ServiceError('Authentication expired. Reconnect or update your API key.', 401)
        raise ServiceError('Service request failed (%s). Please retry.' % e.code, 502)
    except (URLError, TimeoutError, OSError, ValueError):
        raise ServiceError('Could not reach the service. Cached mail is still available.', 503)


def classify_answer(response):
    try:
        result = response['answers']['decision']
        if result.get('type') != 'choice': raise ValueError()
        choice = result['choice']
        if choice not in ('keep', 'hide', 'uncertain'): raise ValueError()
        probabilities = result['probabilities']
        if set(probabilities) != {'keep', 'hide', 'uncertain'}: raise ValueError()
        values = [*probabilities.values(), result['confidence']]
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values): raise ValueError()
        if abs(sum(probabilities.values()) - 1) > .01: raise ValueError()
        if probabilities[choice] < max(probabilities.values()): raise ValueError()
        return choice
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise ServiceError('Filtering failed. Mail remains visible.', 502)


def private_config(directory):
    """Values are provisioned outside the web app, never exposed over HTTP."""
    path = Path(directory) / 'jev.json'
    try: value = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError): value = {}
    return {'key': os.environ.get('TYPESAFE_API_KEY') or value.get('api_key', ''),
            'model': os.environ.get('JEV_MODEL') or value.get('model', MODEL)}


class Store:
    def __init__(self, directory):
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / 'mail.sqlite3'
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS mail (id TEXT PRIMARY KEY, payload TEXT, annotation TEXT, decision TEXT, cache_key TEXT);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(mail)')}
            if 'retained' not in columns:
                db.execute('ALTER TABLE mail ADD COLUMN retained INTEGER NOT NULL DEFAULT 0')
            db.execute("UPDATE mail SET retained=1 WHERE decision='keep' OR id IN (SELECT substr(key,10) FROM settings WHERE key LIKE 'expected:%' AND value='true')")
        os.chmod(self.path, 0o600)
    def connect(self): return sqlite3.connect(self.path, timeout=30)
    def get(self, key, default=None):
        with self.connect() as db: row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    def set(self, key, value):
        with self.connect() as db: db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))
    def rows(self):
        with self.connect() as db: rows = db.execute('SELECT payload,annotation,decision,cache_key FROM mail').fetchall()
        return [(json.loads(p), a, d, c) for p, a, d, c in rows]
    def message(self, id):
        with self.connect() as db: row = db.execute('SELECT payload FROM mail WHERE id=?', (id,)).fetchone()
        if not row: raise ServiceError('Mail not cached.', 404)
        return json.loads(row[0])
    def save(self, message):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM mail WHERE id=?', (message['id'],)).fetchone()
            old = json.loads(row[0]) if row else None
            keys = ('sender', 'email', 'mailbox', 'subject', 'body', 'headers', 'complete')
            changed = old and any(old.get(k) != message.get(k) for k in keys)
            db.execute('INSERT INTO mail(id,payload) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (message['id'], json.dumps(message)))
            if changed: db.execute('UPDATE mail SET decision=NULL,cache_key=NULL WHERE id=?', (message['id'],))
    def clear(self):
        with self.connect() as db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('DELETE FROM mail'); db.execute('DELETE FROM settings')
        with self.connect() as db: db.execute('VACUUM')


class MailService:
    def __init__(self, directory, origin):
        self.store = Store(directory); self.origin = origin
        self.sync_lock = threading.RLock(); self.classify_lock = threading.Lock()
        self.classifying = False; self.classify_error = None
        self.busy = False; self.sync_error = None; self.needs_login = False
        self.job_lock = threading.Lock()
        self.token_lock = threading.RLock()
        self.progress = None
        self.key_signature = None
        self.retry_at = 0
        self.filter_lock = threading.RLock()
        self.generation = 0
        self.rerun_requested = False
        self.search_cache = {}
        self.trim_cache()
        signature = 'jev:' + private_config(directory)['model'] + ':' + str(PROMPT_VERSION)
        if self.store.get('classifier_signature') != signature:
            with self.store.connect() as db:
                db.execute('UPDATE mail SET decision=NULL, cache_key=NULL')
            self.store.set('classifier_signature', signature)
    def trim_cache(self, ids=None):
        if ids is None:
            ids = [m['id'] for m,a,d,c in sorted(self.store.rows(), key=lambda r:r[0]['received'], reverse=True)[:CACHE_LIMIT]]
        with self.store.connect() as db:
            db.execute("UPDATE mail SET retained=1 WHERE decision='keep' OR id IN (SELECT substr(key,10) FROM settings WHERE key LIKE 'expected:%' AND value='true')")
            if ids:
                db.execute('DELETE FROM mail WHERE retained=0 AND id NOT IN (' + ','.join('?' for _ in ids) + ')', ids)
            else: db.execute('DELETE FROM mail WHERE retained=0')
            db.execute("DELETE FROM settings WHERE key LIKE 'read:%' AND substr(key,6) NOT IN (SELECT id FROM mail)")
            db.execute("DELETE FROM settings WHERE key IN ('next','loaded_more')")
    def reset_filter(self):
        with self.filter_lock:
            self.generation += 1
            self.rerun_requested = False
            self.store.set('filter_enabled', False)
            with self.store.connect() as db:
                db.execute("UPDATE mail SET retained=1 WHERE decision='keep' OR id IN (SELECT substr(key,10) FROM settings WHERE key LIKE 'expected:%' AND value='true')")
                db.execute('UPDATE mail SET decision=NULL,cache_key=NULL')
            self.classify_error = None; self.progress = None
        return self.snapshot()
    def set_expected(self, id, keep):
        if type(keep) is not bool: raise ServiceError('Expected keep must be true or false.')
        self.store.message(id)
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('expected:' + id, json.dumps(keep)))
            if keep: db.execute('UPDATE mail SET retained=1 WHERE id=?', (id,))
        return self.snapshot()
    def run_filter(self, prompt=None):
        if prompt is not None:
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
                raise ServiceError('Enter a prompt between 1 and 20,000 characters.')
            temporary = PROMPT_FILE.with_suffix('.tmp')
            temporary.write_text(prompt.strip() + '\n')
            temporary.replace(PROMPT_FILE)
        with self.filter_lock:
            self.generation += 1
            self.store.set('filter_enabled', True)
            with self.store.connect() as db:
                db.execute("UPDATE mail SET retained=1 WHERE decision='keep' OR id IN (SELECT substr(key,10) FROM settings WHERE key LIKE 'expected:%' AND value='true')")
                db.execute('UPDATE mail SET decision=NULL,cache_key=NULL')
            self.retry_at = 0; self.classify_error = None
            self.rerun_requested = self.busy
        return self.start(retry=True, classification_only=True)
    def config(self):
        path = self.store.directory / 'google.json'
        if not path.exists(): return None
        try: return json.loads(path.read_text())['web']
        except (KeyError, ValueError): return None
    def token(self):
        with self.token_lock: return self._token()
    def _token(self):
        value = self.store.get('google_tokens')
        if not value: raise ServiceError('Connect Gmail to continue.', 401)
        if value.get('expires', 0) < time.time() + 60:
            config = self.config()
            if not config or not value.get('refresh_token'): raise ServiceError('Reconnect Gmail.', 401)
            try:
                updated = request_json('https://oauth2.googleapis.com/token', {
                    'client_id': config['client_id'], 'client_secret': config['client_secret'],
                    'grant_type': 'refresh_token', 'refresh_token': value['refresh_token']}, form=True)
            except ServiceError as error:
                if error.status in (400, 401): raise ServiceError('Reconnect Gmail.', 401)
                raise
            value.update(updated); value['expires'] = time.time() + updated.get('expires_in', 3600)
            self.store.set('google_tokens', value)
        return value['access_token']
    def gmail(self, path, params=None):
        url = 'https://gmail.googleapis.com/gmail/v1/users/me/' + path
        if params: url += '?' + urlencode(params)
        return request_json(url, token=self.token())
    def connect(self, code, verifier):
        with self.sync_lock:
            config = self.config()
            tokens = request_json('https://oauth2.googleapis.com/token', {
                'client_id': config['client_id'], 'client_secret': config['client_secret'],
                'code': code, 'code_verifier': verifier, 'redirect_uri': self.origin + '/auth/callback',
                'grant_type': 'authorization_code'}, form=True)
            if SCOPE not in tokens.get('scope', '').split(): raise ServiceError('Gmail permission was not granted.')
            profile = request_json('https://gmail.googleapis.com/gmail/v1/users/me/profile', token=tokens['access_token'])
            account = profile['emailAddress'].lower()
            # Local account allowlist; never open this personal app to arbitrary accounts.
            if not allowed_account(self.store.directory) or account != allowed_account(self.store.directory):
                raise ServiceError('Use the account configured for this personal app.', 403)
            old = self.store.get('google_tokens', {})
            if not tokens.get('refresh_token'): tokens['refresh_token'] = old.get('refresh_token')
            tokens['expires'] = time.time() + tokens.get('expires_in', 3600)
            self.store.set('google_tokens', tokens); self.store.set('account', account)
            self.needs_login = False
    def public(self, message, detail=False):
        fields = ('id', 'sender', 'email', 'mailbox', 'subject', 'date', 'received')
        result = {k: message[k] for k in fields}
        raw = self.store.get('ai:' + message['id'])
        with self.store.connect() as db:
            row = db.execute('SELECT cache_key FROM mail WHERE id=?', (message['id'],)).fetchone()
        if raw and row and row[0] and raw.get('cacheKey') == row[0]:
            result['confidence'] = raw['confidence']; result['probabilities'] = raw['probabilities']; result['modelChoice'] = raw['choice']
        result['expectedKeep'] = self.store.get('expected:' + message['id'], False)
        result['unread'] = 'UNREAD' in message['labels'] and not self.store.get('read:' + message['id'], False)
        colors = self.store.get('colors', {})
        mailbox = message['mailbox']
        if mailbox and mailbox not in colors:
            colors[mailbox] = ['#569078', '#bc806b', '#a38d56', '#718aad', '#9883a7'][len(colors) % 5]
            self.store.set('colors', colors)
        result['color'] = colors.get(mailbox, '#959995')
        result['otp'] = message.get('otp')
        if detail:
            result['body'] = message['body']; result['html'] = message['html']
            result['attachments'] = [{k: v for k, v in a.items() if k != 'data'} for a in message.get('attachments', [])]
        return result
    def status(self):
        return {'account': self.store.get('account'), 'busy': self.busy, 'classifying': self.classifying,
                'error': self.sync_error or self.classify_error, 'needsLogin': self.needs_login,
                'filterEnabled': self.store.get('filter_enabled', True), 'generation': self.generation, 'progress': self.progress}
    def snapshot(self):
        rows = sorted(self.store.rows(), key=lambda r: r[0]['received'], reverse=True)
        evaluated = [(m,d) for m,a,d,c in rows if d is not None]
        correct = sum(d == ('keep' if self.store.get('expected:' + m['id'], False) else 'hide') for m,d in evaluated)
        return {**self.status(), 'prompt': filter_prompt(),
                'score': {'correct': correct, 'evaluated': len(evaluated), 'total': len(rows)}, 'messages': [{**self.public(m), 'decision': d} for m, a, d, c in rows
                 if 'INBOX' in m['labels']]}
    def cached_message(self, id):
        try: return self.store.message(id)
        except ServiceError:
            with self.sync_lock:
                if id in self.search_cache: return self.search_cache[id]
            raise
    def search(self, query, page=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ServiceError('Enter a search query (up to 1,000 characters).')
        if page is not None and (not isinstance(page, str) or len(page) > 2000):
            raise ServiceError('Invalid search page.')
        params = {'q': query.strip(), 'maxResults': 25}
        if page: params['pageToken'] = page
        listing = self.gmail('messages', params)
        def fetch(item):
            return parse_message(self.gmail('messages/' + quote(item['id'], safe=''), {'format':'raw'}), self.store.get('account', ''))
        with ThreadPoolExecutor(max_workers=6) as pool:
            messages = list(pool.map(fetch, listing.get('messages', [])))
        with self.sync_lock:
            for m in messages:
                self.search_cache.pop(m['id'], None)
                self.search_cache[m['id']] = m
            while len(self.search_cache) > 500: self.search_cache.pop(next(iter(self.search_cache)))
        return {'messages':[self.public(m) for m in messages], 'nextPage':listing.get('nextPageToken')}
    def read(self, id):
        m = self.cached_message(id)
        self.store.set('read:' + id, True)
        return self.public(m, detail=True)
    def content_key(self, m, prompt=None, model=None):
        content = {k: m[k] for k in ('sender', 'email', 'mailbox', 'subject', 'body', 'headers', 'complete')}
        return hashlib.sha256(json.dumps([content, model or private_config(self.store.directory)['model'], PROMPT_VERSION, prompt if prompt is not None else filter_prompt()], sort_keys=True).encode()).hexdigest()
    def fetch_page(self, page=None):
        params = {'q': 'in:inbox', 'maxResults': CACHE_LIMIT}
        listing = self.gmail('messages', params)
        cached = {m['id']: m for m, a, d, c in self.store.rows()}
        items = listing.get('messages', [])
        def fetch(item):
            id = item['id']
            if id in cached and cached[id].get('parserVersion') == 2:
                result = self.gmail('messages/' + quote(id, safe=''), {'format': 'minimal'})
                m = cached[id]; m['labels'] = result.get('labelIds', [])
                return m
            resource = self.gmail('messages/' + quote(id, safe=''), {'format': 'raw'})
            return parse_message(resource, self.store.get('account', ''))
        with ThreadPoolExecutor(max_workers=6) as pool:
            for m in pool.map(fetch, items): self.store.save(m)
        ids = [item['id'] for item in items]
        return ids, listing.get('nextPageToken')
    def sync(self, more=False):
        with self.sync_lock:
            ids, _ = self.fetch_page()
            self.trim_cache(ids)
    def classify_message(self, m, config):
        content = {k: m[k] for k in ('sender', 'email', 'mailbox', 'subject', 'body', 'headers', 'complete')}
        payload = {
            'model': config['model'], 'state': content,
            'questions': {'decision': {
                'type': 'choice',
                'instructions': config.get('prompt', filter_prompt()),
                'criteria': {
                    'keep': 'The recipient wants to retain this message under the stated preferences.',
                    'hide': 'This message matches an unwanted category under his stated preferences.',
                    'uncertain': 'Available evidence is insufficient or conflicting; keep visible for review.'}}}}
        for attempt in range(2):
            try:
                response = request_json('https://api.typesafe.ai/v1/systemone', payload, token=config['key'], timeout=45)
                classify_answer(response)
                return response['answers']['decision']
            except ServiceError as error:
                if attempt == 1 or error.status in (400, 401, 402): raise
                time.sleep(.5)
    def classify_pending(self):
        with self.filter_lock:
            if not self.store.get('filter_enabled', True): return
            generation = self.generation
        self.classifying = True; self.classify_error = None
        config = private_config(self.store.directory)
        config['prompt'] = filter_prompt()
        pending = [(m, self.content_key(m, config['prompt'], config['model'])) for m, a, d, c in sorted(self.store.rows(), key=lambda r: r[0]['received'], reverse=True)
                   if 'INBOX' in m['labels'] and c != self.content_key(m, config['prompt'], config['model'])]
        self.progress = {'done': 0, 'total': len(pending)}
        failed = 0
        def classify(item):
            m, key = item
            if generation != self.generation: return m['id'], 'uncertain', None, False
            if m.get('otp'): return m['id'], 'keep', key, False
            if len(m['body']) > 40000: return m['id'], 'uncertain', key, False
            if not config['key']: return m['id'], 'uncertain', None, True
            try: return m['id'], self.classify_message(m, config), key, False
            except Exception: return m['id'], 'uncertain', None, True
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(classify, item) for item in pending]
                for future in as_completed(futures):
                    id, result, key, error = future.result()
                    with self.filter_lock:
                        if generation != self.generation: continue
                        if isinstance(result, dict):
                            self.store.set('ai:' + id, {**result, 'cacheKey': key})
                            result = result['choice']
                        with self.store.connect() as db:
                            db.execute("UPDATE mail SET decision=?,cache_key=?,retained=CASE WHEN ?='keep' THEN 1 ELSE retained END WHERE id=?", (result, key, result, id))
                        failed += int(error)
                        self.progress = {'done': self.progress['done'] + 1, 'total': len(pending)}
            if failed and generation == self.generation: self.classify_error = 'Some mail could not be filtered. It remains visible.'
        finally:
            self.classifying = False
            self.retry_at = time.time() + 60 if failed else 0
    def start(self, more=False, retry=False, classification_only=False):
        if not self.store.get('account'): return self.snapshot()
        if not self.job_lock.acquire(blocking=False): return self.snapshot()
        self.busy = True
        def work():
            try:
                self.sync_error = None
                config = private_config(self.store.directory)
                signature = hashlib.sha256(config['key'].encode()).hexdigest()
                if config['key'] and (retry or signature != self.key_signature):
                    self.key_signature = signature
                    self.retry_at = 0
                    self.classify_pending()
                try:
                    if not classification_only: self.sync()
                except ServiceError as error:
                    self.needs_login = error.status == 401
                    self.sync_error = 'Reconnect Gmail to continue.' if self.needs_login else 'Could not sync. Cached mail is available.'
                if retry or time.time() >= self.retry_at: self.classify_pending()
                else:
                    # A service failure must not leave newly fetched messages invisible.
                    with self.store.connect() as db: db.execute("UPDATE mail SET decision='uncertain' WHERE decision IS NULL")
            except Exception:
                self.sync_error = 'Could not refresh. Please retry.'
                with self.store.connect() as db: db.execute("UPDATE mail SET decision='uncertain' WHERE decision IS NULL")
            finally:
                self.busy = False; self.job_lock.release()
                with self.filter_lock:
                    rerun = self.rerun_requested
                    self.rerun_requested = False
                if rerun: self.start(retry=True, classification_only=True)
        threading.Thread(target=work, daemon=True).start()
        return self.snapshot()
