#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session, SessionForm, SessionForms, SessionTypes
from models import Speaker, SpeakerForm, SpeakerForms

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER"
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

SESSION_DEFAULTS = {
    'highlights': 'To be announced',
    'duration': 60,
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSIONS_BY_SPEAKER = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speakerKey=messages.StringField(1),
)

SESSIONS_BY_TYPE = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    type=messages.StringField(2),
)

SESSION_WISH_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1),
)
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city=="London")
        q = q.filter(Conference.topics=="Medical Innovations")
        q = q.filter(Conference.month==6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )

# - - - Task 1: Add Sessions to a Conference - - - - - - - - - - - - - - - - - - - -

    def _ndbKey(self, *args, **kwargs):
        # this try except clause is needed for NDB issue 143 
        # https://code.google.com/p/appengine-ndb-experiment/issues/detail?id=143
        try:
            key = ndb.Key(**kwargs)
        except Exception as e:
            if e.__class__.__name__ == 'ProtocolBufferDecodeError':
                key = 'Invalid Key'
        return key

    def _checkKey(self, key, websafeKey, kind):
        '''Check that conference exists and is the right Kind'''
        if key == 'Invalid Key':
            raise endpoints.NotFoundException(
                'Invalid key: %s' % websafeKey)

        if not key:
            raise endpoints.NotFoundException(
                'No %s found with key: %s' % (kind, websafeKey))

        if key.kind() != kind:
            raise endpoints.NotFoundException(
                'Not a key of the %s Kind: %s' % (kind, websafeKey))

    def _copySessionToForm(self, session, name=None):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                # convert typeOfSession to enum SessionTypes; just copy others
                if field.name == 'typeOfSession':
                    setattr(sf, field.name, getattr(SessionTypes, str(getattr(session,field.name))))
                else:
                    setattr(sf, field.name, getattr(session,field.name))
            elif field.name == "websafeKey":
                setattr(sf, field.name, session.key.urlsafe())
            elif field.name == "speakerDisplayName":
                setattr(sf, field.name, name)

            # convert startDateTime from session model to date and startTime for session Form
            startDateTime = getattr(session, 'startDateTime')
            if startDateTime:
                if field.name == 'date':
                    setattr(sf, field.name, str(startDateTime.date()))
                if hasattr(session, 'startDateTime') and field.name == 'startTime':
                    setattr(sf, field.name, str(startDateTime.time().strftime('%H:%M')))
        sf.check_initialized()
        return sf

    def _createSessionObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeConferenceKey']
        del data['websafeKey']
    
        # add default values for those missing (both data model & outbound Message)
        for df in SESSION_DEFAULTS:
            if data[df] in (None, []):
                data[df] = SESSION_DEFAULTS[df]
                setattr(request, df, SESSION_DEFAULTS[df])

        if data['typeOfSession']==None:
            del data['typeOfSession']
        else:
            data['typeOfSession'] = str(data['typeOfSession'])

        # set start time and date to be next available if not specified
        # convert dates from strings to Date objects;
        if data['startTime'] and data['date']:
            data['startDateTime'] = datetime.strptime(data['date'][:10] + ' ' + data['startTime'][:5], "%Y-%m-%d %H:%M")
        del data['startTime']
        del data['date']

        # get the conference key for where the session will be added
        conf = self._ndbKey(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # generate Session key as child of Conference
        s_id = Session.allocate_ids(size=1, parent=conf.key)[0]
        s_key = ndb.Key(Session, s_id, parent=conf.key)
        data['key'] = s_key

        # get the speakerDisplayName from Speaker entity if a speakerKey was provided
        if data['speakerKey']:
            speaker = self._ndbKey(urlsafe=request.speakerKey).get()
            data['speakerDisplayName'] = speaker.displayName

# - - - Task 4: Add a Task - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
            #Check the speaker. If there is more than one session by this speaker at this conference, also add a new Memcache entry that features the speaker and session names. You can choose the Memcache key.
            sessions = Session.query().filter(Session.speakerKey==data['speakerKey'])
            if sessions.count() >= 2:
                memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, '%s is our latest Featured Speaker' % data['speakerDisplayName'])
                sessionNames = '\n'.join(s.name for s in sessions)
                taskqueue.add(
                    params={
                        'email': user.email(),
                        'sessionNames': sessionNames,
                        'featuredSpeaker': data['speakerDisplayName']
                        },
                    url='/tasks/send_featuredSpeaker_email'
                    )
# - - - End Task 4 - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

        # create Session
        s = Session(**data)
        s.put()

        return self._copySessionToForm(s)

    #createSession(SessionForm, websafeConferenceKey) -- open only to the organizer of the conference
    @endpoints.method(SESSION_POST_REQUEST, SessionForm,
            path='conference/{websafeConferenceKey}/session',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create a new session for a conference. Open only to the organizer of the conference"""
        return self._createSessionObject(request)

    #getConferenceSessions(websafeConferenceKey) -- Given a conference, return all sessions    
    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='conference/{websafeConferenceKey}/sessions',
            http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Get list of all sessions for a conference."""

        conf = self._ndbKey(urlsafe=request.websafeConferenceKey)

        # check that conf is a conference key and it exists
        self._checkKey(conf, request.websafeConferenceKey, 'Conference')

        sessions = Session.query(ancestor=conf)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    #getConferenceSessionsByType(websafeConferenceKey, typeOfSession) Given a conference, return all sessions of a specified type (eg lecture, keynote, workshop)
    @endpoints.method(SESSIONS_BY_TYPE, SessionForms,
            path='conference/{websafeConferenceKey}/sessions/{type}',
            http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Get list of all sessions for a conference by type."""

        conf = self._ndbKey(urlsafe=request.websafeConferenceKey)

        # check that conf is a conference key and it exists
        self._checkKey(conf, request.websafeConferenceKey, 'Conference')
        
        sessions = Session.query(ancestor=conf).filter(Session.typeOfSession==str(getattr(SessionTypes, request.type)))

        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])


    #getSessionsBySpeaker(speaker) -- Given a speaker, return all sessions given by this particular speaker, across all conferences
    @endpoints.method(SESSIONS_BY_SPEAKER, SessionForms,
            path='sessions/bySpeaker',
            http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Get list of all sessions for a speaker accross all conferences.
           If no speakerKey is provided, all sessions are returned"""

        sessions = Session.query()
        if request.speakerKey:
            sessions = sessions.filter(Session.speakerKey==request.speakerKey) 
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

# - - - Task 1: Speaker entity creation - - - - - - - - - - - - - - - - - - - -

    def _copySpeakerToForm(self, speaker):
        """Copy relevant fields from Speaker to SpeakerForm."""
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                setattr(sf, field.name, getattr(speaker,field.name))
            elif field.name == "websafeKey":
                setattr(sf, field.name, speaker.key.urlsafe())
        sf.check_initialized()
        return sf

    def _createSpeakerObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.displayName:
            raise endpoints.BadRequestException("Speaker 'diplayName' field required")

        # copy SpeakerForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']

        # generate Speaker key
        sp_id = Speaker.allocate_ids(size=1)[0]
        sp_key = ndb.Key(Speaker, sp_id)
        data['key'] = sp_key

        # create Speaker
        sp = Speaker(**data)
        sp.put()

        return self._copySpeakerToForm(sp)

    @endpoints.method(SpeakerForm, SpeakerForm,
            path='speaker',
            http_method='POST', name='addSpeaker')
    def addSpeaker(self, request):
        """Create a new speaker.  Anyone can add a speaker, speaker does not need to be a user"""
        return self._createSpeakerObject(request)

# - - - Task 2: Add Sessions to User Wishlist - - - - - - - - - - - - - - - - - - - -

    #addSessionToWishlist(SessionKey) -- adds the session to the user's list of sessions they are interested in attending
    def _sessionAddIt(self, request):
        """Add a session to the user Profile session wish list."""
        prof = self._getProfileFromUser() # get user Profile

        # get session;
        wssk = request.websafeSessionKey
        s_key = ndb.Key(urlsafe=wssk)
        session = s_key.get()

        # check that session is a Session key and it exists
        self._checkKey(s_key, wssk, 'Session')

        # check if user already added session otherwise add
        if wssk in prof.sessionKeysWishList:
            raise ConflictException(
                "This session is already in your wishlist")

        # add the session to the users session wish list
        prof.sessionKeysWishList.append(wssk)

        # write Profile back to the datastore & return
        prof.put()
        return BooleanMessage(data=True)

    @endpoints.method(SESSION_WISH_REQUEST, BooleanMessage,
            path='sessions/wishList/{websafeSessionKey}',
            http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Register user for selected conference."""
        return self._sessionAddIt(request)

    #getSessionsInWishlist() -- query for all the sessions in a conference that the user is interested in
    @endpoints.method(message_types.VoidMessage, SessionForms,
            path='sessions/wishList',
            http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Get list of sesions that user wishes to attend."""
        prof = self._getProfileFromUser() # get user Profile
        session_keys = [ndb.Key(urlsafe=wssk) for wssk in prof.sessionKeysWishList]
        sessions = ndb.get_multi(session_keys)

        # get speakers
        speakerKeys = [ndb.Key(urlsafe=session.speakerKey)  for session in sessions]
        speakers = ndb.get_multi(speakerKeys)

        # put display names in a dict for easier fetching
        names = {}
        for speaker in speakers:
            names[speaker.key.id()] = speaker.displayName

        # return set of SessionForm objects per Session
        return SessionForms(items=[self._copySessionToForm(session, names[speaker.key.id()])\
         for session in sessions]
        )

# - - - Task 3: Come up with 2 additional queries - - - - - - - - - - - - - - - - - - - - -
    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/incomplete',
            http_method='GET', name='getIncompleteConferences')
    def getIncompleteConferences(self, request):
        """Get list of all conferences that need additional information"""
        q = Conference.query(ndb.OR(
                Conference.description==None,
                Conference.startDate==None,
                Conference.endDate==None))
        items = [self._copyConferenceToForm(conf, getattr(conf.key.parent().get(), 'displayName')) for conf in q]

        return ConferenceForms(items=items)

    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='conference/{websafeConferenceKey}/incompleteSessions',
            http_method='GET', name='getIncompleteConferenceSessions')
    def getIncompleteConferenceSessions(self, request):
        """Get list of all sessions for a conference that have incomplete information."""

        conf = self._ndbKey(urlsafe=request.websafeConferenceKey)

        # check that conf is a conference key and it exists
        self._checkKey(conf, request.websafeConferenceKey, 'Conference')

        sessions = Session.query(ndb.OR(
                Session.highlights=='To be announced',
                Session.speakerKey==None,
                Session.typeOfSession=='TBA'), ancestor=conf)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    @endpoints.method(message_types.VoidMessage, SpeakerForms,
            path='speakers',
            http_method='GET', name='getSpeakers')
    def getSpeakers(self, request):
        """Get list of all speakers"""
        speakers = Speaker.query()
        return SpeakerForms(items=[self._copySpeakerToForm(speaker) for speaker in speakers])

# - - - Task 3: Work on indexes and queries - - - - - - - - - - - - - - - - - - - - -

    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='conference/{websafeConferenceKey}/NotWorkshopSessionsBefore7pm',
            http_method='GET', name='getNotWorkshopSessionsBefore7pm')
    def getNotWorkshopSessionsBefore7pm(self, request):
        """Returns all conference non-workshop sessions before 7pm."""

        conf = self._ndbKey(urlsafe=request.websafeConferenceKey)

        # check that conf is a conference key and it exists
        self._checkKey(conf, request.websafeConferenceKey, 'Conference')

        sessions = Session.query(ndb.AND(
                Session.typeOfSession!='WORKSHOP',
                Session.typeOfSession!='TBA'), ancestor=conf)

        #Fix for BadRequestError: Only one inequality filter per query is supported. Encountered both typeOfSession and startDateTime
        items = []
        for session in sessions:
            if session.startDateTime and \
            session.startDateTime.hour + session.startDateTime.minute/60.0 <= 19:
                items += [self._copySessionToForm(session)]
        return SessionForms(items=items)

# - - - Task 4: Featured Speaker get handler - - - - - - - - - - - - - - - - - - - -

    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='featuredSpeaker',
            http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY) or "")



api = endpoints.api_server([ConferenceApi]) # register API
