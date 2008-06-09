import email

from google.appengine.api import memcache

import lib
import model

class ReceiveMessagePage(lib.BaseHandler):
  def post(self):
    memcache.set("message", self.request.body)


class ShowMessagePage(lib.BaseHandler):
  def get(self):
    template_values = self.GetTemplateValues()
    msg_text = memcache.get("message")
    if msg_text:
      msg = email.message_from_string(msg_text)
      template_values["from"] = msg["From"][:msg["To"].find("@")+2] + "...>"
      template_values["to"] = msg["To"]
      template_values["subject"] = msg["Subject"]
      if not msg.is_multipart():
        template_values["message"] = msg.get_payload()
      else:
        for part in msg.walk():
          if part.get_content_type() == "text/plain":
            template_values["message"] = part.get_payload()
            break
        else:
          template_values["message"] = "Message is MIME multipart with no plain text part."
    else:
      template_values["message"] = "No messages received yet! Send one to test@smtp2web.com."
    self.RenderTemplate("message.html", template_values)
