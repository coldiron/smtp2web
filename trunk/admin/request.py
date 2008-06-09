from google.appengine.ext import webapp
from google.appengine.ext.webapp import util

import controllers

local_domains = [ "smtp2web.com" ]


application = webapp.WSGIApplication([
    ("/", controllers.IndexPage),
    ("/addmapping", controllers.AddMappingPage),
    ("/test_email", controllers.ReceiveMessagePage),
    ("/api/get_mappings", controllers.GetMappingsPage),
    ("/api/upload_logs", controllers.UploadLogsPage),
    ("/mapping/(_[a-z0-9]+)/delete", controllers.DeleteMappingPage),
    ("/mapping/(_[a-z0-9]+)/logs", controllers.LogsPage),   
])


def main():
  util.run_wsgi_app(application)


if __name__ == "__main__":
  main()
