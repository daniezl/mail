import unittest
from email.message import EmailMessage
from webmail.parser import parse_message, plain_html
from test_webmail import resource

NOTICE = ('Confidentiality Notice: This email may contain information protected under the Family Educational Rights and Privacy Act (FERPA) or the Health Insurance Portability and Accountability Act (HIPAA). If this email contains confidential and/or privileged health or student information and you are not entitled to access such information under FERPA or HIPAA, federal regulations require that you destroy this email without reviewing it and you may not forward it to anyone.')


def header(sender,subject='Re: Project'):
    return f'From: {sender} <{sender.lower()}@example.com>\nSent: Thursday, October 1, 2026 8:54:51 PM (UTC-05:00) Eastern Time\nTo: Reader <reader@example.com>\nSubject: {subject}\n\n'


def html_header(sender):
    return header(sender).replace(' <',' &lt;').replace('>','&gt;').replace('\n','<br>')


class ConversationTests(unittest.TestCase):
    def test_outlook_outer_header_without_div_then_inner_reply_marker(self):
        m=EmailMessage();m['From']='Forwarder <forwarder@example.com>';m['Subject']=' FW: Re: Project'
        m.set_content('________________________________\n'+header('Teacher')+'NEW reply\n________________________________\n'+header('Reader')+'OLD reply\n'+NOTICE)
        m.add_alternative('<hr>'+html_header('Teacher')+'<div>NEW <b>reply</b></div><hr><div id="divRplyFwdMsg">'+html_header('Reader')+'</div><p>OLD reply</p><p>'+NOTICE+'</p>',subtype='html')
        r=parse_message(resource(m),'reader@example.com');parts=r['conversation']
        self.assertEqual(r['sender'],'Teacher');self.assertEqual(len(parts),2)
        self.assertIn('NEW',parts[0]['html']);self.assertIn('<b>reply</b>',parts[0]['html']);self.assertNotIn('OLD',parts[0]['body'])
        self.assertEqual(parts[1]['sender'],'Reader');self.assertIn('OLD',parts[1]['body'])
        for part in parts:
            self.assertNotIn('Confidentiality Notice',part['html']);self.assertNotIn('From:',part['body'])
    def test_notice_before_quote_does_not_remove_history(self):
        m=EmailMessage();m['From']='Teacher <teacher@example.com>';m['Subject']='Re: Project'
        m.set_content('Latest response\n'+NOTICE+'\n________________________________\n'+header('Reader')+'Previous question')
        parts=parse_message(resource(m),'reader@example.com')['conversation']
        self.assertEqual(len(parts),2);self.assertEqual(parts[0]['body'],'Latest response');self.assertEqual(parts[1]['body'],'Previous question')
    def test_reply_containing_forward_does_not_replace_latest_sender(self):
        m=EmailMessage();m['From']='Teacher <teacher@example.com>';m['Subject']='Re: Project'
        m.set_content('My latest answer\n---------- Forwarded message ---------\n'+header('Reader')+'Old content')
        r=parse_message(resource(m),'reader@example.com')
        self.assertEqual(r['sender'],'Teacher');self.assertTrue(r['conversation'][0]['body'].startswith('My latest answer'))
    def test_inline_images_and_links_survive(self):
        m=EmailMessage();m['From']='Teacher <teacher@example.com>';m['Subject']='Screenshot'
        m.set_content('See the image')
        m.add_alternative('<p>See the image<img src="cid:pic"></p><a href="https://example.com">Link</a><p>'+NOTICE+'</p>',subtype='html')
        m.get_payload()[1].add_related(b'png',maintype='image',subtype='png',cid='<pic>')
        r=parse_message(resource(m),'reader@example.com')
        self.assertIn('data:image/png;base64',r['conversation'][0]['html']);self.assertIn('https://example.com',r['conversation'][0]['html'])
        self.assertEqual(len(r['attachments']),1)
    def test_gmail_reply_attribution(self):
        m=EmailMessage();m['From']='Teacher <teacher@example.com>';m['Subject']='Re: Project'
        m.set_content('New answer\n\nOn Thu, Oct 1, 2026, Reader <reader@example.com> wrote:\nOld question')
        parts=parse_message(resource(m),'reader@example.com')['conversation']
        self.assertEqual(len(parts),2);self.assertEqual(parts[0]['body'],'New answer');self.assertEqual(parts[1]['body'],'Old question')
    def test_unrecognized_notice_is_not_destructively_removed(self):
        m=EmailMessage();m['From']='Teacher <teacher@example.com>';m['Subject']='Project'
        m.set_content('Confidentiality Notice: please review our proposed wording.\nActual content after this.')
        self.assertIn('Actual content',parse_message(resource(m),'reader@example.com')['conversation'][0]['body'])

    def test_signature_outside_nested_quote_belongs_to_current_sender(self):
        from webmail.conversation import conversation
        html=('<div>Latest answer</div><div>On Thu, Reader &lt;reader@example.com&gt; wrote:</div>'
              '<blockquote><div>Forwarded question</div>'+html_header('Student')+
              '<p>Original question</p></blockquote><div>-- </div><p>Teacher signature</p>')
        parts=conversation('',html,{'from':'Teacher <teacher@example.com>'})
        self.assertIn('Teacher signature',parts[0]['body'])
        self.assertIn('Teacher signature',parts[0]['html'])
        self.assertNotIn('Original question',parts[0]['body'])
        self.assertIn('Original question',parts[-1]['body'])
        self.assertNotIn('Teacher signature',parts[-1]['html'])

    def test_nested_quote_tail_returns_to_each_enclosing_author(self):
        from webmail.conversation import conversation
        html=('Newest<div>On Thu, Reader &lt;reader@example.com&gt; wrote:</div>'
              '<blockquote>Middle<div>On Wed, Other &lt;other@example.com&gt; wrote:</div>'
              '<blockquote>Oldest</blockquote><p>Middle signature</p></blockquote><p>Newest signature</p>')
        parts=conversation('',html,{'from':'Teacher <teacher@example.com>'})
        self.assertEqual(len(parts),3)
        self.assertIn('Newest signature',parts[0]['body'])
        self.assertIn('Middle signature',parts[1]['body'])
        self.assertEqual(parts[2]['body'],'Oldest')
