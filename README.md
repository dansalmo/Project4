App Engine application for the Udacity training course.

###Improvements since previous submission:

1. Added back MEMCACHE_FEATURED_SPEAKER_KEY to conference.py and imported it in main.py
1. Renamed DEFAULTS into CONFERENCE_DEFAULTS
1. Added _checkKey for conference key in all places conference key is used
1. Changed naming from conf to c_key for keys
1. I did not combine _ndbKey and _checkKey since _ndbKey is a workaround that can be
removed if the NDB issue 143 is fixed, but _checkKey will always be needed.

### Task 1 - Description of Sessions and Speakers implementation

Sessions are implemented as child entity of Conference since they are always
part of a specific conference.  The session model uses one combined DateTime
field but the SessionForm allows for input and display of data and time separately.
I chose to use an Enum type for the type of session, similar to T-Shirt size, since
the types should be limited to a set of pre-defined choices, TBA being the default.
A Session can be created with only a ConferenceKey and a name, other fields will be 
defaulted to "To be announced" or another appropriate default.

I ran into and had to work around this [NDB issue][7]

I chose to create a separate Speaker entity that is not tied to Profile
because speakers might not be users or attendees nor should they have to be.
A speaker may be an attendee, in which case the ProfileKey can be added.
Since the speaker name is important to see from a session, I have added the
speaker name to the session.

I also had to implement a means of creating  and getting Speaker entities since
they were needed for testing and App engine does not allow entity creation at the
console.

### Task 3 - Description of additional queries

Purpose of 2 new queries:  Since my code allows for incomplete information in the
creation of a conferenece or session, I added queries to find all incomplete conferences
or sessions of a conference so that the queries could be used to easily find items that
need to be completed.  I also added a 3rd query that gets all speakers. 

### Task 3 - Description of problem and solution proposal for provided query
The problem for implementing the query for those that don't like workshops and don't like
sessions after 7 pm was the need for more than one inequality on different fields.
The error was:

`BadRequestError: Only one inequality filter per query is supported. Encountered both typeOfSession and startDateTime`

I worked around this by doing the query without the time constraint, then iterating
over the query results to remove times > 7pm.

### Task 4 - Featured Speaker email task

Added a task to check if a speaker is speaking in more than one conference and if so,
add a featured speaker entry to the memcache.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
[7]: https://code.google.com/p/appengine-ndb-experiment/issues/detail?id=143
[8]: http://www.restapitutorial.com/lessons/restfulresourcenaming.html
