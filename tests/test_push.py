import tempfile
import time
import unittest
from webmail.service import Store, ServiceError
from webmail.push import PushService

class Provider:
    configured = True
    def __init__(self): self.calls=[]; self.result=(200,'')
    def send(self,token,environment,message):
        self.calls.append(message['id']); return self.result

class Mail:
    def __init__(self,directory): self.store=Store(directory)

class PushTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.mail=Mail(self.directory.name); self.provider=Provider()
        self.push=PushService(self.mail,self.provider); self.token='a'*64
    def message(self,id,decision='keep',received=None):
        m={'id':id,'sender':'Sender','subject':'Subject','body':'Text','headers':{},'email':'a@example.com',
           'mailbox':'b@example.com','complete':True,'received':received or time.time()+1,'labels':['INBOX','UNREAD']}
        self.mail.store.save(m)
        with self.mail.store.connect() as db:db.execute('UPDATE mail SET decision=? WHERE id=?',(decision,id))
    def register(self):self.push.register(self.token,'development')
    def test_baseline_no_history_and_reregister(self):
        self.message('old');self.register();self.push.deliver();self.assertEqual(self.provider.calls,[])
        self.message('new');self.register();self.push.deliver();self.assertEqual(self.provider.calls,['new'])
    def test_keep_uncertain_not_hide_and_no_duplicates(self):
        self.register()
        for id in ['keep','uncertain','hide']:self.message(id,id)
        self.push.deliver();self.push.deliver()
        self.assertEqual(self.provider.calls,['keep','uncertain'])
    def test_pending_waits_and_reset_does_not_replay(self):
        self.register();self.message('new',None);self.push.deliver();self.assertEqual(self.provider.calls,[])
        self.message('new','uncertain');self.push.deliver()
        self.message('new',None);self.push.deliver();self.message('new','keep');self.push.deliver()
        self.assertEqual(self.provider.calls,['new'])
    def test_failure_retry_backoff_and_restart(self):
        self.register();self.message('new');self.provider.result=(503,'');self.push.deliver();self.push.deliver()
        self.assertEqual(len(self.provider.calls),1)
        with self.mail.store.connect() as db:db.execute('UPDATE push_delivery SET retry=0')
        self.provider.result=(200,'');PushService(self.mail,self.provider).deliver()
        self.assertEqual(len(self.provider.calls),2)
    def test_invalid_device_removed(self):
        self.register();self.message('new');self.provider.result=(410,'Unregistered');self.push.deliver()
        with self.mail.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM push_devices').fetchone()[0],0)
    def test_old_or_read_mail_skipped(self):
        self.register();self.message('old',received=time.time()-90000);self.message('read')
        self.mail.store.set('read:read',True);self.push.deliver();self.assertEqual(self.provider.calls,[])
    def test_gmail_read_mail_skipped(self):
        self.register();self.message('read-elsewhere')
        with self.mail.store.connect() as db:
            import json
            row = db.execute('SELECT payload FROM mail WHERE id=?', ('read-elsewhere',)).fetchone()
            message = json.loads(row[0]);message['labels'] = ['INBOX']
            db.execute('UPDATE mail SET payload=? WHERE id=?', (json.dumps(message),'read-elsewhere'))
        self.push.deliver();self.assertEqual(self.provider.calls,[])
    def test_validation(self):
        for token,env in [('bad','development'),('a'*64,'other'),(None,'production')]:
            with self.assertRaises(ServiceError):self.push.register(token,env)

if __name__=='__main__':unittest.main()
