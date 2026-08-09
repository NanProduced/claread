# Blind grouping comparison — structural-05

You will judge two alternative partitions of the same article into
translation groups. The article's sentences are numbered below.
Read the article first, then evaluate both groupings against the rubric.

## Article sentences

[1] .. _tut-structures:

[2] ***************
Data Structures
***************

[3] This chapter describes some things you've learned about already in more detail,
and adds some new things as well.

[4] .. _tut-morelists:

[5] More on Lists
=============

[6] The :ref:`list <typesseq-list>` data type has some more methods.

[7] Here are all
of the methods of list objects:

[8] .. method:

[9] :

[10] list.append(value, /)
   :

[11] noindex:

[12] Add an item to the end of the list.

[13] Similar to ``a[len(a):]

[14] = [x]``.

[15] .. method:

[16] :

[17] list.extend(iterable, /)
   :

[18] noindex:

[19] Extend the list by appending all the items from the iterable.

[20] Similar to
   ``a[len(a):]

[21] = iterable``.

[22] .. method:

[23] :

[24] list.insert(index, value, /)
   :

[25] noindex:

[26] Insert an item at a given position.

[27] The first argument is the index of the
   element before which to insert, so ``a.insert(0, x)`` inserts at the front of
   the list, and ``a.insert(len(a), x)`` is equivalent to ``a.append(x)``.

[28] .. method:

[29] :

[30] list.remove(value, /)
   :

[31] noindex:

[32] Remove the first item from the list whose value is equal to *value*.

[33] It raises a
   :exc:`ValueError` if there is no such item.

[34] .. method:

[35] :

[36] list.pop(index=-1, /)
   :

[37] noindex:

[38] Remove the item at the given position in the list, and return it.

[39] If no index
   is specified, ``a.pop()`` removes and returns the last item in the list.

[40] It raises an :exc:`IndexError` if the list is empty or the index is
   outside the list range.

[41] .. method:

[42] :

[43] list.clear()
   :

[44] noindex:

[45] Remove all items from the list.

[46] Similar to ``del a[:]``.

[47] .. method:

[48] :

[49] list.index(value[, start[, stop]])
   :

[50] noindex:

[51] Return zero-based index of the first occurrence of *value* in the list.

[52] Raises a :exc:`ValueError` if there is no such item.

[53] The optional arguments *start* and *end* are interpreted as in the slice
   notation and are used to limit the search to a particular subsequence of
   the list.

[54] The returned index is computed relative to the beginning of the full
   sequence rather than the *start* argument.

[55] .. method:

[56] :

[57] list.count(value, /)
   :

[58] noindex:

[59] Return the number of times *value* appears in the list.

[60] .. method:

[61] :

[62] list.sort(*, key=None, reverse=False)
   :

[63] noindex:

[64] Sort the items of the list in place (the arguments can be used for sort
   customization, see :func:`sorted` for their explanation).

[65] .. method:

[66] :

[67] list.reverse()
   :

[68] noindex:

[69] Reverse the elements of the list in place.

[70] .. method:

[71] :

[72] list.copy()
   :

[73] noindex:

[74] Return a shallow copy of the list.

[75] Similar to ``a[:]``.

[76] An example that uses most of the list methods:

[77] :

[78] >>> fruits = ['orange', 'apple', 'pear', 'banana', 'kiwi', 'apple', 'banana']
    >>> fruits.count('apple')
    2
    >>> fruits.count('tangerine')
    0
    >>> fruits.index('banana')
    3
    >>> fruits.index('banana', 4)  # Find

[79] next banana starting at position 4
    6
    >>> fruits.reverse()
    >>> fruits
    ['banana', 'apple', 'kiwi', 'banana', 'pear', 'apple', 'orange']
    >>> fruits.append('grape')
    >>> fruits
    ['banana', 'apple',

[80] 'kiwi', 'banana', 'pear', 'apple', 'orange', 'grape']
    >>> fruits.sort()
    >>> fruits
    ['apple', 'apple', 'banana', 'banana', 'grape', 'kiwi', 'orange', 'pear']
    >>> fruits.pop()
    'pear'

[81] You might have noticed that methods like ``insert``, ``remove`` or ``sort`` that
only modify the list have no return value printed -- they return the default
``None``.

[82] [#]_

[83] This is a design principle for all mutable data structures in
Python.

[84] Another thing you might notice is that not all data can be sorted or
compared.

[85] For instance, ``[None, 'hello', 10]`` doesn't sort because
integers can't be compared to strings and ``None`` can't be compared to
other types.

[86] Also, there are some types that don't have a defined
ordering relation.

[87] For example, ``3+4j < 5+7j`` isn't a valid
comparison.

[88] .. _tut-lists-as-stacks:

[89] Using Lists as Stacks
--

[90] --

[91] --

[92] --

[93] --

[94] --

[95] --

[96] --

[97] --

[98] --

[99] -

[100] The list methods make it very easy to use a list as a stack, where the last
element added is the first element retrieved ("last-in, first-out").

[101] To add an
item to the top of the stack, use :meth:`~list.append`.

[102] To retrieve an item from the
top of the stack, use :meth:`~list.pop` without an explicit index.

[103] For example::

[104] >>> stack = [3, 4, 5]
   >>> stack.append(6)
   >>> stack.append(7)
   >>> stack
   [3, 4, 5, 6, 7]
   >>> stack.pop()
   7
   >>> stack
   [3, 4,

[105] 5, 6]
   >>> stack.pop()
   6
   >>> stack.pop()
   5
   >>> stack
   [3, 4]

[106] .. _tut-lists-as-queues:

[107] Using Lists as Queues
--

[108] --

[109] --

[110] --

[111] --

[112] --

[113] --

[114] --

[115] --

[116] --

[117] -

[118] It is also possible to use a list as a queue, where the first element added is
the first element retrieved ("first-in, first-out"); however, lists are not
efficient for this purpose.

[119] While appends and pops from the end of list are
fast, doing inserts or pops from the beginning of a list is slow (because all
of the other elements have to be shifted by one).

[120] To implement a queue, use :class:`collections.deque` which was designed to
have fast appends and pops from both ends.

[121] For example::

[122] >>> from collections import deque
   >>> queue = deque(["Eric", "John", "Michael"])
   >>> queue.append("Terry")           # Terry arrives
   >>> queue.append("Graham")          # Graham arrives
   >>> queue.popleft()                 #

[123] The first to arrive now leaves
   'Eric'
   >>> queue.popleft()                 # The second to arrive now leaves
   'John'
   >>> queue                           # Remaining queue in order

[124] of arrival
   deque(['Michael', 'Terry', 'Graham'])

[125] .. _tut-listcomps:

[126] List Comprehensions
--

[127] --

[128] --

[129] --

[130] --

[131] --

[132] --

[133] --

[134] --

[135] -

[136] List comprehensions provide a concise way to create lists.

[137] Common applications are to make new lists where each element is the result of
some operations applied to each member of another sequence or iterable, or to
create a subsequence of those elements that satisfy a certain condition.

[138] For example, assume we want to create a list of squares, like:

[139] :

[140] >>> squares = []
   >>> for x in range(10):

[141] ...     squares.append(x**2)
   ...
   >>> squares
   [0, 1, 4, 9, 16, 25, 36, 49, 64, 81]

[142] Note that this creates (or overwrites) a variable named ``x`` that still exists
after the loop completes.

[143] We can calculate the list of squares without any
side effects using::

[144] squares = list(map(lambda x:

[145] x**2, range(10)))

[146] or, equivalently:

[147] :

[148] squares = [x**2 for x in range(10)]

[149] which is more concise and readable.

[150] A list comprehension consists of brackets containing an expression followed
by a :keyword:`!for` clause, then zero or more :keyword:`!for` or :keyword:`!if`
clauses.

[151] The result will be a new list resulting from evaluating the expression
in the context of the :keyword:`!for` and :keyword:`!if` clauses which follow it.

[152] For example, this listcomp combines the elements of two lists if they are not
equal::

[153] >>> [(x, y) for x in [1,2,3] for y in [3,1,4] if x != y]
   [(1, 3), (1, 4), (2, 3), (2, 1), (2,

[154] 4), (3, 1), (3, 4)]

[155] and it's equivalent to:

[156] :

[157] >>> combs = []
   >>> for x in [1,2,3]:
   ...     for y in [3,1,4]:
   ...

[158] if x != y:
   ...             combs.append((x, y))
   ...
   >>> combs

[159] [(1, 3), (1, 4), (2, 3), (2, 1), (2, 4), (3, 1), (3, 4)]

[160] Note how the order of the :keyword:`for` and :keyword:`if` statements is the
same in both these snippets.

[161] If the expression is a tuple (e.g. the ``(x, y)`` in the previous example),
it must be parenthesized.

[162] ::

[163] >>> vec = [-4, -2, 0, 2, 4]
   >>> # create a new list with the values doubled
   >>>

[164] [x*2 for x in vec]
   [-8, -4, 0, 4, 8]
   >>> # filter the list to exclude negative numbers
   >>>

[165] [x for x in vec if x >= 0]
   [0, 2, 4]
   >>> # apply a function to all the elements
   >>>

[166] [abs(x) for x in vec]
   [4, 2, 0, 2, 4]
   >>> # call a method on each element
   >>> freshfruit

[167] = ['  banana', '  loganberry ', 'passion fruit  ']
   >>>

[168] [weapon.strip() for weapon in freshfruit]
   ['banana', 'loganberry', 'passion fruit']
   >>> # create a list of 2-tuples like (number, square)
   >>>

[169] [(x, x**2) for x in range(6)]

[170] [(0, 0), (1, 1), (2, 4), (3, 9), (4, 16), (5, 25)]
   >>> #

[171] the tuple must be parenthesized, otherwise an error is raised
   >>>

[172] [x, x**2 for x in range(6)]
     File "<stdin>", line 1
       [x, x**2 for x in range(6)]
        ^^^^^^^

[173] SyntaxError: did you forget parentheses around the comprehension target?

[174] >>> # flatten a list using a listcomp with two 'for'
   >>> vec = [[1,2,3], [4,5,6], [7,8,9]]
   >>>

[175] [num for elem in vec for num in elem]

[176] [1, 2, 3, 4, 5, 6, 7, 8, 9]

[177] List comprehensions can contain complex expressions and nested functions:

[178] :

[179] >>> from math import pi
   >>> [str(round(pi, i)) for i in range(1, 6)]
   ['3.1', '3.14', '3.142', '3.1416', '3.14159']

## Grouping X

  G1: sentences 1 (1 sentence)
  G2: sentences 2 (1 sentence)
  G3: sentences 3 (1 sentence)
  G4: sentences 4 (1 sentence)
  G5: sentences 5 (1 sentence)
  G6: sentences 6–7 (2 sentences)
  G7: sentences 8–14 (7 sentences)
  G8: sentences 15–21 (7 sentences)
  G9: sentences 22–27 (6 sentences)
  G10: sentences 28–33 (6 sentences)
  G11: sentences 34–40 (7 sentences)
  G12: sentences 41–46 (6 sentences)
  G13: sentences 47–54 (8 sentences)
  G14: sentences 55–59 (5 sentences)
  G15: sentences 60–64 (5 sentences)
  G16: sentences 65–69 (5 sentences)
  G17: sentences 70–75 (6 sentences)
  G18: sentences 76–77 (2 sentences)
  G19: sentences 78–80 (3 sentences)
  G20: sentences 81–83 (3 sentences)
  G21: sentences 84–87 (4 sentences)
  G22: sentences 88 (1 sentence)
  G23: sentences 89–99 (11 sentences)
  G24: sentences 100–102 (3 sentences)
  G25: sentences 103 (1 sentence)
  G26: sentences 104–105 (2 sentences)
  G27: sentences 106 (1 sentence)
  G28: sentences 107–117 (11 sentences)
  G29: sentences 118–120 (3 sentences)
  G30: sentences 121 (1 sentence)
  G31: sentences 122–124 (3 sentences)
  G32: sentences 125 (1 sentence)
  G33: sentences 126–135 (10 sentences)
  G34: sentences 136–137 (2 sentences)
  G35: sentences 138–139 (2 sentences)
  G36: sentences 140–141 (2 sentences)
  G37: sentences 142 (1 sentence)
  G38: sentences 143 (1 sentence)
  G39: sentences 144–145 (2 sentences)
  G40: sentences 146–147 (2 sentences)
  G41: sentences 148 (1 sentence)
  G42: sentences 149 (1 sentence)
  G43: sentences 150–151 (2 sentences)
  G44: sentences 152 (1 sentence)
  G45: sentences 153–154 (2 sentences)
  G46: sentences 155–156 (2 sentences)
  G47: sentences 157–159 (3 sentences)
  G48: sentences 160–161 (2 sentences)
  G49: sentences 162–176 (15 sentences)
  G50: sentences 177–178 (2 sentences)
  G51: sentences 179 (1 sentence)

## Grouping Y

  G1: sentences 1 (1 sentence)
  G2: sentences 2 (1 sentence)
  G3: sentences 3 (1 sentence)
  G4: sentences 4 (1 sentence)
  G5: sentences 5 (1 sentence)
  G6: sentences 6–7 (2 sentences)
  G7: sentences 8–10 (3 sentences)
  G8: sentences 11 (1 sentence)
  G9: sentences 12–14 (3 sentences)
  G10: sentences 15–17 (3 sentences)
  G11: sentences 18 (1 sentence)
  G12: sentences 19–21 (3 sentences)
  G13: sentences 22–24 (3 sentences)
  G14: sentences 25 (1 sentence)
  G15: sentences 26–27 (2 sentences)
  G16: sentences 28–30 (3 sentences)
  G17: sentences 31 (1 sentence)
  G18: sentences 32–33 (2 sentences)
  G19: sentences 34–36 (3 sentences)
  G20: sentences 37 (1 sentence)
  G21: sentences 38–40 (3 sentences)
  G22: sentences 41–43 (3 sentences)
  G23: sentences 44 (1 sentence)
  G24: sentences 45–46 (2 sentences)
  G25: sentences 47–49 (3 sentences)
  G26: sentences 50 (1 sentence)
  G27: sentences 51–52 (2 sentences)
  G28: sentences 53–54 (2 sentences)
  G29: sentences 55–57 (3 sentences)
  G30: sentences 58 (1 sentence)
  G31: sentences 59 (1 sentence)
  G32: sentences 60–62 (3 sentences)
  G33: sentences 63 (1 sentence)
  G34: sentences 64 (1 sentence)
  G35: sentences 65–67 (3 sentences)
  G36: sentences 68 (1 sentence)
  G37: sentences 69 (1 sentence)
  G38: sentences 70–72 (3 sentences)
  G39: sentences 73 (1 sentence)
  G40: sentences 74–75 (2 sentences)
  G41: sentences 76–77 (2 sentences)
  G42: sentences 78–80 (3 sentences)
  G43: sentences 81–83 (3 sentences)
  G44: sentences 84–86 (3 sentences)
  G45: sentences 87 (1 sentence)
  G46: sentences 88 (1 sentence)
  G47: sentences 89–91 (3 sentences)
  G48: sentences 92–94 (3 sentences)
  G49: sentences 95–97 (3 sentences)
  G50: sentences 98–99 (2 sentences)
  G51: sentences 100–102 (3 sentences)
  G52: sentences 103 (1 sentence)
  G53: sentences 104–105 (2 sentences)
  G54: sentences 106 (1 sentence)
  G55: sentences 107–109 (3 sentences)
  G56: sentences 110–112 (3 sentences)
  G57: sentences 113–115 (3 sentences)
  G58: sentences 116–117 (2 sentences)
  G59: sentences 118–119 (2 sentences)
  G60: sentences 120–121 (2 sentences)
  G61: sentences 122–124 (3 sentences)
  G62: sentences 125 (1 sentence)
  G63: sentences 126–128 (3 sentences)
  G64: sentences 129–131 (3 sentences)
  G65: sentences 132–134 (3 sentences)
  G66: sentences 135 (1 sentence)
  G67: sentences 136–137 (2 sentences)
  G68: sentences 138–139 (2 sentences)
  G69: sentences 140–141 (2 sentences)
  G70: sentences 142–143 (2 sentences)
  G71: sentences 144–145 (2 sentences)
  G72: sentences 146–147 (2 sentences)
  G73: sentences 148 (1 sentence)
  G74: sentences 149 (1 sentence)
  G75: sentences 150–152 (3 sentences)
  G76: sentences 153–154 (2 sentences)
  G77: sentences 155–156 (2 sentences)
  G78: sentences 157–159 (3 sentences)
  G79: sentences 160 (1 sentence)
  G80: sentences 161–162 (2 sentences)
  G81: sentences 163–165 (3 sentences)
  G82: sentences 166–168 (3 sentences)
  G83: sentences 169–171 (3 sentences)
  G84: sentences 172–174 (3 sentences)
  G85: sentences 175–176 (2 sentences)
  G86: sentences 177–178 (2 sentences)
  G87: sentences 179 (1 sentence)
