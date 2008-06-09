import lib
import model

class IndexPage(lib.BaseHandler):
  def get(self):
    template_values = self.GetTemplateValues()
    if self.user:
      mappings = model.Mapping.all().filter("owner =", self.user)
      template_values['mappings'] = mappings
    self.RenderTemplate("index.html", template_values)

class AboutPage(lib.BaseHandler):
  def get(self):
    self.RenderTemplate("about.html", self.GetTemplateValues())
