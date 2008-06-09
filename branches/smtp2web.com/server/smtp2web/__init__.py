from zope.interface import implements

from twisted.application import service
from twisted.application import internet
from twisted.internet import protocol, defer, reactor
from twisted.mail import smtp
from twisted.python import log
from twisted.web import client
import twisted.internet.error

import cgi
import urllib
import urlparse
import logging
import cPickle
import os
import csv
import hashlib
import socket
import time
import datetime
import uuid
import cStringIO

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


class Message(object):
  implements(smtp.IMessage)

  def __init__(self, settings, sender, rcpt, url, address_key):
    self.settings = settings
    self.sender = sender
    self.post_url = url
    self.address_key = address_key
    self.rcpt = rcpt
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
          (str(uuid.uuid1()), self.address_key, logging.ERROR, ts,
          str(self.sender), str(self.rcpt.dest), self.total_length,
          "Message exceeded maximum size of %d bytes." % (self.settings.max_message_size, )))
      raise smtp.SMTPServerError(552, "Message too long")
  
  def eomReceived(self):
    urlparts = urlparse.urlparse(self.post_url)
    qs = cgi.parse_qsl(urlparts.query, True)
    qs.append(("from", str(self.sender)))
    qs.append(("to", str(self.rcpt.dest)))
    url = urlparse.urlunparse(urlparts[:4]+(urllib.urlencode(qs), urlparts[5]))
    
    ret = client.getPage(url, method="POST", postdata="\n".join(self.lines),
                         headers={"Content-Type": "multipart/rfc822"},
                         agent="smtp2web/1.0")
        
    def addLogEntry(response):
      ts = time.mktime(datetime.datetime.now().utctimetuple())
      self.settings.logentries.append(
          (str(uuid.uuid1()), self.address_key, logging.DEBUG,
           ts, str(self.sender), str(self.rcpt.dest), self.total_length, None))

    def handleError(failure):
      err = None
      if failure.type == twisted.web.error.Error:
        err = ("Received %s %s from server when sending POST request to %s"
               % (failure.value.args[:2] + (url,)))
      elif failure.type == twisted.internet.error.ConnectionRefusedError:
        err = "Connection refused by %s" % (urlparts.netloc, )
      
      if err:
        ts = time.mktime(datetime.datetime.now().utctimetuple())
        self.settings.logentries.append(
            (uuid.uuid1(), self.address_key, logging.ERROR,
             ts, str(self.sender), str(self.rcpt.dest), self.total_length, err))

      return failure
        
    ret.addCallback(addLogEntry)
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
    key = str(user.dest)
    url = self.settings.usermap.get(key, None)
    if not url:
      key = user.dest.domain
      url = self.settings.usermap.get(key, None)
    if not url:
      raise smtp.SMTPBadRcpt(user.dest)
    else:
      return lambda: Message(self.settings, self.sender, user, url, key)
  
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
    qs = {}
    qs["hostname"] = self.settings.hostname
    qs["last_updated"] = self.settings.usermap_lastupdated or ""
    qs["request_hash"] = hashlib.sha1(
        "%s:%s" % (self.settings.secret_key, qs["last_updated"])).hexdigest()
    url = "http://%s/api/get_mappings?%s" % (self.settings.master_host,
                                             urllib.urlencode(qs))
    ret = client.getPage(url, agent="smtp2web/1.0")
    
    def _doUpdate(result):
      result = [x for x in result.split("\n") if x]
      if len(result) > 1 or not self.settings.usermap_lastupdated:
        log.msg("Updating %d user map entries" % (len(result) - 1, ))
        reader = csv.reader(result)
        for user, host, url, ts in reader:
          if user:
            self.settings.usermap["%s@%s" % (user, host)] = url
          else:
            self.settings.usermap[host] = url
          self.settings.usermap_lastupdated = ts
        self.settings.save()
        
        if len(result) == 100:
          return updateMappings()
        else:
          return result
    ret.addCallback(_doUpdate)
    ret.addErrback(log.err)
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
    
    url = ("http://%s/api/upload_logs?hostname=%s&request_hash=%s"
           % (self.settings.master_host, self.settings.hostname, request_hash))
    ret = client.getPage(url, method="POST", postdata=data,
                         headers={"Content-Type": "text/csv"},
                         agent="smtp2web/1.0")
    
    def _handleResponse(result):
      self.settings.logentries[:50] = []
      return self.uploadLogs()
    ret.addCallback(_handleResponse)
    
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
