# Blind grouping comparison — structural-04

You will judge two alternative partitions of the same article into
translation groups. The article's sentences are numbered below.
Read the article first, then evaluate both groupings against the rubric.

## Article sentences

[1] .. _tut-morecontrol:

[2] ***********************
More Control Flow Tools
***********************

[3] As well as the :keyword:`while` statement just introduced, Python uses a few more
that we will encounter in this chapter.

[4] .. _tut-if:

[5] :

[6] keyword:

[7] `!if` Statements
=========================

[8] Perhaps the most well-known statement type is the :keyword:`if` statement.

[9] For
example::

[10] >>> x = int(input("Please enter an integer: "))
   Please enter an integer: 42
   >>> if x < 0:
   ...

[11] x = 0
   ...     print('Negative changed to zero')
   ...

[12] elif x == 0:
   ...     print('Zero')
   ...

[13] elif x == 1:
   ...     print('Single')
   ... else:
   ...     print('More')
   ...

[14] More

[15] There can be zero or more :keyword:`elif` parts, and the :keyword:`else` part is
optional.

[16] The keyword ':keyword:`!elif`' is short for 'else if', and is useful
to avoid excessive indentation.

[17] An  :keyword:`!if` ... :keyword:`!elif` ...
:keyword:`!elif` ... sequence is a substitute for the ``switch`` or
``case`` statements found in other languages.

[18] If you're comparing the same value to several constants, or checking for specific types or
attributes, you may also find the :keyword:`!match` statement useful.

[19] For more
details see :ref:`tut-match`.

[20] .. _tut-for:

[21] :

[22] keyword:

[23] `!for` Statements
==========================

[24] .. index:

[25] :

[26] pair:

[27] statement;

[28] for

[29] The :keyword:`for` statement in Python differs a bit from what you may be used
to in C or Pascal.

[30] Rather than always iterating over an arithmetic progression
of numbers (like in Pascal), or giving the user the ability to define both the
iteration step and halting condition (as C), Python's :keyword:`!for` statement
iterates over the items of any sequence (a list or a string), in the order that
they appear in the sequence.

[31] For example (no pun intended):

[32] ..

[33] One suggestion was to give a real C example here, but that may only serve to
   confuse non-C programmers.

[34] :

[35] :

[36] >>> #

[37] Measure some strings:
   >>>

[38] words = ['cat', 'window', 'defenestrate']
   >>> for w in words:
   ...

[39] print(w, len(w))
   ...
   cat 3
   window 6
   defenestrate 12

[40] Code that modifies a collection while iterating over that same collection can
be tricky to get right.

[41] Instead, it is usually more straight-forward to loop
over a copy of the collection or to create a new collection::

[42] # Create a sample collection
    users = {'Hans':

[43] 'active', 'Éléonore':

[44] 'inactive', '景太郎':

[45] 'active'}

[46] # Strategy:

[47] Iterate over a copy
    for user, status in users.copy().items():

[48] if status == 'inactive':

[49] del users[user]

[50] # Strategy:

[51] Create a new collection
    active_users = {}
    for user, status in users.items():

[52] if status == 'active':

[53] active_users[user] = status

[54] .. _tut-range:

[55] The :

[56] func:

[57] `range` Function
==========================

[58] If you do need to iterate over a sequence of numbers, the built-in function
:func:`range` comes in handy.

[59] It generates arithmetic progressions::

[60] >>> for i in range(5):
    ...     print(i)
    ...

[61] 0
    1
    2
    3
    4

[62] The given end point is never part of the generated sequence; ``range(10)`` generates
10 values, the legal indices for items of a sequence of length 10.

[63] It
is possible to let the range start at another number, or to specify a different
increment (even negative; sometimes this is called the 'step')::

[64] >>> list(range(5, 10))
    [5, 6, 7, 8, 9]

[65] >>> list(range(0, 10, 3))
    [0, 3, 6, 9]

[66] >>> list(range(-10, -100, -30))
    [-10, -40, -70]

[67] To iterate over the indices of a sequence, you can combine :

[68] func:

[69] `range` and
:

[70] func:

[71] `len` as follows:

[72] :

[73] >>> a = ['Mary', 'had', 'a', 'little', 'lamb']
   >>> for i in range(len(a)):
   ...

[74] print(i, a[i])
   ...
   0

[75] Mary
   1 had
   2 a
   3 little
   4 lamb

[76] In most such cases, however, it is convenient to use the :func:`enumerate`
function, see :ref:`tut-loopidioms`.

[77] A strange thing happens if you just print a range:

[78] :

[79] >>> range(10)
   range(0, 10)

[80] In many ways the object returned by :func:`range` behaves as if it is a list,
but in fact it isn't.

[81] It is an object which returns the successive items of
the desired sequence when you iterate over it, but it doesn't really make
the list, thus saving space.

[82] We say such an object is :term:`iterable`, that is, suitable as a target for
functions and constructs that expect something from which they can
obtain successive items until the supply is exhausted.

[83] We have seen that
the :keyword:`for` statement is such a construct, while an example of a function
that takes an iterable is :func:`sum`::

[84] >>> sum(range(4))  # 0 + 1 + 2 + 3
    6

[85] Later we will see more functions that return iterables and take iterables as
arguments.

[86] In chapter :ref:`tut-structures`, we will discuss :func:`list` in more
detail.

[87] .. _tut-break:

[88] :

[89] keyword:

[90] `!break` and :

[91] keyword:

[92] `!continue` Statements
=====================================================

[93] The :

[94] keyword:

[95] `break` statement breaks out of the innermost enclosing
:

[96] keyword:

[97] `for` or :

[98] keyword:

[99] `while` loop:

[100] :

[101] >>> for n in range(2, 10):
    ...     for x in range(2, n):
    ...

[102] if n % x

[103] == 0:
    ...             print(f"{n} equals {x} * {n//x}")
    ...

[104] break
    ...

[105] 4 equals 2 * 2
    6 equals 2 * 3
    8 equals 2 * 4
    9 equals 3 * 3

[106] The :

[107] keyword:

[108] `continue` statement continues with the next
iteration of the loop:

[109] :

[110] >>> for num in range(2, 10):
    ...

[111] if num % 2 == 0:
    ...         print(f"Found an even number {num}")
    ...         continue
    ...

[112] print(f"Found an odd number {num}")
    ...
    Found an even number 2
    Found an odd number 3
    Found an even number 4
    Found an odd number 5
    Found an even number 6
    Found an odd number 7
    Found an even number 8
    Found an odd number 9

[113] .. _tut-for-else:
..

[114] _break-and-continue-statements-and-else-clauses-on-loops:

[115] :

[116] keyword:

[117] `!else` Clauses on Loops
=================================

[118] In a :keyword:`!for` or :keyword:`!while` loop the :keyword:`!break` statement
may be paired with an :keyword:`!else` clause.

[119] If the loop finishes without
executing the :keyword:`!break`, the :keyword:`!else` clause executes.

[120] In a :keyword:`for` loop, the :keyword:`!else` clause is executed
after the loop finishes its final iteration, that is, if no break occurred.

[121] In a :keyword:`while` loop, it's executed after the loop's condition becomes false.

[122] In either kind of loop, the :keyword:`!else` clause is **not** executed if the
loop was terminated by a :keyword:`break`.

[123] Of course, other ways of ending the
loop early, such as a :keyword:`return` or a raised exception, will also skip
execution of the :keyword:`else` clause.

[124] This is exemplified in the following :

[125] keyword:

[126] `!for` loop,

[127] which searches for prime numbers:

[128] :

[129] >>> for n in range(2, 10):
   ...     for x in range(2, n):
   ...

[130] if n % x

[131] == 0:
   ...

[132] print(n, 'equals', x, '*', n//x)
   ...

[133] break
   ...     else:
   ...         # loop fell through without finding a factor
   ...

[134] print(n, 'is a prime number')
   ...
   2 is a prime number
   3 is a prime number
   4 equals 2 * 2
   5 is a prime number
   6 equals 2 * 3
   7 is a prime number
   8 equals 2 * 4
   9 equals 3 * 3

[135] (Yes, this is the correct code.

[136] Look closely: the ``else`` clause belongs to
the ``for`` loop, **not** the ``if`` statement.)

[137] One way to think of the else clause is to imagine it paired with the ``if``
inside the loop.

[138] As the loop executes, it will run a sequence like
if/if/if/else.

[139] The ``if`` is inside the loop, encountered a number of times.

[140] If
the condition is ever true, a ``break`` will happen.

[141] If the condition is never
true, the ``else`` clause outside the loop will execute.

[142] When used with a loop, the ``else`` clause has more in common with the ``else``
clause of a :keyword:`try` statement than it does with that of ``if``
statements: a ``try`` statement's ``else`` clause runs when no exception
occurs, and a loop's ``else`` clause runs when no ``break`` occurs.

[143] For more on
the ``try`` statement and exceptions, see :ref:`tut-handling`.

[144] .. index:

[145] :

[146] single:

[147] ...;

[148] ellipsis literal

## Grouping X

  G1: sentences 1 (1 sentence)
  G2: sentences 2 (1 sentence)
  G3: sentences 3 (1 sentence)
  G4: sentences 4 (1 sentence)
  G5: sentences 5–7 (3 sentences)
  G6: sentences 8–9 (2 sentences)
  G7: sentences 10–12 (3 sentences)
  G8: sentences 13–14 (2 sentences)
  G9: sentences 15–17 (3 sentences)
  G10: sentences 18–19 (2 sentences)
  G11: sentences 20 (1 sentence)
  G12: sentences 21–23 (3 sentences)
  G13: sentences 24–26 (3 sentences)
  G14: sentences 27–28 (2 sentences)
  G15: sentences 29–31 (3 sentences)
  G16: sentences 32–33 (2 sentences)
  G17: sentences 34–35 (2 sentences)
  G18: sentences 36–38 (3 sentences)
  G19: sentences 39 (1 sentence)
  G20: sentences 40–41 (2 sentences)
  G21: sentences 42–44 (3 sentences)
  G22: sentences 45 (1 sentence)
  G23: sentences 46–48 (3 sentences)
  G24: sentences 49 (1 sentence)
  G25: sentences 50–52 (3 sentences)
  G26: sentences 53 (1 sentence)
  G27: sentences 54 (1 sentence)
  G28: sentences 55–57 (3 sentences)
  G29: sentences 58–59 (2 sentences)
  G30: sentences 60–61 (2 sentences)
  G31: sentences 62–63 (2 sentences)
  G32: sentences 64 (1 sentence)
  G33: sentences 65 (1 sentence)
  G34: sentences 66 (1 sentence)
  G35: sentences 67–69 (3 sentences)
  G36: sentences 70–72 (3 sentences)
  G37: sentences 73–75 (3 sentences)
  G38: sentences 76 (1 sentence)
  G39: sentences 77–78 (2 sentences)
  G40: sentences 79 (1 sentence)
  G41: sentences 80–81 (2 sentences)
  G42: sentences 82–83 (2 sentences)
  G43: sentences 84 (1 sentence)
  G44: sentences 85–86 (2 sentences)
  G45: sentences 87 (1 sentence)
  G46: sentences 88–90 (3 sentences)
  G47: sentences 91–92 (2 sentences)
  G48: sentences 93–95 (3 sentences)
  G49: sentences 96–98 (3 sentences)
  G50: sentences 99–100 (2 sentences)
  G51: sentences 101–103 (3 sentences)
  G52: sentences 104–105 (2 sentences)
  G53: sentences 106–108 (3 sentences)
  G54: sentences 109 (1 sentence)
  G55: sentences 110–112 (3 sentences)
  G56: sentences 113–114 (2 sentences)
  G57: sentences 115–117 (3 sentences)
  G58: sentences 118–119 (2 sentences)
  G59: sentences 120 (1 sentence)
  G60: sentences 121 (1 sentence)
  G61: sentences 122–123 (2 sentences)
  G62: sentences 124–126 (3 sentences)
  G63: sentences 127–128 (2 sentences)
  G64: sentences 129–131 (3 sentences)
  G65: sentences 132–134 (3 sentences)
  G66: sentences 135–136 (2 sentences)
  G67: sentences 137–139 (3 sentences)
  G68: sentences 140–141 (2 sentences)
  G69: sentences 142–143 (2 sentences)
  G70: sentences 144–146 (3 sentences)
  G71: sentences 147–148 (2 sentences)

## Grouping Y

  G1: sentences 1 (1 sentence)
  G2: sentences 2 (1 sentence)
  G3: sentences 3 (1 sentence)
  G4: sentences 4 (1 sentence)
  G5: sentences 5–7 (3 sentences)
  G6: sentences 8 (1 sentence)
  G7: sentences 9 (1 sentence)
  G8: sentences 10–14 (5 sentences)
  G9: sentences 15–17 (3 sentences)
  G10: sentences 18–19 (2 sentences)
  G11: sentences 20 (1 sentence)
  G12: sentences 21–23 (3 sentences)
  G13: sentences 24–28 (5 sentences)
  G14: sentences 29–30 (2 sentences)
  G15: sentences 31 (1 sentence)
  G16: sentences 32–33 (2 sentences)
  G17: sentences 34–35 (2 sentences)
  G18: sentences 36–39 (4 sentences)
  G19: sentences 40–41 (2 sentences)
  G20: sentences 42–45 (4 sentences)
  G21: sentences 46–49 (4 sentences)
  G22: sentences 50–53 (4 sentences)
  G23: sentences 54 (1 sentence)
  G24: sentences 55–57 (3 sentences)
  G25: sentences 58–59 (2 sentences)
  G26: sentences 60–61 (2 sentences)
  G27: sentences 62 (1 sentence)
  G28: sentences 63 (1 sentence)
  G29: sentences 64–66 (3 sentences)
  G30: sentences 67–72 (6 sentences)
  G31: sentences 73–75 (3 sentences)
  G32: sentences 76 (1 sentence)
  G33: sentences 77–78 (2 sentences)
  G34: sentences 79 (1 sentence)
  G35: sentences 80–82 (3 sentences)
  G36: sentences 83 (1 sentence)
  G37: sentences 84 (1 sentence)
  G38: sentences 85–86 (2 sentences)
  G39: sentences 87 (1 sentence)
  G40: sentences 88–92 (5 sentences)
  G41: sentences 93–100 (8 sentences)
  G42: sentences 101–105 (5 sentences)
  G43: sentences 106–109 (4 sentences)
  G44: sentences 110–112 (3 sentences)
  G45: sentences 113–114 (2 sentences)
  G46: sentences 115–117 (3 sentences)
  G47: sentences 118–119 (2 sentences)
  G48: sentences 120–122 (3 sentences)
  G49: sentences 123 (1 sentence)
  G50: sentences 124–128 (5 sentences)
  G51: sentences 129–134 (6 sentences)
  G52: sentences 135–136 (2 sentences)
  G53: sentences 137–138 (2 sentences)
  G54: sentences 139–141 (3 sentences)
  G55: sentences 142–143 (2 sentences)
  G56: sentences 144–148 (5 sentences)
