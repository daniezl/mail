"""Split sanitized mail into current content and independently expandable quotes."""
from html import escape
from html.parser import HTMLParser
from email.utils import parseaddr
import re

# Remove only recognized boilerplate sentences, never everything after a notice.
NOTICE = re.compile(
    r'Confidentiality Notice:\s*This email may contain information protected under the Family Educational Rights and Privacy Act\s*\(FERPA\)\s*or the Health Insurance Portability and Accountability Act\s*\(HIPAA\)\.'
    r'\s*If this email contains confidential and/or privileged health\s+or student information and you are not entitled to access such information under FERPA or HIPAA, federal regulations require that you destroy this email without reviewing it and you may not forward it to anyone\.', re.I)


def clean_notice(text):
    return NOTICE.sub('', text).strip()


class Node:
    def __init__(self, tag='', attrs=(), text=None):
        self.tag, self.attrs, self.text = tag, attrs, text
        self.children = []; self.start = self.end = 0


class Document(HTMLParser):
    """Text offsets and HTML ranges share one tree, so slicing cannot lose replies."""
    blocks = set('p div hr h1 h2 h3 h4 h5 h6 table tr ul ol li blockquote pre'.split())
    void = {'br', 'hr', 'img'}
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node(); self.stack = [self.root]
        self.feed(html); self.close()
        self.text = ''; self.project(self.root)
    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs); self.stack[-1].children.append(node)
        if tag not in self.void: self.stack.append(node)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]; break
    def handle_data(self, data):
        pre = any(n.tag == 'pre' for n in self.stack)
        self.stack[-1].children.append(Node(text=data if pre else re.sub(r'\s+', ' ', data)))
    def project(self, node):
        if node.tag in self.blocks: self.text += '\n'
        node.start = len(self.text)
        if node.text is not None: self.text += node.text
        elif node.tag in ('br', 'hr'): self.text += '\n'
        elif node.tag == 'img': self.text += '\ufffc'
        else:
            for child in node.children: self.project(child)
        node.end = len(self.text)
        if node.tag in self.blocks: self.text += '\n'
    def render(self, start, end):
        def visit(node):
            if node.end <= start or node.start >= end: return ''
            if node.text is not None:
                return escape(node.text[max(start-node.start,0):end-node.start])
            content = ''.join(visit(c) for c in node.children)
            if not node.tag: return content
            # Each quote has its own UI container; avoid compounding indentation.
            tag = 'div' if node.tag == 'blockquote' else node.tag
            attrs = ''.join(' %s="%s"' % (k,escape(v or '',quote=True)) for k,v in node.attrs)
            if tag in self.void: return '<'+tag+attrs+'>'
            return '<'+tag+attrs+'>'+content+'</'+tag+'>' if content else ''
        return visit(self.root)
    def cleaned_range(self, start, end):
        ranges=[]; pos=start
        for match in NOTICE.finditer(self.text,start,end):
            ranges.append((pos,match.start()));pos=match.end()
        ranges.append((pos,end))
        return ''.join(self.render(a,b) for a,b in ranges)


def header_blocks(text):
    lines = list(re.finditer(r'[^\n]*(?:\n|$)',text))
    result=[]; i=0
    while i<len(lines):
        first=re.match(r'\s*From:\s*(.+)',lines[i][0],re.I)
        if not first: i+=1;continue
        fields={};j=i;last=None;end=lines[i].end()
        while j<len(lines):
            line=lines[j][0].strip();field=re.match(r'(From|Sent|Date|To|Cc|Subject):\s*(.*)',line,re.I)
            if field:
                key=field[1].lower()
                if key in fields: break
                fields[key]=field[2];last=key;end=lines[j].end()
            elif not line:
                pass
            elif last in ('to','cc','sent','date'):
                # Header wrapping is allowed only before Subject, with bounded size.
                if 'subject' in fields or len(fields[last])>2000: break
                fields[last]+=' '+line;end=lines[j].end()
            else: break
            j+=1
        if fields.get('from') and fields.get('to') and fields.get('subject') and ('sent' in fields or 'date' in fields):
            start=lines[i].start()
            # Include separator immediately before the header, not earlier content.
            prefix=re.search(r'(?:\n[ \t]*)*(?:_{5,}|-{3,}\s*(?:Original Message|Forwarded message)\s*-*|Begin forwarded message:)[ \t]*\n\s*$',text[:start],re.I)
            if prefix:start=prefix.start()
            result.append({'start':start,'end':end,'headers':fields});i=max(j,i+1)
        else:i+=1
    # Gmail/Apple reply attribution, optionally wrapped across lines.
    for m in re.finditer(r'(?m)^[ \t]*On [^\n]+(?:\n[^\n]+){0,2}?wrote:[ \t]*(?:\n|$)',text):
        if any(b['start']<=m.start()<b['end'] for b in result):continue
        attribution=' '.join(m[0].split())
        author=re.search(r'([^,]+<[^>]+>)\s+wrote:',attribution)
        result.append({'start':m.start(),'end':m.end(),'headers':{'from':author[1].strip() if author else 'Previous message','attribution':attribution}})
    return sorted(result,key=lambda b:b['start'])


def conversation(body, html, headers, forwarded=False):
    doc=Document(html) if html else None
    text=doc.text if doc else body
    blocks=header_blocks(text)
    # Only unwrap a leading forwarding header matching the parsed current sender.
    # A reply's embedded From header always starts history, never current content.
    start=0
    if forwarded and blocks:
        first=blocks[0];prefix=text[:first['start']].strip(' \n\t\r\xa0_-')
        if not prefix and parseaddr(first['headers']['from'])[1].lower()==parseaddr(headers.get('from',''))[1].lower():
            start=first['end'];blocks=blocks[1:]
    # Quote endings return ownership to the enclosing author, even when that
    # author places a signature (or more reply text) after the quoted history.
    quotes=[]
    def find_quotes(node):
        if node.tag=='blockquote': quotes.append(node)
        for child in node.children: find_quotes(child)
    if doc: find_quotes(doc.root)
    boundaries={}
    for node in quotes:
        candidates=[b for b in blocks if
                    node.start<=b['start']<node.end or
                    (b['end']<=node.start and not text[b['end']:node.start].strip())]
        if candidates:
            first=min(candidates,key=lambda b:b['start'])
            boundaries.setdefault(first['start'],[]).append(node)
    sections=[]
    def section(fields):
        sender,address=parseaddr(fields.get('from',''))
        result={'sender':sender or address or 'Previous message','email':address,
                'sent':fields.get('date') or fields.get('sent',''),
                'to':fields.get('to',''),'subject':fields.get('subject',''),
                'attribution':fields.get('attribution',''),'body':'','html':''}
        sections.append(result)
        return result
    current=section(headers)
    events=[(b['start'],1,b) for b in blocks]
    events += [(n.end,0,n) for nodes in boundaries.values() for n in nodes]
    events.append((len(text),2,None))
    owners={}
    for end,kind,value in sorted(events,key=lambda e:(e[0],e[1])):
        if end<start: continue
        chunk=clean_notice(text[start:end]).strip(' \n\t\r\xa0_').replace('\ufffc','')
        if chunk: current['body']+='\n'+chunk if current['body'] else chunk
        if doc: current['html']+=doc.cleaned_range(start,end)
        if kind==1:
            for node in boundaries.get(end,[]): owners[id(node)]=current
            current=section(value['headers']);start=value['end']
        else:
            if kind==0: current=owners.get(id(value),current)
            start=end
    return [s for i,s in enumerate(sections) if i==0 or s['body'] or s['html']]
