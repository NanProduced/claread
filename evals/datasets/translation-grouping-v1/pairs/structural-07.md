# Blind grouping comparison — structural-07

You will judge two alternative partitions of the same article into
translation groups. The article's sentences are numbered below.
Read the article first, then evaluate both groupings against the rubric.

## Article sentences

[1] Retrieving objects
==================

[2] To retrieve objects from your database, construct a
:class:`~django.db.models.query.

[3] QuerySet` via a
:class:`~django.db.models.

[4] Manager` on your model class.

[5] A :class:`~django.db.models.query.

[6] QuerySet` represents a collection of objects
from your database.

[7] It can have zero, one or many *filters*.

[8] Filters narrow
down the query results based on the given parameters.

[9] In SQL terms, a
:class:`~django.db.models.query.

[10] QuerySet` equates to a ``SELECT`` statement,
and a filter is a limiting clause such as ``WHERE`` or ``LIMIT``.

[11] You get a :class:`~django.db.models.query.

[12] QuerySet` by using your model's
:class:`~django.db.models.

[13] Manager`.

[14] Each model has at least one
:class:`~django.db.models.

[15] Manager`, and it's called
:attr:`~django.db.models.

[16] Model.objects` by default.

[17] Access it directly via the
model class, like so:

[18] .. code-block:

[19] :

[20] pycon

[21] >>> Blog.objects
    <django.db.models.manager.

[22] Manager object at ...>
    >>> b = Blog(name="Foo", tagline="Bar")
    >>> b.objects
    Traceback:
        ...

[23] AttributeError: "Manager isn't accessible via Blog instances."

[24] .. note:

[25] :

[26] A ``Manager`` is accessible only via model classes, rather than from model
    instances, to enforce a separation between "table-level" operations and
    "record-level" operations.

[27] The :class:`~django.db.models.

[28] Manager` is the main source of querysets for
a model.

[29] For example, ``Blog.objects.all()`` returns a
:class:`~django.db.models.query.

[30] QuerySet` that contains all ``Blog`` objects in
the database.

[31] Retrieving all objects
--

[32] --

[33] --

[34] --

[35] --

[36] --

[37] --

[38] --

[39] --

[40] --

[41] --

[42] The simplest way to retrieve objects from a table is to get all of them.

[43] To do
this, use the :meth:`~django.db.models.query.

[44] QuerySet.all` method on a
:class:`~django.db.models.

[45] Manager`:

[46] .. code-block:

[47] :

[48] pycon

[49] >>> all_entries = Entry.objects.all()

[50] The :meth:`~django.db.models.query.

[51] QuerySet.all` method returns a
:class:`~django.db.models.query.

[52] QuerySet` of all the objects in the database.

[53] Retrieving specific objects with filters
--

[54] --

[55] --

[56] --

[57] --

[58] --

[59] --

[60] --

[61] --

[62] --

[63] --

[64] --

[65] --

[66] --

[67] --

[68] --

[69] --

[70] --

[71] --

[72] --

[73] The :class:`~django.db.models.query.

[74] QuerySet` returned by
:meth:`~django.db.models.query.

[75] QuerySet.all` describes all objects in the
database table.

[76] Usually, though, you'll need to select only a subset of the
complete set of objects.

[77] To create such a subset, you refine the initial
:class:`~django.db.models.query.

[78] QuerySet`, adding filter conditions.

[79] The two
most common ways to refine a :class:`~django.db.models.query.

[80] QuerySet` are:

[81] ``filter(**kwargs)``
    Returns a new :class:`~django.db.models.query.

[82] QuerySet` containing objects
    that match the given lookup parameters.

[83] ``exclude(**kwargs)``
    Returns a new :class:`~django.db.models.query.

[84] QuerySet` containing objects
    that do *not* match the given lookup parameters.

[85] The lookup parameters (``**kwargs`` in the above function definitions) should
be in the format described in `Field lookups`_ below.

[86] For example, to get a :class:`~django.db.models.query.

[87] QuerySet` of blog entries
from the year 2006, use :meth:`~django.db.models.query.

[88] QuerySet.filter` like
so::

[89] Entry.objects.filter(pub_date__year=2006)

[90] With the default manager class, it is the same as:

[91] :

[92] Entry.objects.all().filter(pub_date__year=2006)

[93] .. _chaining-filters:

[94] Chaining filters
~~~~~~~~~~~~~~~~

[95] The result of refining a :class:`~django.db.models.query.

[96] QuerySet` is itself a
:class:`~django.db.models.query.

[97] QuerySet`, so it's possible to chain
refinements together.

[98] For example:

[99] .. code-block:

[100] :

[101] pycon

[102] >>> Entry.objects.filter(headline__startswith="What").exclude(
    ...     pub_date__gte=datetime.date.today()
    ... ).filter(pub_date__gte=datetime.date(2005, 1, 30))

[103] This takes the initial :class:`~django.db.models.query.

[104] QuerySet` of all entries
in the database, adds a filter, then an exclusion, then another filter.

[105] The
final result is a :class:`~django.db.models.query.

[106] QuerySet` containing all
entries with a headline that starts with "What", that were published between
January 30, 2005, and the current day.

[107] .. _filtered-querysets-are-unique:

[108] Filtered ``QuerySet``\s are unique
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

[109] Each time you refine a :class:`~django.db.models.query.

[110] QuerySet`, you get a
brand-new :class:`~django.db.models.query.

[111] QuerySet` that is in no way bound to
the previous :class:`~django.db.models.query.

[112] QuerySet`.

[113] Each refinement creates
a separate and distinct :class:`~django.db.models.query.

[114] QuerySet` that can be
stored, used and reused.

[115] Example:

[116] .. code-block:

[117] :

[118] pycon

[119] >>> q1 = Entry.objects.filter(headline__startswith="What")
    >>> q2 = q1.exclude(pub_date__gte=datetime.date.today())
    >>> q3 = q1.filter(pub_date__gte=datetime.date.today())

[120] These three querysets are separate.

[121] The first is a base
:class:`~django.db.models.query.

[122] QuerySet` containing all entries that contain a
headline starting with "What".

[123] The second is a subset of the first, with an
additional criteria that excludes records whose ``pub_date`` is today or in the
future.

[124] The third is a subset of the first, with an additional criteria that
selects only the records whose ``pub_date`` is today or in the future.

[125] The
initial :class:`~django.db.models.query.

[126] QuerySet` (``q1``) is unaffected by the
refinement process.

[127] .. _querysets-are-lazy:

[128] ``QuerySet``\s are lazy
~~~~~~~~~~~~~~~~~~~~~~~

[129] ``QuerySet`` objects are lazy -- the act of creating a
:class:`~django.db.models.query.

[130] QuerySet` doesn't involve any database
activity.

[131] You can stack filters together all day long, and Django won't
actually run the query until the :class:`~django.db.models.query.

[132] QuerySet` is
*evaluated*.

[133] Take a look at this example:

[134] .. code-block:

[135] :

[136] pycon

[137] >>> q = Entry.objects.filter(headline__startswith="What")
    >>> q = q.filter(pub_date__lte=datetime.date.today())
    >>> q = q.exclude(body_text__icontains="food")
    >>> print(q)

[138] Though this looks like three database hits, in fact it hits the database only
once, at the last line (``print(q)``).

[139] In general, the results of a
:class:`~django.db.models.query.

[140] QuerySet` aren't fetched from the database
until you "ask" for them.

[141] When you do, the
:class:`~django.db.models.query.

[142] QuerySet` is *evaluated* by accessing the
database.

[143] For more details on exactly when evaluation takes place, see
:ref:`when-querysets-are-evaluated`.

[144] .. _retrieving-single-object-with-get:

[145] Retrieving a single object with ``get()``
--

[146] --

[147] --

[148] --

[149] --

[150] --

[151] --

[152] --

[153] --

[154] --

[155] --

[156] --

[157] --

[158] --

[159] --

[160] --

[161] --

[162] --

[163] --

[164] --

[165] -

[166] :meth:`~django.db.models.query.

[167] QuerySet.filter` will always give you a
:class:`~django.db.models.query.

[168] QuerySet`, even if only a single object matches
the query - in this case, it will be a
:class:`~django.db.models.query.

[169] QuerySet` containing a single element.

[170] If you know there is only one object that matches your query, you can use the
:meth:`~django.db.models.query.

[171] QuerySet.get` method on a
:class:`~django.db.models.

[172] Manager` which returns the object directly:

[173] .. code-block:

[174] :

[175] pycon

[176] >>> one_entry = Entry.objects.get(pk=1)

[177] You can use any query expression with
:meth:`~django.db.models.query.

[178] QuerySet.get`, just like with
:meth:`~django.db.models.query.

[179] QuerySet.filter` - again, see `Field lookups`_
below.

[180] Note that there is a difference between using
:meth:`~django.db.models.query.

[181] QuerySet.get`, and using
:meth:`~django.db.models.query.

[182] QuerySet.filter` with a slice of ``[0]``.

[183] If
there are no results that match the query,
:meth:`~django.db.models.query.

[184] QuerySet.get` will raise a ``DoesNotExist``
exception.

[185] This exception is an attribute of the model class that the query is
being performed on - so in the code above, if there is no ``Entry`` object with
a primary key of 1, Django will raise ``Entry.

[186] DoesNotExist``.

[187] Similarly, Django will complain if more than one item matches the
:meth:`~django.db.models.query.

[188] QuerySet.get` query.

[189] In this case, it will raise
:exc:`~django.core.exceptions.MultipleObjectsReturned`, which again is an
attribute of the model class itself.

[190] Other ``QuerySet`` methods
--

[191] --

[192] --

[193] --

[194] --

[195] --

[196] --

[197] --

[198] --

[199] --

[200] --

[201] --

[202] --

[203] Most of the time you'll use :meth:`~django.db.models.query.

[204] QuerySet.all`,
:meth:`~django.db.models.query.

[205] QuerySet.get`,
:meth:`~django.db.models.query.

[206] QuerySet.filter` and
:meth:`~django.db.models.query.

[207] QuerySet.exclude` when you need to look up
objects from the database.

[208] However, that's far from all there is; see the
:ref:`QuerySet API Reference <queryset-api>` for a complete list of all the
various :class:`~django.db.models.query.

[209] QuerySet` methods.

[210] .. _limiting-querysets:

[211] Limiting ``QuerySet``\s
--

[212] --

[213] --

[214] --

[215] --

[216] --

[217] --

[218] --

[219] --

[220] --

[221] --

[222] -

[223] Use a subset of Python's array-slicing syntax to limit your
:class:`~django.db.models.query.

[224] QuerySet` to a certain number of results.

[225] This
is the equivalent of SQL's ``LIMIT`` and ``OFFSET`` clauses.

[226] For example, this returns the first 5 objects (``LIMIT 5``):

[227] .. code-block:

[228] :

[229] pycon

[230] >>> Entry.objects.all()[:

[231] 5]

[232] This returns the sixth through tenth objects (``OFFSET 5 LIMIT 5``):

[233] .. code-block:

[234] :

[235] pycon

[236] >>> Entry.objects.all()[5:

[237] 10]

[238] Negative indexing (i.e. ``Entry.objects.all()[-1]``) is not supported.

[239] Generally, slicing a :class:`~django.db.models.query.

[240] QuerySet` returns a new
:class:`~django.db.models.query.

[241] QuerySet` -- it doesn't evaluate the query.

[242] An
exception is if you use the "step" parameter of Python slice syntax.

[243] For
example, this would actually execute the query in order to return a list of
every *second* object of the first 10:

[244] .. code-block:

[245] :

[246] pycon

[247] >>> Entry.objects.all()[:

[248] 10:

[249] 2]

[250] Further filtering or ordering of a sliced queryset is prohibited due to the
ambiguous nature of how that might work.

[251] To retrieve a *single* object rather than a list
(e.g. ``SELECT foo FROM bar LIMIT 1``), use an index instead of a slice.

[252] For
example, this returns the first ``Entry`` in the database, after ordering
entries alphabetically by headline:

[253] .. code-block:

[254] :

[255] pycon

[256] >>> Entry.objects.order_by("headline")[0]

[257] This is roughly equivalent to:

[258] .. code-block:

[259] :

[260] pycon

[261] >>> Entry.objects.order_by("headline")[0:

[262] 1].get()

[263] Note, however, that the first of these will raise ``IndexError`` while the
second will raise ``DoesNotExist`` if no objects match the given criteria.

[264] See
:meth:`~django.db.models.query.

[265] QuerySet.get` for more details.

## Grouping X

  G1: sentences 1 (1 sentence)
  G2: sentences 2–4 (3 sentences)
  G3: sentences 5–7 (3 sentences)
  G4: sentences 8–10 (3 sentences)
  G5: sentences 11–13 (3 sentences)
  G6: sentences 14–16 (3 sentences)
  G7: sentences 17 (1 sentence)
  G8: sentences 18–20 (3 sentences)
  G9: sentences 21–23 (3 sentences)
  G10: sentences 24–25 (2 sentences)
  G11: sentences 26 (1 sentence)
  G12: sentences 27–29 (3 sentences)
  G13: sentences 30 (1 sentence)
  G14: sentences 31–33 (3 sentences)
  G15: sentences 34–36 (3 sentences)
  G16: sentences 37–39 (3 sentences)
  G17: sentences 40–41 (2 sentences)
  G18: sentences 42–44 (3 sentences)
  G19: sentences 45 (1 sentence)
  G20: sentences 46–48 (3 sentences)
  G21: sentences 49 (1 sentence)
  G22: sentences 50–52 (3 sentences)
  G23: sentences 53–55 (3 sentences)
  G24: sentences 56–58 (3 sentences)
  G25: sentences 59–61 (3 sentences)
  G26: sentences 62–64 (3 sentences)
  G27: sentences 65–67 (3 sentences)
  G28: sentences 68–70 (3 sentences)
  G29: sentences 71–72 (2 sentences)
  G30: sentences 73–75 (3 sentences)
  G31: sentences 76 (1 sentence)
  G32: sentences 77–79 (3 sentences)
  G33: sentences 80 (1 sentence)
  G34: sentences 81–82 (2 sentences)
  G35: sentences 83–84 (2 sentences)
  G36: sentences 85 (1 sentence)
  G37: sentences 86–88 (3 sentences)
  G38: sentences 89 (1 sentence)
  G39: sentences 90–91 (2 sentences)
  G40: sentences 92 (1 sentence)
  G41: sentences 93 (1 sentence)
  G42: sentences 94 (1 sentence)
  G43: sentences 95–97 (3 sentences)
  G44: sentences 98 (1 sentence)
  G45: sentences 99–101 (3 sentences)
  G46: sentences 102 (1 sentence)
  G47: sentences 103–105 (3 sentences)
  G48: sentences 106 (1 sentence)
  G49: sentences 107 (1 sentence)
  G50: sentences 108 (1 sentence)
  G51: sentences 109–111 (3 sentences)
  G52: sentences 112–114 (3 sentences)
  G53: sentences 115 (1 sentence)
  G54: sentences 116–118 (3 sentences)
  G55: sentences 119 (1 sentence)
  G56: sentences 120–122 (3 sentences)
  G57: sentences 123–125 (3 sentences)
  G58: sentences 126 (1 sentence)
  G59: sentences 127 (1 sentence)
  G60: sentences 128 (1 sentence)
  G61: sentences 129–131 (3 sentences)
  G62: sentences 132–133 (2 sentences)
  G63: sentences 134–136 (3 sentences)
  G64: sentences 137 (1 sentence)
  G65: sentences 138–140 (3 sentences)
  G66: sentences 141–143 (3 sentences)
  G67: sentences 144 (1 sentence)
  G68: sentences 145–147 (3 sentences)
  G69: sentences 148–150 (3 sentences)
  G70: sentences 151–153 (3 sentences)
  G71: sentences 154–156 (3 sentences)
  G72: sentences 157–159 (3 sentences)
  G73: sentences 160–162 (3 sentences)
  G74: sentences 163–165 (3 sentences)
  G75: sentences 166–168 (3 sentences)
  G76: sentences 169 (1 sentence)
  G77: sentences 170–172 (3 sentences)
  G78: sentences 173–175 (3 sentences)
  G79: sentences 176 (1 sentence)
  G80: sentences 177–179 (3 sentences)
  G81: sentences 180–182 (3 sentences)
  G82: sentences 183–185 (3 sentences)
  G83: sentences 186 (1 sentence)
  G84: sentences 187–189 (3 sentences)
  G85: sentences 190–192 (3 sentences)
  G86: sentences 193–195 (3 sentences)
  G87: sentences 196–198 (3 sentences)
  G88: sentences 199–201 (3 sentences)
  G89: sentences 202 (1 sentence)
  G90: sentences 203–205 (3 sentences)
  G91: sentences 206–208 (3 sentences)
  G92: sentences 209 (1 sentence)
  G93: sentences 210 (1 sentence)
  G94: sentences 211–213 (3 sentences)
  G95: sentences 214–216 (3 sentences)
  G96: sentences 217–219 (3 sentences)
  G97: sentences 220–222 (3 sentences)
  G98: sentences 223–225 (3 sentences)
  G99: sentences 226 (1 sentence)
  G100: sentences 227–229 (3 sentences)
  G101: sentences 230–231 (2 sentences)
  G102: sentences 232 (1 sentence)
  G103: sentences 233–235 (3 sentences)
  G104: sentences 236–237 (2 sentences)
  G105: sentences 238 (1 sentence)
  G106: sentences 239–241 (3 sentences)
  G107: sentences 242–243 (2 sentences)
  G108: sentences 244–246 (3 sentences)
  G109: sentences 247–249 (3 sentences)
  G110: sentences 250 (1 sentence)
  G111: sentences 251–252 (2 sentences)
  G112: sentences 253–255 (3 sentences)
  G113: sentences 256 (1 sentence)
  G114: sentences 257 (1 sentence)
  G115: sentences 258–260 (3 sentences)
  G116: sentences 261–262 (2 sentences)
  G117: sentences 263–265 (3 sentences)

## Grouping Y

  G1: sentences 1 (1 sentence)
  G2: sentences 2–4 (3 sentences)
  G3: sentences 5–8 (4 sentences)
  G4: sentences 9–10 (2 sentences)
  G5: sentences 11–13 (3 sentences)
  G6: sentences 14–16 (3 sentences)
  G7: sentences 17 (1 sentence)
  G8: sentences 18–23 (6 sentences)
  G9: sentences 24–26 (3 sentences)
  G10: sentences 27–30 (4 sentences)
  G11: sentences 31–41 (11 sentences)
  G12: sentences 42–45 (4 sentences)
  G13: sentences 46–49 (4 sentences)
  G14: sentences 50–52 (3 sentences)
  G15: sentences 53–72 (20 sentences)
  G16: sentences 73–75 (3 sentences)
  G17: sentences 76–78 (3 sentences)
  G18: sentences 79–80 (2 sentences)
  G19: sentences 81–82 (2 sentences)
  G20: sentences 83–84 (2 sentences)
  G21: sentences 85 (1 sentence)
  G22: sentences 86–88 (3 sentences)
  G23: sentences 89 (1 sentence)
  G24: sentences 90–91 (2 sentences)
  G25: sentences 92 (1 sentence)
  G26: sentences 93–94 (2 sentences)
  G27: sentences 95–98 (4 sentences)
  G28: sentences 99–102 (4 sentences)
  G29: sentences 103–106 (4 sentences)
  G30: sentences 107–108 (2 sentences)
  G31: sentences 109–114 (6 sentences)
  G32: sentences 115 (1 sentence)
  G33: sentences 116–119 (4 sentences)
  G34: sentences 120–124 (5 sentences)
  G35: sentences 125–126 (2 sentences)
  G36: sentences 127–128 (2 sentences)
  G37: sentences 129–132 (4 sentences)
  G38: sentences 133 (1 sentence)
  G39: sentences 134–137 (4 sentences)
  G40: sentences 138–142 (5 sentences)
  G41: sentences 143 (1 sentence)
  G42: sentences 144–165 (22 sentences)
  G43: sentences 166–172 (7 sentences)
  G44: sentences 173–176 (4 sentences)
  G45: sentences 177–179 (3 sentences)
  G46: sentences 180–182 (3 sentences)
  G47: sentences 183–186 (4 sentences)
  G48: sentences 187–189 (3 sentences)
  G49: sentences 190–202 (13 sentences)
  G50: sentences 203–209 (7 sentences)
  G51: sentences 210–222 (13 sentences)
  G52: sentences 223–225 (3 sentences)
  G53: sentences 226 (1 sentence)
  G54: sentences 227–231 (5 sentences)
  G55: sentences 232 (1 sentence)
  G56: sentences 233–237 (5 sentences)
  G57: sentences 238 (1 sentence)
  G58: sentences 239–242 (4 sentences)
  G59: sentences 243 (1 sentence)
  G60: sentences 244–249 (6 sentences)
  G61: sentences 250–251 (2 sentences)
  G62: sentences 252 (1 sentence)
  G63: sentences 253–256 (4 sentences)
  G64: sentences 257 (1 sentence)
  G65: sentences 258–262 (5 sentences)
  G66: sentences 263–265 (3 sentences)
