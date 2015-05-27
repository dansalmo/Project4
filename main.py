#!/usr/bin/env python

"""
main.py -- Udacity conference server-side Python App Engine
    HTTP controller handlers for memcache & task queue access

$Id$

created by wesc on 2014 may 24

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'

import webapp2
from google.appengine.api import app_identity
from google.appengine.api import mail
from conference import ConferenceApi

from google.appengine.api import memcache
from google.appengine.ext import ndb
from models import Session

from conference import MEMCACHE_FEATURED_SPEAKER_KEY

class SetAnnouncementHandler(webapp2.RequestHandler):
    def get(self):
        """Set Announcement in Memcache."""
        ConferenceApi._cacheAnnouncement()
        self.response.set_status(204)

class SendConfirmationEmailHandler(webapp2.RequestHandler):
    def post(self):
        """Send email confirming Conference creation."""
        mail.send_mail(
            'noreply@%s.appspotmail.com' % (
                app_identity.get_application_id()),     # from
            self.request.get('email'),                  # to
            'You created a new Conference!',            # subj
            'Hi, you have created a following '         # body
            'conference:\r\n\r\n%s' % self.request.get(
                'conferenceInfo')
        )

# - - - Task 4: Add a Task - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
# The task will check if there is more than one session by this speaker at this conference,
# also add a new Memcache entry that features the speaker and session names.
class CheckFeaturedSpeakerHandler(webapp2.RequestHandler):
    def post(self):
        """set memcache entry if speaker has more than one session"""
        sessions = Session.query().filter(Session.speakerKey==self.request.get('speakerKey'))
        # Add one if the session key just added can not yet be found in the queried sessions
        not_found = not any(s.key.urlsafe() == self.request.get('sessionKey') for s in sessions)
        if sessions.count() + not_found > 1:
            memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, 
                '%s is our latest Featured Speaker' % self.request.get(
                'speakerDisplayName'))

app = webapp2.WSGIApplication([
    ('/crons/set_announcement', SetAnnouncementHandler),
    ('/tasks/send_confirmation_email', SendConfirmationEmailHandler),
    ('/tasks/check_featuredSpeaker', CheckFeaturedSpeakerHandler),
], debug=True)
