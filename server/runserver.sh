#!/bin/sh
cd /usr/local/smtp2web
twistd -y smtpserver.tac -u smtp2web -g smtp2web -l /var/log/smtp2web/log --pidfile /var/run/smtp2web.pid
