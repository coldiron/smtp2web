import os
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.api import users

def RequiresLogin(fun):
  def RequiresLoginDecorator(self, *args, **kwargs):
    if not self.user:
      self.redirect(users.create_login_url("/"))
      return
    return fun(self, *args, **kwargs)
  return RequiresLoginDecorator


class BaseHandler(webapp.RequestHandler):
  def initialize(self, request, response):
    super(BaseHandler, self).initialize(request, response)
    self.user = users.get_current_user()

  def GetTemplatePath(self, template):
    return os.path.join(os.path.dirname(__file__), "..", "templates", template)

  def RenderTemplate(self, template_name, template_values):
    self.response.out.write(
        template.render(self.GetTemplatePath(template_name),
                               template_values))

  def GetTemplateValues(self):
    return {
        "user": self.user,
        "login_url": users.create_login_url("/"),
        "logout_url": users.create_logout_url("/"),
    }
