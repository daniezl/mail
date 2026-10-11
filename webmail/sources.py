"""Resolve source accounts, not arbitrary recipients of a forwarded message."""
import re
from email.utils import getaddresses

ROUTING_HEADERS = ('from', 'to', 'cc', 'subject', 'delivered-to', 'x-original-to',
                   'x-forwarded-to', 'x-forwarded-for', 'envelope-to')

def addresses(values):
    if isinstance(values, str): values = [values]
    # Outlook recipient lists often use semicolons without RFC group syntax.
    return {address.lower() for _, address in getaddresses([v.replace(';', ',') for v in values]) if '@' in address}

def source_mailbox(message, account, owned):
    account = (account or '').lower()
    owned = {a.lower() for a in owned if '@' in a} | ({account} if account else set())
    if not owned: return message.get('mailbox', '')
    headers = message.get('headers', {})
    routing = message.get('deliveryHeaders', {})
    def matches(values): return addresses(values) & owned
    # The original message may address a group or put this account in Cc.
    recipients = matches([headers.get('to',''), headers.get('cc','')])
    if len(recipients) == 1: return next(iter(recipients))
    for key in ('x-original-to', 'x-forwarded-for', 'envelope-to'):
        found = matches(routing.get(key, [])) - {account}
        if len(found) == 1: return next(iter(found))
    sender = matches(routing.get('from', [])) - {account}
    subject = ' '.join(routing.get('subject', []))
    if len(sender) == 1 and re.match(r'^\s*(?:fw|fwd|转发):', subject, re.I):
        return next(iter(sender))
    if len(recipients) > 1: return ''
    old = message.get('mailbox','').lower()
    if old in owned: return old
    # A forwarded message with no known source must not be called direct Gmail.
    if re.match(r'^\s*(?:fw|fwd|转发):', subject or message.get('subject',''), re.I): return ''
    delivered = matches(routing.get('delivered-to', []))
    return next(iter(delivered)) if len(delivered) == 1 else ''
