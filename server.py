#!/usr/bin/env python3
"""Run: python3 server.py. Deliberately loopback-only, not a public hosting server."""
import base64
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit, quote
from webmail.service import MailService, ServiceError, SCOPE

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get('MAIL_PORT', '5173'))
ORIGIN = 'http://127.0.0.1:' + str(PORT)
SESSION = secrets.token_urlsafe(32)
CSRF = secrets.token_urlsafe(32)
STATES = {}
SERVICE = None
STATIC = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/styles.css': ('styles.css', 'text/css')}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # Do not log OAuth codes, queries, or message IDs.
    def cookie(self):
        try:
            c = SimpleCookie(self.headers.get('Cookie', ''))
            return c['mail_session'].value if 'mail_session' in c else ''
        except Exception: return ''
    def send(self, status, data=b'', mime='application/json', headers=None):
        if not isinstance(data, bytes): data = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') else ''))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self' about:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(data)
    def redirect(self, url): self.send(303, b'', headers={'Location': url})
    def guard(self, write=False):
        if self.headers.get('Host') != '127.0.0.1:' + str(PORT): raise ServiceError('Invalid host.', 403)
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' and urlsplit(self.path).path != '/auth/callback': raise ServiceError('Cross-site request blocked.', 403)
        if not secrets.compare_digest(self.cookie(), SESSION): raise ServiceError('Reload the app to continue.', 401)
        if write and (self.headers.get('Origin') != ORIGIN or not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), CSRF)):
            raise ServiceError('Invalid request origin.', 403)
    def do_GET(self):
        try:
            url = urlsplit(self.path); path = url.path; query = parse_qs(url.query)
            if self.headers.get('Host') != '127.0.0.1:' + str(PORT): raise ServiceError('Open ' + ORIGIN, 403)
            if path in STATIC:
                if self.headers.get('Sec-Fetch-Site') == 'cross-site' and not (path == '/' and self.headers.get('Sec-Fetch-Dest') == 'document'): raise ServiceError('Cross-site request blocked.', 403)
                file, mime = STATIC[path]
                return self.send(200, (ROOT / file).read_bytes(), mime, {'Set-Cookie': 'mail_session=' + SESSION + '; HttpOnly; SameSite=Lax; Path=/'} if path == '/' else None)
            self.guard()
            if path == '/api/state': return self.send(200, {**SERVICE.snapshot(), 'csrf': CSRF})
            if path == '/api/message':
                m = SERVICE.store.message(query.get('id', [''])[0])
                return self.send(200, SERVICE.public(m, detail=True))
            if path == '/api/attachment':
                m = SERVICE.store.message(query.get('id', [''])[0]); i = query.get('part', [''])[0]
                a = next((a for a in m['attachments'] if a['id'] == i), None)
                if not a: raise ServiceError('Attachment unavailable.', 404)
                return self.send(200, base64.b64decode(a['data']), 'application/octet-stream', {'Content-Disposition': "attachment; filename*=UTF-8''" + quote(Path(a['name']).name, safe='')})
            if path == '/auth/callback':
                state = query.get('state', [''])[0]; entry = STATES.pop(state, None)
                if not entry or time.time() - entry['time'] > 600 or entry['session'] != self.cookie(): raise ServiceError('Login expired. Please try again.')
                if 'error' in query: return self.redirect('/?login=cancelled')
                SERVICE.connect(query.get('code', [''])[0], entry['verifier'])
                return self.redirect('/')
            raise ServiceError('Not found.', 404)
        except ServiceError as e:
            if urlsplit(self.path).path == '/auth/callback': self.redirect('/?login=failed')
            else: self.send(e.status, {'error': str(e)})
        except Exception:
            if urlsplit(self.path).path == '/auth/callback': self.redirect('/?login=failed')
            else: self.send(500, {'error': 'Request failed. Please retry.'})
    def do_POST(self):
        try:
            self.guard(write=True)
            length = int(self.headers.get('Content-Length', 0))
            if length > 65536: raise ServiceError('Request too large.', 413)
            data = json.loads(self.rfile.read(length) or b'{}')
            path = urlsplit(self.path).path
            if path == '/api/connect':
                config = SERVICE.config()
                if not config: raise ServiceError('Gmail connection is not ready. Please try again shortly.', 503)
                now = time.time()
                for key in list(STATES):
                    if now - STATES[key]['time'] > 600: STATES.pop(key, None)
                state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
                STATES[state] = {'time': now, 'verifier': verifier, 'session': self.cookie()}
                params = {'client_id': config['client_id'], 'redirect_uri': ORIGIN + '/auth/callback',
                          'response_type': 'code', 'scope': SCOPE, 'state': state,
                          'access_type': 'offline', 'prompt': 'consent', 'login_hint': SERVICE.store.get('account', ''),
                          'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
                          'code_challenge_method': 'S256'}
                return self.send(200, {'url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params)})
            if path == '/api/sync': return self.send(200, SERVICE.start(bool(data.get('more')), bool(data.get('retry'))))
            if path == '/api/filter/reset': return self.send(200, SERVICE.reset_filter())
            if path == '/api/filter/run': return self.send(200, SERVICE.run_filter(data.get('prompt')))
            if path == '/api/filter/expected': return self.send(200, SERVICE.set_expected(data.get('id', ''), data.get('keep')))
            if path == '/api/read': return self.send(200, SERVICE.read(data.get('id', '')))
            raise ServiceError('Not found.', 404)
        except (ValueError, TypeError): self.send(400, {'error': 'Invalid request.'})
        except ServiceError as e: self.send(e.status, {'error': str(e)})
        except Exception: self.send(500, {'error': 'Request failed. Please retry.'})


def main():
    global SERVICE
    os.umask(0o077)
    SERVICE = MailService(os.environ.get('MAIL_DATA_DIR', ROOT / '.local'), ORIGIN)
    print('Mail is running at ' + ORIGIN, flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()

if __name__ == '__main__': main()
