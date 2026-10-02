import base64
from email.message import EmailMessage
import json
import tempfile
import unittest
from unittest.mock import patch
from webmail.parser import parse_message, safe_html, extract_otp
from webmail.service import MailService, Store, ServiceError, classify_answer


def resource(message, id='a', labels=None):
    return {'id': id, 'raw': base64.urlsafe_b64encode(message.as_bytes()).decode(), 'internalDate':'1790100000000', 'labelIds':labels or ['INBOX','UNREAD']}


def fixture(subject='Hello', text='A personal reply to Alex.', id='a'):
    m=EmailMessage();m['From']='Professor <teacher@example.com>';m['To']='alex@school.example';m['Subject']=subject;m.set_content(text)
    return parse_message(resource(m,id), 'reader@example.com')


class ParserTests(unittest.TestCase):
    def test_original_metadata(self):
        m=fixture(); self.assertEqual(m['sender'],'Professor');self.assertEqual(m['mailbox'],'alex@school.example');self.assertTrue(m['complete'])
    def test_inline_forward_and_reply(self):
        m=fixture('Fwd: result','---------- Forwarded message ---------\nFrom: Records <records@example.com>\nDate: Tue, 22 Sep 2026 08:00:00 -0400\nSubject: Your grade\nTo: Alex <alex@school.example>\n\nYour grade is A.')
        self.assertEqual(m['sender'],'Records');self.assertEqual(m['subject'],'Your grade');self.assertEqual(m['body'],'Your grade is A.')
        self.assertEqual(fixture('Re: hello','On Monday someone wrote:\nFrom: Other\nTo: Another\n\nQuote')['sender'],'Professor')
        self.assertEqual(fixture('Fwd: mystery')['mailbox'],'')
    def test_outlook_forward(self):
        text='________________________________\r\nFrom: Teacher <teacher@example.com>\r\nSent: Tuesday, September 22, 2026 9:11:00 PM (UTC-05:00) Eastern Time (US & Canada)\r\nTo: Alex <alex@school.example>\r\nSubject: Your paper\r\n\r\nHello Alex'
        m=EmailMessage();m['From']='Forwarder <f@example.com>';m['To']='gmail@example.com';m['Subject']='FW: Your paper';m['Date']='Wed, 23 Sep 2026 05:00:00 +0000';m.set_content(text)
        m.add_alternative('<div id="divRplyFwdMsg"><b>From:</b> Teacher</div><p>Hello <b>Alex</b></p>',subtype='html')
        result=parse_message(resource(m),'gmail@example.com')
        self.assertEqual(result['sender'],'Teacher');self.assertEqual(result['subject'],'Your paper');self.assertEqual(result['mailbox'],'alex@school.example')
        self.assertEqual(result['body'],'Hello Alex');self.assertIn('<b>Alex</b>',result['html']);self.assertNotIn('From:',result['html'])
        from datetime import datetime,timezone
        self.assertEqual(result['date'],datetime(2026,9,23,2,11,tzinfo=timezone.utc).timestamp())
    def test_outlook_forward_with_leading_subject_whitespace(self):
        m=EmailMessage();m['From']='Forwarder <f@example.com>';m['To']='reader@example.com';m['Subject']=' FW: Travel details'
        m.set_content('________________________________\nFrom: Travel Office <travel@example.com>\nSent: Wednesday, September 30, 2026 3:42:08 PM (UTC-05:00) Eastern Time\nTo: Parent <parent@example.com>; Office <office@example.com>\nCc: Student <student@example.com>\nSubject: Travel details\n\nLatest itinerary.\n\nFrom: Parent <parent@example.com>\nTo: Office <office@example.com>\nSubject: Previous request\n\nOld request.')
        m.add_alternative('<div id="divRplyFwdMsg"><b>From:</b> Travel Office</div><p>Latest itinerary.</p><blockquote>Old request.</blockquote>',subtype='html')
        result=parse_message(resource(m),'reader@example.com')
        self.assertEqual(result['sender'],'Travel Office');self.assertEqual(result['email'],'travel@example.com')
        self.assertEqual(result['subject'],'Travel details');self.assertTrue(result['body'].startswith('Latest itinerary.'))
        self.assertIn('Latest itinerary.',result['html']);self.assertIn('Old request.',result['body'])
        self.assertEqual(fixture(' Re: Travel details',m.get_payload()[0].get_content())['sender'],'Professor')
        m.get_payload()[1].set_content('<p>From: Travel Office</p><p>Latest itinerary.</p>',subtype='html')
        self.assertIn('Latest itinerary.',parse_message(resource(m),'reader@example.com')['html'])
    def test_attached_mime_html_and_attachments(self):
        original=EmailMessage();original['From']='Original <original@example.com>';original['To']='me@example.com';original['Subject']='Result';original.set_content('Result A')
        original.add_alternative('<p>Hello <b>Alex</b><img src="cid:pic"></p>', subtype='html')
        original.get_payload()[1].add_related(b'fakepng', maintype='image',subtype='png',cid='<pic>',filename='picture.png')
        wrapper=EmailMessage();wrapper['From']='Forwarder <f@example.com>';wrapper.set_content('Forwarded');wrapper.add_attachment(original)
        parsed=parse_message(resource(wrapper),'reader@example.com')
        self.assertEqual(parsed['sender'],'Original');self.assertIn('data:image/png;base64',parsed['html']);self.assertEqual(parsed['attachments'][0]['name'],'picture.png')
    def test_untrusted_html(self):
        html=safe_html('<script>alert(1)</script><p onclick="steal()" style="color:red;background:url(https://evil)">Hi</p><img src="https://tracker"><a href="javascript:alert(1)">Bad</a><a href="https://example.com">Good</a><iframe src="https://evil"></iframe>')
        self.assertNotIn('script',html);self.assertNotIn('onclick',html);self.assertNotIn('tracker',html);self.assertNotIn('url(',html);self.assertNotIn('iframe',html);self.assertIn('noopener noreferrer',html)
    def test_otp_expiry_ambiguity_and_false_positive(self):
        m=fixture('Verification code: 483921','Valid for 5 minutes.');self.assertEqual(m['otp']['code'],'483921');self.assertEqual(m['otp']['expires']-m['received'],300)
        self.assertIsNone(fixture('Verification code: 483921','Your security code: 123456')['otp'])
        self.assertIsNone(fixture('Your receipt','2026 is your graduation year. Your total is 1234.')['otp'])
        self.assertEqual(fixture('Sign in','123456 is your verification code')['otp']['code'],'123456')

def answer(value='keep'):
    return {'answers':{'decision':{'type':'choice','choice':value,'probabilities':{k:float(k==value) for k in ('keep','hide','uncertain')},'confidence':1.0}}}

class JevTests(unittest.TestCase):
    def test_typed_decisions(self):
        for value in ('keep','hide','uncertain'):self.assertEqual(classify_answer(answer(value)),value)
        invalid=[]
        for field,value in [('confidence',float('nan')),('type','score'),('probabilities',{'keep':1}),('choice','hide')]:
            bad=answer();bad['answers']['decision'][field]=value;invalid.append(bad)
        for bad in ({},answer('delete'),*invalid):
            with self.assertRaises(ServiceError):classify_answer(bad)
    def test_cache_failure_fallback_and_otp_local(self):
        with tempfile.TemporaryDirectory() as directory:
            svc=MailService(directory,'http://127.0.0.1:5173');svc.store.save(fixture());svc.store.save(fixture('Verification code: 123456','Valid for 10 minutes',id='otp'))
            with patch('webmail.service.private_config',return_value={'key':'test','model':'jev-1.13.0'}),patch('webmail.service.request_json',return_value=answer('hide')) as call:
                svc.classify_pending();self.assertEqual(call.call_count,1);svc.classify_pending();self.assertEqual(call.call_count,1)
                self.assertNotIn('123456',json.dumps(call.call_args.args));self.assertEqual({m['id']:m['decision'] for m in svc.snapshot()['messages']},{'a':'hide','otp':'keep'})
            m=svc.store.message('a');m['body']='Changed content';svc.store.save(m)
            with patch('webmail.service.private_config',return_value={'key':'test','model':'jev-1.13.0'}),patch('webmail.service.request_json',side_effect=ServiceError('Offline')):
                svc.classify_pending();self.assertTrue(svc.classify_error);self.assertIn('a',[m['id'] for m in svc.snapshot()['messages']])
                self.assertIsNone(next(r[3] for r in svc.store.rows() if r[0]['id']=='a'))
    def test_one_failure_does_not_block_remaining_mail(self):
        with tempfile.TemporaryDirectory() as directory:
            svc=MailService(directory,'http://127.0.0.1:5173')
            svc.store.save(fixture(id='failed'));svc.store.save(fixture(id='good'))
            def decide(m,config):
                if m['id']=='failed':raise ServiceError('Temporary failure',503)
                return 'hide'
            with patch('webmail.service.private_config',return_value={'key':'test','model':'jev-1.13.0'}),patch.object(svc,'classify_message',side_effect=decide):svc.classify_pending()
            results={m['id']:(d,c) for m,a,d,c in svc.store.rows()}
            self.assertEqual(results['failed'],('uncertain',None));self.assertEqual(results['good'][0],'hide')
            self.assertEqual(svc.progress,{'done':2,'total':2})
    def test_missing_body_reaches_classifier_with_completeness_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            svc=MailService(directory,'http://127.0.0.1:5173')
            m=fixture();m['body']='';m['complete']=False;svc.store.save(m)
            with patch('webmail.service.private_config',return_value={'key':'test','model':'jev-1.13.0'}),patch('webmail.service.request_json',return_value=answer('uncertain')) as call:
                svc.classify_pending()
                self.assertEqual(call.call_args.args[0], 'https://api.typesafe.ai/v1/systemone')
                content=call.call_args.args[1]['state']
                self.assertFalse(content['complete']);self.assertEqual(content['body'],'')
                self.assertEqual(svc.snapshot()['messages'][0]['decision'],'uncertain')
    def test_no_key_fail_open(self):
        with tempfile.TemporaryDirectory() as directory:
            svc=MailService(directory,'http://127.0.0.1:5173');svc.store.save(fixture())
            with patch('webmail.service.private_config',return_value={'key':'','model':'jev-1.13.0'}):svc.classify_pending()
            self.assertEqual(len(svc.snapshot()['messages']),1);self.assertTrue(svc.classify_error)
    def test_migration_invalidates_jev(self):
        with tempfile.TemporaryDirectory() as directory:
            s=Store(directory);s.save(fixture())
            with s.connect() as db:db.execute("UPDATE mail SET decision='hide',cache_key='jev'")
            svc=MailService(directory,'http://127.0.0.1:5173');self.assertIsNone(svc.store.rows()[0][2])

class ServiceTests(unittest.TestCase):
    def test_followups_only_later_messages_cached_without_read_or_classify(self):
        original=self.s.store.message('a');original['threadId']='thread';self.s.store.save(original)
        def gmail(path,params):
            if path.startswith('threads/'):
                return {'messages':[{'id':'a'},{'id':'new'},{'id':'older'}]}
            m=EmailMessage();m['From']='Reply <reply@example.com>';m['To']='alex@example.com';m['Subject']='Re: Hello'
            m.set_content('Later answer')
            r=resource(m,path.split('/')[-1]);r['internalDate']=str(int((original['date']+(60 if r['id']=='new' else -60))*1000))
            return r
        with patch.object(self.s,'gmail',side_effect=gmail): result=self.s.followups('a')
        self.assertEqual([r['id'] for r in result['replies']],['new'])
        self.assertFalse(self.s.store.get('read:new',False))
        self.assertEqual(len(self.s.store.rows()),1)
        cache=self.s.store.get('thread-replies:a');cache['checked']=0;self.s.store.set('thread-replies:a',cache)
        with patch.object(self.s,'gmail',side_effect=ServiceError('Offline')):
            self.assertEqual(self.s.followups('a')['replies'],result['replies'])

    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=MailService(self.tmp.name,'http://127.0.0.1:5173');self.s.store.save(fixture())
    def tearDown(self):self.tmp.cleanup()
    def test_expectations_score_and_reset(self):
        self.s.store.save(fixture(id='b'));self.s.store.save(fixture(id='c'))
        self.s.set_expected('a', True)
        with self.s.store.connect() as db:
            db.execute("UPDATE mail SET decision=CASE id WHEN 'a' THEN 'keep' WHEN 'b' THEN 'hide' ELSE 'uncertain' END")
        self.assertEqual(self.s.snapshot()['score'], {'correct':2,'evaluated':3,'total':3})
        self.s.reset_filter()
        restored=MailService(self.tmp.name,'http://127.0.0.1:5173')
        self.assertTrue(next(m for m in restored.snapshot()['messages'] if m['id']=='a')['expectedKeep'])
        self.assertEqual(restored.snapshot()['score']['evaluated'],0)
        with self.assertRaises(ServiceError): self.s.set_expected('a', 'true')
    def test_prompt_edit_used_for_run(self):
        from pathlib import Path
        prompt=Path(self.tmp.name)/'prompt.txt';prompt.write_text('Original')
        with patch('webmail.service.PROMPT_FILE',prompt),patch.object(self.s,'start',return_value={}) as start:
            self.s.run_filter('New JSON prompt')
            self.assertEqual(self.s.snapshot()['prompt'],'New JSON prompt')
            start.assert_called_once_with(retry=True,classification_only=True)
            with self.assertRaises(ServiceError):self.s.run_filter('  ')
            self.assertEqual(prompt.read_text().strip(),'New JSON prompt')
    def test_read_is_local_and_persistent(self):
        with patch.object(self.s,'gmail') as gmail:
            self.assertFalse(self.s.read('a')['unread']);gmail.assert_not_called()
        self.assertIn('UNREAD',self.s.store.message('a')['labels'])
        self.assertFalse(MailService(self.tmp.name,'http://127.0.0.1:5173').public(self.s.store.message('a'))['unread'])
    def test_sync_deduplicates_and_drops_pagination(self):
        raw=EmailMessage();raw['From']='A <a@example.com>';raw.set_content('Hi')
        def gmail(path,params=None):
            if path=='messages':return {'messages':[{'id':'a'}], 'nextPageToken':'page3' if params.get('pageToken') else 'page2'}
            return resource(raw)
        with patch.object(self.s,'gmail',side_effect=gmail):self.s.sync();self.s.sync(True);self.s.sync()
        self.assertEqual(len(self.s.store.rows()),1);self.assertIsNone(self.s.store.get('next'))
    def test_review_shows_all_decisions_without_bodies(self):
        self.s.store.save(fixture(id='b'))
        with self.s.store.connect() as db:
            db.execute("UPDATE mail SET decision='keep' WHERE id='a'");db.execute("UPDATE mail SET decision='hide' WHERE id='b'")
        messages=self.s.snapshot()['messages'];self.assertEqual({m['id']:m['decision'] for m in messages},{'a':'keep','b':'hide'});self.assertNotIn('body',messages[0])
    def test_changed_content_invalidates_hidden_cache(self):
        with self.s.store.connect() as db:db.execute("UPDATE mail SET decision='hide',cache_key='old'")
        m=self.s.store.message('a');m['labels']=[];self.s.store.save(m);self.assertEqual(self.s.store.rows()[0][2],'hide')
        m['body']='New personal reply';self.s.store.save(m);self.assertIsNone(self.s.store.rows()[0][2])
    def test_cache_is_capped_to_hundred(self):
        for i in range(120):
            m=fixture(id=str(i));m['received']=i;self.s.store.save(m)
        self.s.trim_cache()
        rows=self.s.store.rows();self.assertEqual(len(rows),100)
        self.assertIn('119',[m['id'] for m,a,d,c in rows]);self.assertNotIn('0',[m['id'] for m,a,d,c in rows])
    def test_low_confidence_does_not_override_choice(self):
        response=answer('hide')
        response['answers']['decision'].update(confidence=.02,probabilities={'keep':.32,'hide':.35,'uncertain':.33})
        with patch('webmail.service.private_config',return_value={'key':'test','model':'jev-1.13.0'}),patch('webmail.service.request_json',return_value=response) as call:
            self.s.classify_pending()
            self.assertEqual(set(call.call_args.args[1]['questions']['decision']['criteria']),{'keep','hide','uncertain'})
        message=self.s.snapshot()['messages'][0]
        self.assertEqual(message['decision'],'hide');self.assertEqual(message['confidence'],.02)
        self.s.reset_filter()
        self.assertNotIn('confidence',self.s.snapshot()['messages'][0])
    def test_old_cache_reader_repairs_or_falls_back_without_losing_labels(self):
        old=self.s.store.message('a');old['parserVersion']=2
        old['body']='Latest answer';old['html']='<p>Incorrect old quote</p>'
        self.s.store.save(old);self.s.set_expected('a',True)
        with patch.object(self.s,'gmail',side_effect=ServiceError('Offline')):
            result=self.s.read('a')
        self.assertEqual(result['conversation'][0]['body'],'Latest answer')
        self.assertEqual(result['conversation'][0]['html'],'')
        self.assertTrue(result['expectedKeep'])
        raw=EmailMessage();raw['From']='Teacher <teacher@example.com>';raw['Subject']='Re: Question';raw.set_content('Restored latest answer')
        with patch.object(self.s,'gmail',return_value=resource(raw)):
            result=self.s.read('a')
        self.assertEqual(result['conversation'][0]['body'],'Restored latest answer')
        self.assertTrue(result['expectedKeep']);self.assertFalse(result['unread'])
        with self.s.store.connect() as db:self.assertEqual(db.execute("SELECT retained FROM mail WHERE id='a'").fetchone()[0],1)
    def test_search_includes_archived_and_does_not_pollute_cache(self):
        message=EmailMessage();message['From']='Sender <sender@example.com>';message['Subject']='Archived result';message.set_content('Searchable body')
        raw=resource(message,id='archived',labels=['UNREAD'])
        def gmail(path,params):
            if path=='messages':
                self.assertEqual(params,{'q':'subject:result','maxResults':25,'pageToken':'page2'})
                return {'messages':[{'id':'archived'}],'nextPageToken':'page3'}
            return raw
        with patch.object(self.s,'gmail',side_effect=gmail):result=self.s.search('subject:result','page2')
        self.assertEqual(result['nextPage'],'page3');self.assertEqual(result['messages'][0]['id'],'archived')
        self.assertEqual([m['id'] for m,a,d,c in self.s.store.rows()],['a'])
        detail=self.s.read('archived');self.assertFalse(detail['unread']);self.assertIn('Searchable body',detail['body'])
        with self.assertRaises(ServiceError):self.s.search(' ')
    def test_manual_keep_is_retained_even_after_uncheck(self):
        self.s.set_expected('a', True)
        self.s.set_expected('a', False)
        self.s.reset_filter();self.s.trim_cache([])
        self.assertEqual([m['id'] for m,a,d,c in self.s.store.rows()], ['a'])
    def test_existing_manual_labels_migrate_to_retained(self):
        self.s.store.set('expected:a',True)
        reloaded=MailService(self.tmp.name,'http://127.0.0.1:5173')
        reloaded.trim_cache([])
        self.assertEqual([m['id'] for m,a,d,c in reloaded.store.rows()], ['a'])
    def test_kept_cache_survives_trim_reset_and_restart(self):
        self.s.store.set('read:a', True)
        with self.s.store.connect() as db: db.execute("UPDATE mail SET decision='keep' WHERE id='a'")
        self.s.reset_filter()
        self.s.store.save(fixture(id='b'))
        self.s.trim_cache(['b'])
        self.assertEqual({m['id'] for m,a,d,c in self.s.store.rows()}, {'a','b'})
        reloaded=MailService(self.tmp.name,'http://127.0.0.1:5173')
        reloaded.trim_cache([])
        self.assertEqual([m['id'] for m,a,d,c in reloaded.store.rows()], ['a'])
        self.assertTrue(reloaded.store.get('read:a'))
        self.assertEqual(reloaded.store.message('a')['body'],fixture()['body'])
    def test_kept_mail_does_not_consume_recent_cache_slots(self):
        m=fixture(id='old');m['received']=0;self.s.store.save(m)
        with self.s.store.connect() as db: db.execute("UPDATE mail SET decision='keep' WHERE id='old'")
        for i in range(110):
            m=fixture(id=str(i));m['received']=i+1;self.s.store.save(m)
        self.s.trim_cache()
        self.assertEqual(len(self.s.store.rows()),101)
        self.s.store.message('old')
    def test_reset_reveals_and_persists_pause(self):
        with self.s.store.connect() as db:db.execute("UPDATE mail SET decision='hide',cache_key='old'")
        self.s.store.set('read:a',True)
        snapshot=self.s.reset_filter();self.assertEqual(len(snapshot['messages']),1);self.assertFalse(snapshot['filterEnabled'])
        self.assertFalse(snapshot['messages'][0]['unread'])
        reloaded=MailService(self.tmp.name,'http://127.0.0.1:5173')
        with patch.object(reloaded,'classify_message') as classify:reloaded.classify_pending();classify.assert_not_called()
    def test_reset_discards_inflight_result(self):
        import threading
        entered=threading.Event();release=threading.Event()
        def classify(*args):entered.set();release.wait(3);return 'hide'
        with patch('webmail.service.private_config',return_value={'key':'test','model':'jev-1.13.0'}),patch.object(self.s,'classify_message',side_effect=classify):
            worker=threading.Thread(target=self.s.classify_pending);worker.start();self.assertTrue(entered.wait(2))
            self.s.reset_filter();release.set();worker.join(3)
        self.assertIsNone(self.s.store.rows()[0][2]);self.assertEqual(len(self.s.snapshot()['messages']),1)
    def test_prompt_content_changes_cache_key(self):
        m=self.s.store.message('a')
        self.assertNotEqual(self.s.content_key(m,prompt='one'),self.s.content_key(m,prompt='two'))
    def test_restore_token_and_refresh(self):
        import time
        self.s.store.set('google_tokens',{'access_token':'cached-token','refresh_token':'refresh','expires':time.time()+3600})
        with patch('webmail.service.request_json') as request:self.assertEqual(self.s.token(),'cached-token');request.assert_not_called()
        self.s.store.set('google_tokens',{'access_token':'expired','refresh_token':'refresh','expires':0})
        with patch.object(self.s,'config',return_value={'client_id':'test','client_secret':'test'}),patch('webmail.service.request_json',return_value={'access_token':'new','expires_in':3600}):
            self.assertEqual(self.s.token(),'new');self.assertEqual(self.s.store.get('google_tokens')['refresh_token'],'refresh')
        self.s.store.set('google_tokens',{'refresh_token':'refresh','expires':0})
        with patch.object(self.s,'config',return_value={'client_id':'test','client_secret':'test'}),patch('webmail.service.request_json',side_effect=ServiceError('Expired',400)):
            with self.assertRaises(ServiceError) as error:self.s.token()
            self.assertEqual(error.exception.status,401)
    def test_only_readonly_scope(self):
        from webmail.service import SCOPE
        self.assertEqual(SCOPE,'https://www.googleapis.com/auth/gmail.readonly')

class SecurityTests(unittest.TestCase):
    def handler(self,**headers):
        import server
        h=object.__new__(server.Handler);h.path='/api/read';h.headers={'Host':'127.0.0.1:'+str(server.PORT),'Cookie':'mail_session='+server.SESSION,'Origin':server.ORIGIN,'X-CSRF-Token':server.CSRF,**headers};return h
    def test_origin_csrf_and_session(self):
        self.handler().guard(write=True)
        for changes in ({'Host':'evil.example'},{'Origin':'https://evil.example'},{'X-CSRF-Token':''},{'Cookie':''},{'Sec-Fetch-Site':'cross-site'}):
            with self.assertRaises(ServiceError):self.handler(**changes).guard(write=True)

if __name__=='__main__':unittest.main()
