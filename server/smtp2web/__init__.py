from zope.interface import implements

from twisted.application import service
from twisted.application import internet
from twisted.internet import protocol, defer, reactor
from twisted.mail import smtp
from twisted.python import log
from twisted.web import client
import twisted.internet.error

import cgi
import csv
import cPickle
import cStringIO
import datetime
import hashlib
import logging
import os
import re
import socket
import sys
import time
import urllib
import urlparse
import uuid


class Settings(object):
  def __init__(self, **kwargs):
    self.max_message_size = 1048576
    self.usermap = {}
    self.usermap_lastupdated = None
    self.logentries = []
    self.sync_interval = 60.0
    self.master_host = "smtp2web.com"
    self.state_file = None
    self.hostname = socket.getfqdn()
    self.secret_key = None
    for key, val in kwargs.iteritems():
      setattr(self, key, val)
  
  def load(self):
    try:
      f = open(self.state_file, "r")
      self.usermap, self.usermap_lastupdated = cPickle.load(f)
      f.close()
    except IOError, e:
      log.err("Unable to load state file; starting from scratch.")
  
  def save(self):
    f = open(self.state_file, "w+")
    cPickle.dump((self.usermap, self.usermap_lastupdated), f)
    f.close()


def getPage(url, *args, **kwargs):
  scheme, host, port, path = client._parse(url)
  factory = client.HTTPClientFactory(url, *args, **kwargs)
  factory.noisy = False
  if scheme == "https":
    from twisted.internet import ssl
    reactor.connectSSL(host, port, factory, ssl.ClientContextFactory())
  else:
    reactor.connectTCP(host, port, factory)
  return factory.deferred


class MessageSubmissionError(Exception):
  pass


class MessageHandler(object):
  def __init__(self, url):
    self.url = url
    self.id = None
  
  def invoke(self, sender, rcpt, message):
    urlparts = urlparse.urlparse(self.url)
    qs = cgi.parse_qsl(urlparts.query, True)
    qs.append(("from", sender))
    qs.append(("to", rcpt))
    url = urlparse.urlunparse(urlparts[:4]+(urllib.urlencode(qs), urlparts[5]))
    
    ret = getPage(url, method="POST", postdata=message,
                  headers={"Content-Type": "multipart/rfc822"},
                  agent="smtp2web/1.0", timeout=30)
    
    def handleError(failure):
      err = None
      if failure.type == twisted.web.error.Error:
        raise MessageSubmissionError(
            "Received %s %s from server when sending POST request to %s"
            % (failure.value.args[:2] + (url,)))
      elif failure.type == twisted.internet.error.ConnectionRefusedError:
        raise MessageSubmissionError("Connection refused by %s"
                                     % (urlparts.netloc, ))
      else:
        return failure
        
    ret.addErrback(handleError)
    return ret


class DomainMapping(object):
  def __init__(self):
    self._users = dict()
    self._regexes = list()
    self._regexmap = dict()
  
  def updateMapping(self, id, is_regex, handler, priority):
    assert handler.id == None
    handler.id = id
    if is_regex:
      if not id:
        entry = (re.compile(".*"), handler, priority)
      else:
        entry = (re.compile(id), handler, priority)
      if id in self._regexmap:
        i = self._regexes.index(self._regexmap[id])
        self._regexes[i] = entry
      else:
        self._regexes.append(entry)
      self._regexes.sort(key=lambda x:x[2])
      self._regexmap[id] = entry
    else:
      self._users[id] = handler
  
  def deleteMapping(self, id, is_regex):
    if is_regex:
      if id in self._regexmap:
        self._regexes.remove(self._regexmap[id])
        del self._regexmap[id]
    else:
      if id in self._users:
        del self._users[id]
  
  def findHandler(self, user):
    if user in self._users:
      return self._users[user]
    else:
      for regex, handler, priority in self._regexes:
        if regex.search(user):
          return handler
      return None


class Message(object):
  implements(smtp.IMessage)

  def __init__(self, settings, sender, rcpt, handler):
    self.settings = settings
    self.sender = sender
    self.rcpt = rcpt
    self.handler = handler
    self.lines = []
    self.total_length = 0
  
  def lineReceived(self, line):
    line_len = len(line)
    if (self.total_length + line_len) <= self.settings.max_message_size:
      self.lines.append(line)
      self.total_length += line_len + 1
    else:
      ts = time.mktime(datetime.datetime.now().utctimetuple())
      self.settings.logentries.append(
          (str(uuid.uuid1()), (self.handler.id, self.rcpt.domain), logging.ERROR, ts,
          str(self.sender), str(self.rcpt.dest), self.total_length,
          "Message exceeded maximum size of %d bytes." % (self.settings.max_message_size, )))
      raise smtp.SMTPServerError(552, "Message too long")
  
  def eomReceived(self):
    ret = self.handler.invoke(str(self.sender), str(self.rcpt.dest),
                              "\n".join(self.lines))
        
    def addLogEntry(response):
      ts = time.mktime(datetime.datetime.now().utctimetuple())
      self.settings.logentries.append(
          (str(uuid.uuid1()), self.handler.id, self.rcpt.dest.domain, logging.DEBUG,
           ts, str(self.sender), str(self.rcpt.dest), self.total_length, None))
    ret.addCallback(addLogEntry)

    def handleError(failure):
      if failure.type == MessageSubmissionError:
        ts = time.mktime(datetime.datetime.now().utctimetuple())
        self.settings.logentries.append(
            (uuid.uuid1(), self.handler.id, self.rcpt.dest.domain, logging.ERROR,
             ts, str(self.sender), str(self.rcpt.dest), self.total_length,
             str(failure.value)))
      return failure
    ret.addErrback(handleError)

    return ret
  
  def connectionLost(self):
    pass


class MessageDelivery(object):
  """Encapsulates a single message transaction."""
  implements(smtp.IMessageDelivery)
  
  def __init__(self, settings):
    self.sender = None
    self.settings = settings
  
  def validateTo(self, user):
    mapping = self.settings.usermap.get(user.dest.domain, None)
    if not mapping:
      raise smtp.SMTPBadRcpt(user.dest)
    handler = mapping.findHandler(user.dest.local)
    if not handler:
      raise smtp.SMTPBadRcpt(user.dest)
    return lambda: Message(self.settings, self.sender, user, handler)
  
  def validateFrom(self, helo, origin):
    self.sender = origin
    return origin

  def receivedHeader(self, helo, origin, recipients):
    heloStr = ""
    if helo[0]:
      heloStr = " helo=%s" % (helo[0],)
    domain = self.settings.hostname
    from_ = "from %s ([%s]%s)" % (helo[0], helo[1], heloStr)
    by = "by %s with smtp2web (1.0)" % (domain, )
    for_ = "for %s; %s" % (' '.join(map(str, recipients)),
                           smtp.rfc822date())
    return "Received: %s\n\t%s\n\t%s" % (from_, by, for_)

class MessageDeliveryFactory(object):
  """One MessageDeliveryFactory is created per SMTP connection."""
  implements(smtp.IMessageDeliveryFactory)
  
  def __init__(self, settings):
    self.settings = settings
  
  def getMessageDelivery(self):
    return MessageDelivery(self.settings)


class ESMTPFactory(protocol.ServerFactory):
  """Called to create a new MessageDeliveryFactory for each connection."""
  protocol = smtp.ESMTP
  
  def __init__(self, settings):
    self.settings = settings
    if os.path.exists(settings.state_file):
      reactor.callWhenRunning(self.settings.load)
    reactor.callWhenRunning(self.sync)
  
  def updateMappings(self):
    qs = {
        "hostname": self.settings.hostname,
        "last_updated": self.settings.usermap_lastupdated or "",
        "ver": 1,
    }
    qs["request_hash"] = hashlib.sha1(
        "%s:%s" % (self.settings.secret_key, qs["last_updated"])).hexdigest()
    url = "http://%s/api/get_mappings?%s" % (self.settings.master_host,
                                             urllib.urlencode(qs))
    ret = getPage(url, agent="smtp2web/1.0", timeout=30)
    
    def _doUpdate(result):
      result = [x for x in result.split("\n") if x]
      if len(result) > 0:
        reader = csv.reader(result)
        updated = 0
        for i, (user, host, url, ts, deleted) in enumerate(reader):
          if i == 0 and ts == self.settings.usermap_lastupdated: continue
          updated += 1  
          
          if host not in self.settings.usermap:
            self.settings.usermap[host] = DomainMapping()
          
          mapping = self.settings.usermap[host]
          handler = MessageHandler(url)
          if deleted == "True":
            if not user:
              mapping.deleteMapping(".*", True)
            else:
              mapping.deleteMapping(user, False)
          else:
            if not user:
              mapping.updateMapping("", True, handler, sys.maxint)
            else:
              mapping.updateMapping(user, False, handler, 0)
          self.settings.usermap_lastupdated = ts
        self.settings.save()
        if updated:
          log.msg("Updated %d user map entries" % (updated, ))
        
        if len(result) == 100:
          return updateMappings()
        else:
          return result
    ret.addCallback(_doUpdate)
    
    def _handleError(failure):
      log.err("Error fetching handler updates from %s: %s"
              % (url, str(failure.value)))
    ret.addErrback(_handleError)
    return ret
  
  def uploadLogs(self):
    if not self.settings.logentries: return defer.succeed(None)
    data = cStringIO.StringIO()
    writer = csv.writer(data)
    writer.writerows(self.settings.logentries[:50])
    data = data.getvalue()
    sha1 = hashlib.sha1(self.settings.secret_key)
    sha1.update(":")
    sha1.update(data)
    request_hash = sha1.hexdigest()
    
    url = ("http://%s/api/upload_logs?version=1&hostname=%s&request_hash=%s"
           % (self.settings.master_host, self.settings.hostname, request_hash))
    ret = getPage(url, method="POST", postdata=data,
                  headers={"Content-Type": "text/csv"},
                  agent="smtp2web/1.0", timeout=30)
    
    def _handleResponse(result):
      self.settings.logentries[:50] = []
      return self.uploadLogs()
    ret.addCallback(_handleResponse)

    def _handleError(failure):
      log.err("Error uploading log entries to %s: %s"
              % (url, str(failure.value)))
    ret.addErrback(_handleError)
    
    return ret
  
  def sync(self):
    """Syncs with the database."""
    dl = defer.DeferredList([self.updateMappings(), self.uploadLogs()])
    def _reSync(result):
      reactor.callLater(self.settings.sync_interval, self.sync)
    dl.addBoth(_reSync)
  
  def buildProtocol(self, addr):
    p = self.protocol()
    p.deliveryFactory = MessageDeliveryFactory(self.settings)
    p.factory = self
    return p
