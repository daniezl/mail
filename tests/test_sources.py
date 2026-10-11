import unittest
from webmail.sources import source_mailbox

class SourceTests(unittest.TestCase):
    def source(self, message):
        return source_mailbox(message,'me@gmail.com',['me@school.example','me@outlook.com'])
    def test_group_and_cc_match_only_own_accounts(self):
        self.assertEqual(self.source({'headers':{'to':'Other <other@school.example>; Me <me@school.example>; Third <third@example.com>'}}),'me@school.example')
        self.assertEqual(self.source({'headers':{'to':'other@example.com','cc':'me@outlook.com'}}),'me@outlook.com')
    def test_forwarder_when_original_recipients_do_not_include_me(self):
        m={'mailbox':'other@example.com','headers':{'to':'other@example.com'},'deliveryHeaders':{'from':['Me <me@school.example>'],'subject':['FW: plans'],'delivered-to':['me@gmail.com']}}
        self.assertEqual(self.source(m),'me@school.example')
    def test_original_outlook_address_wins_over_school_forwarder(self):
        m={'headers':{'to':'me@outlook.com'},'deliveryHeaders':{'from':['me@school.example'],'subject':['FW: Test']}}
        self.assertEqual(self.source(m),'me@outlook.com')
    def test_bcc_direct_gmail_and_unknown_forward(self):
        m={'headers':{'to':'undisclosed-recipients:;'},'deliveryHeaders':{'delivered-to':['me@gmail.com']}}
        self.assertEqual(self.source(m),'me@gmail.com')
        m['deliveryHeaders']['subject']=['Fwd: Unknown'];self.assertEqual(self.source(m),'')
    def test_never_assign_somebody_elses_mailbox(self):
        self.assertEqual(self.source({'mailbox':'other@gmail.com','headers':{'to':'other@gmail.com'}}),'')
    def test_multiple_own_addresses_stay_ambiguous(self):
        self.assertEqual(self.source({'headers':{'to':'me@school.example, me@outlook.com'}}),'')
