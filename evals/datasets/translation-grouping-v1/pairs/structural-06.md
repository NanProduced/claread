# Blind grouping comparison — structural-06

You will judge two alternative partitions of the same article into
translation groups. The article's sentences are numbered below.
Read the article first, then evaluate both groupings against the rubric.

## Article sentences

[1] ===================
How to use sessions
===================

[2] .. module:: django.contrib.sessions
   :synopsis: Provides session management for Django projects.

[3] Django provides full support for anonymous sessions.

[4] The session framework
lets you store and retrieve arbitrary data on a per-site-visitor basis.

[5] It
stores data on the server side and abstracts the sending and receiving of
cookies.

[6] Cookies contain a session ID -- not the data itself (unless you're
using the :ref:`cookie based backend<cookie-session-backend>`).

[7] Enabling sessions
=================

[8] Sessions are implemented via a piece of :doc:`middleware </ref/middleware>`.

[9] To enable session functionality, do the following:

[10] * Edit the :setting:`MIDDLEWARE` setting and make sure it contains
  ``'django.contrib.sessions.middleware.

[11] SessionMiddleware'``.

[12] The default
  ``settings.py`` created by ``django-admin startproject`` has
  ``SessionMiddleware`` activated.

[13] If you don't want to use sessions, you might as well remove the
``SessionMiddleware`` line from :setting:`MIDDLEWARE` and
``'django.contrib.sessions'`` from your :setting:`INSTALLED_APPS`.

[14] It'll save you a small bit of overhead.

[15] .. _configuring-sessions:

[16] Configuring the session engine
==============================

[17] By default, Django stores sessions in your database (using the model
``django.contrib.sessions.models.

[18] Session``).

[19] Though this is convenient, in
some setups it's faster to store session data elsewhere, so Django can be
configured to store session data on your filesystem or in your cache.

[20] Using database-backed sessions
--

[21] --

[22] --

[23] --

[24] --

[25] --

[26] --

[27] --

[28] --

[29] --

[30] --

[31] --

[32] --

[33] --

[34] --

[35] If you want to use a database-backed session, you need to add
``'django.contrib.sessions'`` to your :setting:`INSTALLED_APPS` setting.

[36] Once you have configured your installation, run ``manage.py migrate``
to install the single database table that stores session data.

[37] .. _cached-sessions-backend:

[38] Using cached sessions
--

[39] --

[40] --

[41] --

[42] --

[43] --

[44] --

[45] --

[46] --

[47] --

[48] -

[49] For better performance, you may want to use a cache-based session backend.

[50] To store session data using Django's cache system, you'll first need to make
sure you've configured your cache; see the :doc:`cache documentation
</topics/cache>` for details.

[51] .. warning:

[52] :

[53] You should only use cache-based sessions if you're using the Memcached or
    Redis cache backend.

[54] The local-memory cache backend doesn't retain data
    long enough to be a good choice, and it'll be faster to use file or
    database sessions directly instead of sending everything through the file
    or database cache backends.

[55] Additionally, the local-memory cache backend is
    NOT multi-process safe, therefore probably not a good choice for production
    environments.

[56] If you have multiple caches defined in :setting:`CACHES`, Django will use the
default cache.

[57] To use another cache, set :setting:`SESSION_CACHE_ALIAS` to the
name of that cache.

[58] Once your cache is configured, you have to choose between a database-backed
cache or a non-persistent cache.

[59] The cached database backend (``cached_db``) uses a write-through cache --
session writes are applied to both the database and cache, in that order.

[60] If
writing to the cache (or deleting from it) fails, the exception is handled and
logged via the :ref:`sessions logger <django-contrib-sessions-logger>`, to
avoid failing an otherwise successful write or delete operation.

[61] Session reads use the cache, or the database if the data has been evicted from
the cache.

[62] To use this backend, set :setting:`SESSION_ENGINE` to
``"django.contrib.sessions.backends.cached_db"``, and follow the configuration
instructions for the `using database-backed sessions`_.

[63] The cache backend (``cache``) stores session data only in your cache.

[64] This is
faster because it avoids database persistence, but you will have to consider
what happens when cache data is evicted.

[65] Eviction can occur if the cache fills
up or the cache server is restarted, and it will mean session data is lost,
including logging out users.

[66] To use this backend, set :setting:`SESSION_ENGINE`
to ``"django.contrib.sessions.backends.cache"``.

[67] The cache backend can be made persistent by using a persistent cache, such as
Redis with appropriate configuration.

[68] But unless your cache is definitely
configured for sufficient persistence, opt for the cached database backend.

[69] This avoids edge cases caused by unreliable data storage in production.

[70] .. versionchanged:

[71] :

[72] 6.2

[73] In earlier versions, only failed writes were caught and logged rather than
  failed deletes.

[74] Using file-based sessions
--

[75] --

[76] --

[77] --

[78] --

[79] --

[80] --

[81] --

[82] --

[83] --

[84] --

[85] --

[86] -

[87] To use file-based sessions, set the :setting:`SESSION_ENGINE` setting to
``"django.contrib.sessions.backends.file"``.

[88] You might also want to set the :setting:`SESSION_FILE_PATH` setting (which
defaults to output from ``tempfile.gettempdir()``, most likely ``/tmp``) to
control where Django stores session files.

[89] Be sure to check that your web
server has permissions to read and write to this location.

[90] .. _cookie-session-backend:

[91] Using cookie-based sessions
--

[92] --

[93] --

[94] --

[95] --

[96] --

[97] --

[98] --

[99] --

[100] --

[101] --

[102] --

[103] --

[104] -

[105] To use cookies-based sessions, set the :setting:`SESSION_ENGINE` setting to
``"django.contrib.sessions.backends.signed_cookies"``.

[106] The session data will be
stored using Django's tools for :doc:`cryptographic signing </topics/signing>`
and the :setting:`SECRET_KEY` setting.

[107] .. note:

[108] :

[109] It's recommended to leave the :setting:`SESSION_COOKIE_HTTPONLY` setting
    on ``True`` to prevent access to the stored data from JavaScript.

[110] .. warning:

[111] :

[112] **The session data is signed but not encrypted**

[113] When using the cookies backend the session data can be read by the client.

[114] A MAC (Message Authentication Code) is used to protect the data against
    changes by the client, so that the session data will be invalidated when
    being tampered with.

[115] The same invalidation happens if the client storing
    the cookie (e.g. your user's browser) can't store all of the session cookie
    and drops data.

[116] Even though Django compresses the data, it's still entirely
    possible to exceed the :rfc:`common limit of 4096 bytes <2965#section-5.3>`
    per cookie.

[117] **No freshness guarantee**

[118] Note also that while the MAC can guarantee the authenticity of the data
    (that it was generated by your site, and not someone else), and the
    integrity of the data (that it is all there and correct), it cannot
    guarantee freshness i.e. that you are being sent back the last thing you
    sent to the client.

[119] This means that for some uses of session data, the
    cookie backend might open you up to `replay attacks`_.

[120] Unlike other session
    backends which keep a server-side record of each session and invalidate it
    when a user logs out, cookie-based sessions are not invalidated when a user
    logs out.

[121] Thus if an attacker steals a user's cookie, they can use that
    cookie to login as that user even if the user logs out.

[122] Cookies will only
    be detected as 'stale' if they are older than your
    :setting:`SESSION_COOKIE_AGE`.

[123] **Performance**

[124] Finally, the size of a cookie can have an impact on the speed of your site.

[125] .. _`replay attacks`:

[126] https:

[127] //en.wikipedia.org/wiki/Replay_attack

## Grouping X

  G1: sentences 1 (1 sentence)
  G2: sentences 2 (1 sentence)
  G3: sentences 3–5 (3 sentences)
  G4: sentences 6 (1 sentence)
  G5: sentences 7 (1 sentence)
  G6: sentences 8 (1 sentence)
  G7: sentences 9 (1 sentence)
  G8: sentences 10–12 (3 sentences)
  G9: sentences 13–14 (2 sentences)
  G10: sentences 15 (1 sentence)
  G11: sentences 16 (1 sentence)
  G12: sentences 17–19 (3 sentences)
  G13: sentences 20–22 (3 sentences)
  G14: sentences 23–25 (3 sentences)
  G15: sentences 26–28 (3 sentences)
  G16: sentences 29–31 (3 sentences)
  G17: sentences 32–34 (3 sentences)
  G18: sentences 35 (1 sentence)
  G19: sentences 36 (1 sentence)
  G20: sentences 37 (1 sentence)
  G21: sentences 38–40 (3 sentences)
  G22: sentences 41–43 (3 sentences)
  G23: sentences 44–46 (3 sentences)
  G24: sentences 47–48 (2 sentences)
  G25: sentences 49 (1 sentence)
  G26: sentences 50 (1 sentence)
  G27: sentences 51–52 (2 sentences)
  G28: sentences 53–55 (3 sentences)
  G29: sentences 56–57 (2 sentences)
  G30: sentences 58 (1 sentence)
  G31: sentences 59–60 (2 sentences)
  G32: sentences 61–62 (2 sentences)
  G33: sentences 63–65 (3 sentences)
  G34: sentences 66 (1 sentence)
  G35: sentences 67–69 (3 sentences)
  G36: sentences 70–72 (3 sentences)
  G37: sentences 73 (1 sentence)
  G38: sentences 74–76 (3 sentences)
  G39: sentences 77–79 (3 sentences)
  G40: sentences 80–82 (3 sentences)
  G41: sentences 83–85 (3 sentences)
  G42: sentences 86 (1 sentence)
  G43: sentences 87 (1 sentence)
  G44: sentences 88–89 (2 sentences)
  G45: sentences 90 (1 sentence)
  G46: sentences 91–93 (3 sentences)
  G47: sentences 94–96 (3 sentences)
  G48: sentences 97–99 (3 sentences)
  G49: sentences 100–102 (3 sentences)
  G50: sentences 103–104 (2 sentences)
  G51: sentences 105–106 (2 sentences)
  G52: sentences 107–108 (2 sentences)
  G53: sentences 109 (1 sentence)
  G54: sentences 110–111 (2 sentences)
  G55: sentences 112 (1 sentence)
  G56: sentences 113 (1 sentence)
  G57: sentences 114–116 (3 sentences)
  G58: sentences 117 (1 sentence)
  G59: sentences 118–120 (3 sentences)
  G60: sentences 121–122 (2 sentences)
  G61: sentences 123 (1 sentence)
  G62: sentences 124 (1 sentence)
  G63: sentences 125–127 (3 sentences)

## Grouping Y

  G1: sentences 1 (1 sentence)
  G2: sentences 2 (1 sentence)
  G3: sentences 3–6 (4 sentences)
  G4: sentences 7 (1 sentence)
  G5: sentences 8–9 (2 sentences)
  G6: sentences 10–12 (3 sentences)
  G7: sentences 13–14 (2 sentences)
  G8: sentences 15–16 (2 sentences)
  G9: sentences 17–19 (3 sentences)
  G10: sentences 20–34 (15 sentences)
  G11: sentences 35–36 (2 sentences)
  G12: sentences 37–48 (12 sentences)
  G13: sentences 49–50 (2 sentences)
  G14: sentences 51–55 (5 sentences)
  G15: sentences 56–57 (2 sentences)
  G16: sentences 58 (1 sentence)
  G17: sentences 59–62 (4 sentences)
  G18: sentences 63–66 (4 sentences)
  G19: sentences 67–69 (3 sentences)
  G20: sentences 70–73 (4 sentences)
  G21: sentences 74–86 (13 sentences)
  G22: sentences 87–89 (3 sentences)
  G23: sentences 90–104 (15 sentences)
  G24: sentences 105–106 (2 sentences)
  G25: sentences 107–109 (3 sentences)
  G26: sentences 110–112 (3 sentences)
  G27: sentences 113–116 (4 sentences)
  G28: sentences 117 (1 sentence)
  G29: sentences 118–120 (3 sentences)
  G30: sentences 121–122 (2 sentences)
  G31: sentences 123 (1 sentence)
  G32: sentences 124 (1 sentence)
  G33: sentences 125–127 (3 sentences)
