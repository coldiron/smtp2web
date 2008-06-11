from google.appengine.ext import webapp
from google.appengine.ext.webapp import util

import controllers


local_domains = [ "smtp2web.com" ]

class EmptyHandler(webapp.RequestHandler):
  def get(self):
    pass

application = webapp.WSGIApplication([
    ("/", controllers.IndexPage),
    ("/addmapping", controllers.AddMappingPage),
    ("/test_email", controllers.ReceiveMessagePage),
    ("/latest_message", controllers.ShowMessagePage),
    ("/api/get_mappings", controllers.GetMappingsPage),
    ("/api/upload_logs", controllers.UploadLogsPage),
    ("/mapping/(_[a-z0-9]+)/delete", controllers.DeleteMappingPage),
    ("/mapping/(_[a-z0-9]+)/logs", controllers.LogsPage),
    ("/smtp2web_0664fb0ba66df737.html", EmptyHandler),
    ("/about", controllers.AboutPage), 
])


def main():
  util.run_wsgi_app(application)


if __name__ == "__main__":
  main()
