"""Gmail raw MIME parsing. Mail content is data, never executable markup."""
import base64
from datetime import datetime
import email.policy
from email.parser import BytesParser
from email.utils import parseaddr, getaddresses, parsedate_to_datetime
from html import escape
from html.parser import HTMLParser
import re


def decode64(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


class Plain(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip += 1
        if tag in ('br', 'p', 'div', 'tr'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def plain_html(value):
    p = Plain(); p.feed(value); return ''.join(p.parts).strip()


class SafeHTML(HTMLParser):
    tags = set('p div span br hr b strong i em u s h1 h2 h3 h4 h5 h6 table thead tbody tfoot tr td th ul ol li blockquote pre code a img'.split())
    styles = set('color background-color font-size font-family font-weight font-style text-align text-decoration padding padding-left padding-right padding-top padding-bottom margin margin-top margin-bottom border border-collapse border-radius line-height width max-width height vertical-align'.split())
    def __init__(self, images):
        super().__init__(convert_charrefs=True); self.out = []; self.images = images; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'iframe', 'object', 'svg', 'math', 'form'):
            self.skip += 1; return
        if self.skip or tag not in self.tags: return
        clean = []
        for key, value in attrs:
            value = value or ''
            if key == 'href' and tag == 'a' and re.match(r'^(https?://|mailto:)', value, re.I):
                clean.extend([('href', value), ('target', '_blank'), ('rel', 'noopener noreferrer')])
            elif key == 'src' and tag == 'img' and value.lower().startswith('cid:'):
                if value[4:].strip('<>') in self.images: clean.append(('src', self.images[value[4:].strip('<>')]))
            elif key in ('alt', 'title', 'colspan', 'rowspan'): clean.append((key, value))
            elif key == 'style':
                rules = []
                for rule in value.split(';'):
                    k, _, v = rule.partition(':')
                    if k.strip().lower() in self.styles and not re.search(r'url|expression|@|\\|[<>]', v, re.I): rules.append(k + ':' + v)
                if rules: clean.append(('style', ';'.join(rules)))
        self.out.append('<' + tag + ''.join(' %s="%s"' % (k, escape(v, quote=True)) for k, v in clean) + '>')
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'iframe', 'object', 'svg', 'math', 'form'):
            self.skip = max(0, self.skip - 1); return
        if not self.skip and tag in self.tags: self.out.append('</' + tag + '>')
    def handle_data(self, data):
        if not self.skip: self.out.append(escape(data))


def safe_html(source, images=None):
    p = SafeHTML(images or {}); p.feed(source); return ''.join(p.out)


def forwarded_html(source):
    # Outlook's own forwarding header is a separate, bounded div. Preserve the
    # original HTML below it rather than flattening the entire message.
    marker = re.search(r'<div\b[^>]*\bid=["\']divRplyFwdMsg["\'][^>]*>', source, re.I)
    if not marker: return ''
    depth = 1
    for token in re.finditer(r'</?div\b[^>]*>', source[marker.end():], re.I):
        depth += -1 if token[0].startswith('</') else 1
        if depth == 0: return source[marker.end() + token.end():]
    return ''


def header_date(headers, fallback):
    value = headers.get('date') or headers.get('sent', '')
    outlook = re.match(r'(.+?)\s+\(UTC([+-]\d{2}:\d{2})\)', value)
    if outlook:
        try: return datetime.strptime(outlook[1] + ' ' + outlook[2], '%A, %B %d, %Y %I:%M:%S %p %z').timestamp()
        except ValueError: return fallback
    try: return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, OverflowError): return fallback


def parse_message(resource, account):
    root = BytesParser(policy=email.policy.default).parsebytes(decode64(resource['raw']))
    original = next((p.get_payload()[0] for p in root.walk() if p.get_content_type() == 'message/rfc822' and isinstance(p.get_payload(), list) and p.get_payload()), root)
    texts, htmls, attachments, images = [], [], [], {}
    for part in original.walk():
        if part.is_multipart(): continue
        data = part.get_payload(decode=True) or b''
        mime = part.get_content_type()
        cid = str(part.get('Content-ID', '')).strip('<>')
        if part.get_content_disposition() == 'attachment' or part.get_filename() or cid:
            i = len(attachments)
            attachments.append({'id': str(i), 'name': str(part.get_filename() or 'attachment'), 'mime': mime, 'data': base64.b64encode(data).decode()})
            if cid and mime in ('image/png', 'image/jpeg', 'image/gif', 'image/webp'):
                images[cid] = 'data:' + mime + ';base64,' + base64.b64encode(data).decode()
        elif mime in ('text/plain', 'text/html'):
            try: content = data.decode(part.get_content_charset() or 'utf-8', errors='replace')
            except LookupError: content = data.decode('utf-8', errors='replace')
            (texts if mime == 'text/plain' else htmls).append(content)
    source_html = '\n'.join(htmls)
    body = ('\n'.join(texts).strip() or plain_html(source_html)).replace('\r\n', '\n')
    headers = {k.lower(): str(v) for k, v in original.items()}
    forwarded = original is not root
    match = re.search(r'(?:-+\s*Forwarded message\s*-+|Begin forwarded message:)\s*\n((?:(?:From|Date|Sent|Subject|To|Cc):[^\n]*\n|\s*\n){2,})', body, re.I)
    if not match and re.match(r'^(fw|fwd):', headers.get('subject', ''), re.I):
        match = re.search(r'(?:^|\n)_{5,}\s*\n((?:(?:From|Date|Sent|Subject|To|Cc):[^\n]*\n|[ \t]*\n){3,})', body, re.I)
    if match:
        fields = {k.lower(): v.strip() for k, v in re.findall(r'^(From|Date|Sent|Subject|To|Cc):\s*(.+)$', match[1], re.M | re.I)}
        if fields.get('from') and fields.get('to'):
            if 'sent' in fields and 'date' not in fields: headers.pop('date', None)
            headers.update(fields); forwarded = True
            body = body[match.end():].strip()
            source_html = forwarded_html(source_html)
    sender, address = parseaddr(headers.get('from', ''))
    recipients = getaddresses([headers.get('to', '')])
    mailbox = recipients[0][1].lower() if len(recipients) == 1 else ''
    if not forwarded:
        for key in ('x-original-to', 'x-forwarded-to', 'delivered-to'):
            candidate = parseaddr(headers.get(key, ''))[1].lower()
            if candidate and candidate != account.lower(): mailbox = candidate; break
        if re.match(r'^(fwd?|转发):', headers.get('subject', ''), re.I): mailbox = ''
    received = int(resource.get('internalDate', 0)) / 1000
    date = header_date(headers, received)
    message = {'parserVersion': 2, 'id': resource['id'], 'sender': sender or address or 'Unknown', 'email': address,
               'mailbox': mailbox, 'subject': headers.get('subject', '(No subject)'), 'date': date, 'received': received,
               'body': body, 'html': safe_html(source_html, images), 'labels': resource.get('labelIds', []),
               'attachments': attachments, 'complete': bool(body) and '\ufffd' not in body,
               'headers': {k: headers[k] for k in ('to', 'cc', 'list-id', 'list-unsubscribe', 'precedence', 'in-reply-to') if k in headers}}
    message['otp'] = extract_otp(message)
    return message


def extract_otp(message):
    text = message['subject'] + '\n' + message['body']
    keyword = r'(?:verification|security|one[ -]time|authentication|login|sign[ -]?in|access)\s*(?:code|password)|验证码|\bOTP\b'
    codes = set(re.findall(r'(?:' + keyword + r')[^\d\n]{0,40}(\d{4,8})(?!\d)', text, re.I))
    codes.update(re.findall(r'(?<!\d)(\d{4,8})\s+is your\s+(?:' + keyword + r')', text, re.I))
    if len(codes) != 1: return None
    expiry = re.search(r'(?:expires?\s+in|valid\s+for)\s+(\d+)\s*(second|minute|hour)s?', text, re.I)
    seconds = int(expiry[1]) * {'second': 1, 'minute': 60, 'hour': 3600}[expiry[2].lower()] if expiry else 600
    if seconds <= 0: return None
    return {'code': codes.pop(), 'expires': message['received'] + seconds}
