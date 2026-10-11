"""Durable APNs delivery. Existing mail is baselined, never replayed on install."""
import json
import os
from pathlib import Path
import re
import threading
import time
from .service import ServiceError


class APNs:
    def __init__(self):
        self.key = os.environ.get('APNS_KEY_FILE', '')
        self.key_id = os.environ.get('APNS_KEY_ID', '')
        self.team = os.environ.get('APNS_TEAM_ID', '')
        self.topic = os.environ.get('APNS_TOPIC', 'com.daniezl.mail.ios')
        self.jwt = None
        self.issued = 0

    @property
    def configured(self):
        return bool(self.key and self.key_id and self.team and Path(self.key).is_file())

    def send(self, token, environment, message):
        import jwt
        import httpx
        now = time.time()
        if now - self.issued > 2400 or not self.jwt:
            self.jwt = jwt.encode({'iss': self.team, 'iat': int(now)}, Path(self.key).read_text(),
                                  algorithm='ES256', headers={'kid': self.key_id})
            self.issued = now
        host = 'api.sandbox.push.apple.com' if environment == 'development' else 'api.push.apple.com'
        with httpx.Client(http2=True, timeout=15) as client:
            r = client.post('https://' + host + '/3/device/' + token,
                headers={'authorization': 'bearer ' + self.jwt, 'apns-topic': self.topic,
                         'apns-push-type': 'alert', 'apns-priority': '10',
                         'apns-expiration': str(int(now + 3600)), 'apns-collapse-id': message['id']},
                json={'aps': {'alert': {'title': message['sender'][:120], 'body': message['subject'][:240]},
                              'sound': 'default', 'thread-id': message.get('threadId') or message['id']},
                      'messageId': message['id']})
        reason = ''
        if r.status_code != 200:
            try: reason = r.json().get('reason', '')
            except ValueError: pass
        return r.status_code, reason


class PushService:
    def __init__(self, mail, provider=None):
        self.mail = mail
        self.provider = provider or APNs()
        self.lock = threading.Lock()
        with mail.store.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS push_devices(token TEXT PRIMARY KEY, environment TEXT NOT NULL, created REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS push_delivery(token TEXT NOT NULL, message TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                  attempts INTEGER NOT NULL DEFAULT 0, retry REAL NOT NULL DEFAULT 0, PRIMARY KEY(token,message));
            ''')

    def register(self, token, environment):
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-fA-F]{64,512}', token) or environment not in ('development','production'):
            raise ServiceError('Invalid push device.')
        token = token.lower()
        with self.lock, self.mail.store.connect() as db:
            if not db.execute('SELECT 1 FROM push_devices WHERE token=?', (token,)).fetchone():
                if db.execute('SELECT COUNT(*) FROM push_devices').fetchone()[0] >= 32:
                    raise ServiceError('Too many registered devices.', 409)
                db.execute('INSERT INTO push_devices VALUES (?,?,?)', (token, environment, time.time()))
                db.execute('INSERT OR IGNORE INTO push_delivery(token,message,done) SELECT ?,id,1 FROM mail', (token,))
        return {'configured': self.provider.configured}

    def unregister(self, token):
        with self.lock, self.mail.store.connect() as db:
            db.execute('DELETE FROM push_devices WHERE token=?', (token,))
            db.execute('DELETE FROM push_delivery WHERE token=?', (token,))

    def deliver(self):
        if not self.provider.configured or not self.lock.acquire(blocking=False): return
        try:
            now = time.time()
            with self.mail.store.connect() as db:
                devices = db.execute('SELECT token,environment,created FROM push_devices').fetchall()
            for token, environment, created in devices:
                for message, _, decision, _ in self.mail.store.rows():
                    with self.mail.store.connect() as db:
                        entry = db.execute('SELECT done,attempts,retry FROM push_delivery WHERE token=? AND message=?', (token,message['id'])).fetchone()
                    if entry and (entry[0] or entry[2] > now): continue
                    # Undecided mail waits for classification. A filter reset never replays deliveries.
                    if decision is None: continue
                    skip = (decision not in ('keep', 'uncertain') or 'INBOX' not in message['labels'] or
                            'UNREAD' not in message['labels'] or
                            message['received'] < max(created, now - 86400) or
                            self.mail.store.get('read:' + message['id'], False))
                    attempts = entry[1] if entry else 0
                    if skip: status, reason = 200, ''
                    else:
                        try: status, reason = self.provider.send(token, environment, message)
                        except Exception: status, reason = 503, ''
                    if status == 410 or reason in ('BadDeviceToken', 'DeviceTokenNotForTopic', 'Unregistered'):
                        with self.mail.store.connect() as db:
                            db.execute('DELETE FROM push_devices WHERE token=?',(token,))
                            db.execute('DELETE FROM push_delivery WHERE token=?',(token,))
                        break
                    attempts += 1
                    with self.mail.store.connect() as db:
                        db.execute('INSERT OR REPLACE INTO push_delivery VALUES (?,?,?,?,?)',
                            (token,message['id'],int(status == 200),attempts,now + min(3600,30 * 2 ** min(attempts,7))))
                    if status in (403,429) or status >= 500: break
        finally: self.lock.release()


def start_background(mail, push, interval=30):
    def run():
        while True:
            try:
                if not mail.busy:
                    push.deliver()
                    mail.start()
            except Exception:
                # No email content, credentials, or device tokens in logs.
                pass
            time.sleep(interval)
    threading.Thread(target=run, daemon=True, name='mail-background-sync').start()
