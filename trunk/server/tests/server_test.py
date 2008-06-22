from twisted.internet import defer
from twisted.mail import smtp
from twisted.python import failure
from twisted.trial import unittest
import logging

import smtp2web

class TestMessageHandler(object):
  def __init__(self, fails=False):
    self.id = None
    self.fails = fails
    self.invocations = []
    
  def invoke(self, sender, rcpt, message):
    self.invocations.append((sender, rcpt, message))
    if self.fails:
      return defer.fail(failure.Failure(smtp2web.MessageSubmissionError()))
    else:
      return defer.succeed(None)


class TestSettings(object):
  def __init__(self):
    self.max_message_size = 256
    self.usermap = {}
    self.usermap_lastupdated = None
    self.logentries = []
  
  def load(self):
    mapping = smtp2web.DomainMapping()
    mapping.updateMapping("test", False, TestMessageHandler(False), 0)
    mapping.updateMapping("fail", False, TestMessageHandler(True), 0)
    self.usermap['smtp2web.com'] = mapping
    
    mapping = smtp2web.DomainMapping()
    mapping.updateMapping("fail", False, TestMessageHandler(True), 0)
    mapping.updateMapping("", True, TestMessageHandler(False), 0)
    self.usermap['testdomain.com'] = mapping
  
  def sync(self):
    return


class ServerTest(unittest.TestCase):
  def setUp(self):
    self.settings = TestSettings()
    self.settings.load()
    self.factory = smtp2web.ESMTPFactory(self.settings)
    self.smtp = self.factory.buildProtocol(None)
    # Cheating here to bypass testing smtp.ESMTP
    self.delivery = self.smtp.deliveryFactory.getMessageDelivery()
    self.sender = smtp.Address("test@test.com")
    self.test_message = """From: Me <me@us.com>
To: You <you@them.com>
Subject: Test

This is a test message."""
    
  def _sendMessageData(self, message_builder):
    message = message_builder()
    for line in self.test_message.split("\n"):
      message.lineReceived(line)
    return message.eomReceived()
  
  def _sendMessage(self, rcpt, send_body=True):
    self.failUnlessEqual(self.delivery.validateFrom(None, self.sender),
                         self.sender)
    ret = defer.maybeDeferred(self.delivery.validateTo, rcpt)
    if send_body:
      ret.addCallback(self._sendMessageData)
    return ret
  
  def _checkLogs(self, result, logs):
    for logentry, testentry in zip(self.settings.logentries, logs):
      (log_uuid, log_id, log_host, log_level, log_ts, log_sender, log_rcpt,
       log_len, log_msg) = logentry
      self.failUnlessEqual((log_id, log_host, log_level, log_sender, log_rcpt,
                            log_len), testentry)
  
  def test_send_message(self):
    rcpt = smtp.User("test@smtp2web.com", None, object(), self.sender)
    ret = self._sendMessage(rcpt)
    handler = self.settings.usermap['smtp2web.com'].findHandler("test")
    ret.addCallback(lambda x: self.failUnlessEqual(handler.invocations,
                    [(str(self.sender), str(rcpt.dest), self.test_message)]))
    ret.addCallback(self._checkLogs, [("test", "smtp2web.com", logging.DEBUG,
                    str(self.sender), str(rcpt.dest), len(self.test_message))])
    return ret
  
  def test_send_wildcard(self):
    rcpt = smtp.User("test@testdomain.com", None, object(), self.sender)
    ret = self._sendMessage(rcpt)
    handler = self.settings.usermap['testdomain.com'].findHandler("test")
    ret.addCallback(lambda x: self.failUnlessEqual(handler.invocations,
                    [(str(self.sender), str(rcpt.dest), self.test_message)]))
    ret.addCallback(self._checkLogs, [("", "testdomain.com", logging.DEBUG,
                    str(self.sender), str(rcpt.dest), len(self.test_message))])
    return ret

  def test_send_fail(self):
    rcpt = smtp.User("fail@smtp2web.com", None, object(), self.sender)
    ret = self._sendMessage(rcpt)
    ret.addCallbacks(
        self.fail,
        lambda failure: failure.trap(smtp2web.MessageSubmissionError)
    )
    handler = self.settings.usermap['smtp2web.com'].findHandler("fail")
    ret.addCallback(lambda x: self.failUnlessEqual(handler.invocations,
                    [(str(self.sender), str(rcpt.dest), self.test_message)]))
    ret.addCallback(self._checkLogs, [("fail", "smtp2web.com", logging.ERROR,
                    str(self.sender), str(rcpt.dest), len(self.test_message))])
    return ret

  def test_invalid_address(self):
    rcpt = smtp.User("doesnotexist@smtp2web.com", None, object(), self.sender)
    ret = self._sendMessage(rcpt, False)
    ret.addCallbacks(
        self.fail,
        lambda failure: failure.trap(smtp.SMTPBadRcpt)
    )
    ret.addCallback(self._checkLogs, [("doesnotexist", "smtp2web.com",
                    logging.ERROR, str(self.sender), str(rcpt.dest), 0)])
    return ret

  def test_invalid_domain(self):
    rcpt = smtp.User("user@invaliddomain.com", None, object(), self.sender)
    ret = self._sendMessage(rcpt, False)
    ret.addCallbacks(
        self.fail,
        lambda failure: failure.trap(smtp.SMTPBadRcpt)
    )
    ret.addCallback(self._checkLogs, [("user", "invaliddomain.com",
                    logging.ERROR, str(self.sender), str(rcpt.dest), 0)])
    return ret

  def test_message_too_long(self):
    rcpt = smtp.User("test@smtp2web.com", None, object(), self.sender)
    self.test_message += "-" * 256
    ret = self._sendMessage(rcpt)
    
    def _checkFailure(failure):
      failure.trap(smtp.SMTPServerError)
      self.failUnlessEqual(failure.value.code, 552)

    ret.addCallbacks(self.fail, _checkFailure)
    ret.addCallback(self._checkLogs, [("test", "smtp2web.com", logging.ERROR,
                    str(self.sender), str(rcpt.dest), len(self.test_message))])
    return ret
