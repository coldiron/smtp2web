from twisted.application import service
from twisted.application import internet
from twisted.enterprise import adbapi

import sys
import os
sys.path.append(os.dirname(__file__))

import smtp2web

application = service.Application("smtp2web Service")

settings = smtpserver.Settings(secret_key="<enter secret key here>",
                               state_file="state", master_host="localhost:8081")

smtpServerFactory = smtp2web.ESMTPFactory(settings)
smtpServerService = internet.TCPServer(2025, smtpServerFactory)
smtpServerService.setServiceParent(application)
