import hashlib
import csv
import datetime
import time
import logging

import lib
import model

class ApiPage(lib.BaseHandler):
  def initialize(self, request, response):
    super(lib.BaseHandler, self).initialize(request, response)
    self.hostname = self.request.GET.get("hostname", None)
    self.server = None
    if self.hostname:
      self.server = model.SmtpServer.get_by_key_name(self.hostname)

  def check_hash(self, data):
    if not self.server: return False
    request_hash = self.request.GET.get("request_hash", None)
    sha1 = hashlib.sha1(self.server.secret_key)
    sha1.update(":")
    sha1.update(data)
    return request_hash == sha1.hexdigest()
  
class GetMappingsPage(ApiPage):
  def get(self):
    if not self.server:
      self.error(403)
      self.response.out.write("Invalid hostname")
      return
    last_updated = self.request.GET.get("last_updated", "")
    if last_updated:
      ts = datetime.datetime.fromtimestamp(float(last_updated))
    if not self.check_hash(last_updated):
      self.error(403)
      self.response.out.write("Request hash does not match")
    
    q = model.Mapping.all()
    if last_updated:
      q.filter("last_updated >=", ts)
    q.order("last_updated")
    mappings = q.fetch(100)
    
    self.response.headers["Content-Type"] = "text/csv"
    writer = csv.writer(self.response.out)
    writer.writerows((x.user, x.host, x.url,
                      time.mktime(x.last_updated.timetuple()))
                     for x in mappings)


class UploadLogsPage(ApiPage):
  def post(self):
    if not self.server:
      self.error(403)
      self.response.out.write("Not a valid server")
      return
    if not self.check_hash(self.request.body):
      self.error(403)
      self.response.out.write("Request hash does not match")
      return
    
    reader = csv.reader(self.request.body_file)
    for id, key, level, ts, sender, rcpt, length, msg in reader:
      if "@" in key:
        user, host = key.split("@", 1)
      else:
        user = ""
        host = key
      mapping = model.Mapping.get_by_address(user, host)
      if not mapping:
        logging.error("Unable to find mapping for '%s'" % key)
        continue
      
      level = int(level)
      model.LogEntry(key_name="_"+id,
                     mapping=mapping,
                     server=self.server,
                     ts=datetime.datetime.utcfromtimestamp(float(ts)),
                     sender=sender,
                     recipient=rcpt,
                     length=int(length),
                     message=msg,
                     is_warning=level>=logging.WARNING,
                     is_error=level>=logging.ERROR).put()
