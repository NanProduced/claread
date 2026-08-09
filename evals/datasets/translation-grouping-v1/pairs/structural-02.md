# Blind grouping comparison — structural-02

You will judge two alternative partitions of the same article into
translation groups. The article's sentences are numbered below.
Read the article first, then evaluate both groupings against the rubric.

## Article sentences

[1] ## Why flexbox?

[2] CSS flexible box layout enables you to:

[3] - Vertically center a block of content inside its parent.

[4] - Make all the children of a container take up an equal amount of the available width/height, regardless of how much width/height is available.

[5] - Make all columns in a multiple-column layout adopt the same height even if they contain a different amount of content.

[6] Flexbox features may be the perfect solution for your one dimensional layout needs.

[7] Let's dig in and find out!

[8] > [!NOTE]
>

[9] Scrimba's introductory [Flexbox](https://scrimba.com/learn-html-and-css-c0p/~017?via=mdn)

[10] <sup>[_MDN learning partner_](/en-US/docs/MDN/Writing_guidelines/Learning_content#partner_links_and_embeds)</sup> scrim provides an interactive guide covering how common flexbox is on the web and therefore why it is so important to learn, and walks you through a typical use case that demonstrates the power of flexbox.

[11] ## Introducing a simple example

[12] In this article, you'll work through a series of exercises to help you understand how flexbox works.

[13] To get started, you should make a local copy of the HTML and CSS.

[14] Load it in a modern browser (like Firefox or Chrome) and have a look at the code in your code editor.

[15] Alternatively click the "Play" button to open it in the playground.

[16] ```html live-sample___flexbox_0
<header>
  <h1>Sample flexbox example</h1>
</header>
<section>
  <article>
    <h2>First article</h2>
    <p>Content…</p>
  </article>
  <article>
    <h2>Second article</h2>
    <p>Content…</p>
  </article>
  <article>
    <h2>Third article</h2>
    <p>Content…</p>
  </article>
</section>

[17] ```

[18] ```css live-sample___flexbox_0
body {
  font-family:

[19] sans-serif;

[20] margin:

[21] 0;

[22] }
header {
  background:

[23] purple;

[24] height:

[25] 100px;

[26] }
h1 {
  text-align:

[27] center;

[28] color:

[29] white;

[30] line-height:

[31] 100px;

[32] margin:

[33] 0;

[34] }
section {
  zoom:

[35] 0.8;

[36] }
article {
  padding:

[37] 10px;

[38] margin:

[39] 10px;

[40] background:

[41] aqua;

[42] }
/* Add your flexbox CSS below here */
```

[43] You'll see that we have a {{htmlelement("header")}} element with a top level heading inside it and a {{htmlelement("section")}} element containing three {{htmlelement("article")}}s.

[44] We're going to use these to create a fairly standard three column layout.

[45] ## Specifying what elements to lay out as flexible boxes

[46] To start with, we need to select which elements are to be laid out as flexible boxes.

[47] To do this, we set a special value of {{cssxref("display")}} on the parent element of the elements you want to affect.

[48] In this case we want to lay out the {{htmlelement("article")}} elements, so we set this on the {{htmlelement("section")}}:

[49] ```html hidden live-sample___flexbox_1
<header>
  <h1>Sample flexbox example</h1>
</header>
<section>
  <article>
    <h2>First article</h2>
    <p>Content…</p>
  </article>
  <article>
    <h2>Second article</h2>
    <p>Content…</p>
  </article>
  <article>
    <h2>Third article</h2>
    <p>Content…</p>
  </article>

[50] </section>
```

[51] ```css hidden live-sample___flexbox_1
body {
  font-family:

[52] sans-serif;

[53] margin:

[54] 0;

[55] }
header {
  background:

[56] purple;

[57] height:

[58] 100px;

[59] }
h1 {
  text-align:

[60] center;

[61] color:

[62] white;

[63] line-height:

[64] 100px;

[65] margin:

[66] 0;

[67] }
section {
  zoom:

[68] 0.8;

[69] }
article {
  padding:

[70] 10px;

[71] margin:

[72] 10px;

[73] background:

[74] aqua;

[75] }
/* Add your flexbox CSS below here */
```

[76] ```css live-sample___flexbox_1
section {
  display:

[77] flex;

[78] }
```

[79] This causes the `<section>` element to become a **flex container** and its children become **flex items**.

[80] This is what it looks like:

[81] This single declaration gives us everything we need.

[82] Incredible, right?

[83] We have a multiple column layout with equal-sized columns, and the columns are all the same height.

[84] This is because the default values given to flex items (the children of the flex container) are set up to solve common problems such as this.

[85] Let's recap what's happening here.

[86] Adding a {{cssxref("display")}} value of `flex` to an element makes it a flex container.

[87] The container is displayed as [Block-level content](/en-US/docs/Glossary/Block-level_content) in terms of how it interacts with the rest of the page.

[88] When the element is converted to a flex container, its children are converted to (and laid out as) flex items.

[89] You can make the container inline using an [outside `display` value](/en-US/docs/Web/CSS/Reference/Properties/display#outside) (e.g., `display: inline flex`), which affects how the container itself is laid out in the page.

[90] The legacy `inline-flex` display value displays the container as inline as well.

[91] We'll focus on how the contents of the container behave in this tutorial, but if you want to see the effect of inline versus block layout, you can have a look at the [value comparison](/en-US/docs/Web/CSS/Reference/Properties/display#display_value_comparison) on the `display` property page.

[92] The next sections explain in more detail what flex items are and what happens inside an element when you make it a flex container.

[93] ## The flex model

[94] When elements are laid out as flex items, they are laid out along two axes:

[95] !

[96] [Three flex items in a left-to-right language are laid out side-by-side in a flex container.

[97] The main axis — the axis of the flex container in the direction in which the flex items are laid out — is horizontal.

[98] The ends of the axis are main-start and main-end and are on the left and right respectively.

[99] The cross axis is vertical; perpendicular to the main axis.

[100] The cross-start and cross-end are at the top and bottom respectively.

[101] The length of the flex item along the main axis, in this case, the width, is called the main size, and the length of the flex item along the cross axis, in this case, the height, is called the cross size.](flex_terms.png)

[102] - The **main axis** is the axis running in the direction the flex items are laid out in (for example, as a row across the page, or a column down the page.)

[103] The start and end points of this axis are called the **main start** and **main end**.

[104] The length of a flex item along the main axis is the **main size**.
- The **cross axis** is the axis running perpendicular to the direction the flex items are laid out in.

[105] The start and end points of this axis are called the **cross start** and **cross end**.

[106] The length of a flex item along the cross axis is the **cross size**.
- The parent element that has `display: flex` set on it (the {{htmlelement("section")}} in our example) is called the **flex container**.
- The items laid out as flexible boxes inside the flex container are called **flex items** (the {{htmlelement("article")}} elements in our example).

[107] Bear this terminology in mind as you go through subsequent sections.

[108] You can always refer back to it if you get confused about any of the terms being used.

[109] ## Columns or rows?

[110] Flexbox provides a property called {{cssxref("flex-direction")}} that specifies which direction the main axis runs (which direction the flexbox children are laid out in).

[111] By default this is set to `row`, which causes them to be laid out in a row in the direction your browser's default language works in (left to right, in the case of an English browser).

[112] Try adding the following declaration to your {{htmlelement("section")}} rule:

[113] ```css
flex-direction:

[114] column;

[115] ```

[116] You'll see that this puts the items back in a column layout, much like they were before we added any CSS.

[117] Before you move on, delete this declaration from your example.

[118] > [!NOTE]
>

[119] You can also lay out flex items in a reverse direction using the `row-reverse` and `column-reverse` values.

[120] Experiment with these values too!

[121] ## Wrapping

[122] One issue that arises when you have a fixed width or height in your layout is that eventually your flexbox children will overflow their container, breaking the layout.

[123] In the following example we have 5 {{htmlelement("article")}}s, which don't fit, because they have a `min-width` of `400px`, so there is a horizontal scroll.

[124] ```html hidden live-sample___flex-wrap_0
<header>
  <h1>Sample flexbox example</h1>
</header>
<section>
  <article>
    <h2>First article</h2>
    <p>Content…</p>
  </article>
  <article>
    <h2>Second article</h2>
    <p>Content…</p>
  </article>
  <article>
    <h2>Third article</h2>
    <p>Content…</p>
  </article>

[125] <article>
    <h2>Fourth article</h2>
    <p>Content…</p>
  </article>
  <article>
    <h2>Fifth article</h2>
    <p>Content…</p>
  </article>
</section>
```

[126] ```css hidden live-sample___flex-wrap_0
body {
  font-family:

[127] sans-serif;

[128] margin:

[129] 0;

[130] }
header {
  background:

[131] purple;

[132] height:

[133] 100px;

[134] }
h1 {
  text-align:

[135] center;

[136] color:

[137] white;

[138] line-height:

[139] 100px;

[140] margin:

[141] 0;

[142] }
article {
  min-width:

[143] 400px;

[144] padding:

[145] 10px;

[146] margin:

[147] 10px;

[148] background:

[149] aqua;

[150] }
section {
  display:

[151] flex;

[152] flex-direction:

[153] row;

[154] zoom:

[155] 0.8;

[156] }
```

[157] Here we see that the children are indeed breaking out of their container.

[158] By default, the browser tries to place all the flex items in a single row if the `flex-direction` is set to `row` or a single column if the `flex-direction` is set to `column`.

[159] ```html hidden live-sample___flex-wrap_1
<header>
  <h1>Sample flexbox example</h1>
</header>
<section>
  <article>
    <h2>First article</h2>
    <p>Content…</p>
  </article>
  <article>
    <h2>Second article</h2>
    <p>Content…</p>
  </article>
  <article>
    <h2>Third article</h2>
    <p>Content…</p>
  </article>

[160] <article>
    <h2>Fourth article</h2>
    <p>Content…</p>
  </article>
  <article>
    <h2>Fifth article</h2>
    <p>Content…</p>
  </article>
</section>
```

[161] ```css hidden live-sample___flex-wrap_1
body {
  font-family:

[162] sans-serif;

[163] margin:

[164] 0;

[165] }
header {
  background:

[166] purple;

[167] height:

[168] 100px;

[169] }
h1 {
  text-align:

[170] center;

[171] color:

[172] white;

[173] line-height:

[174] 100px;

[175] margin:

[176] 0;

[177] }
article {
  min-width:

[178] 400px;

[179] padding:

[180] 10px;

[181] margin:

[182] 10px;

[183] background:

[184] aqua;

[185] }
section {
  display:

[186] flex;

[187] flex-direction:

[188] row;

[189] zoom:

[190] 0.8;

[191] }
```

[192] One way in which you can fix this is to add the following declaration to your {{htmlelement("section")}} rule:

[193] ```css live-sample___flex-wrap_1
section {
  flex-wrap:

[194] wrap;

[195] }
```

[196] You'll see that the layout looks much better with this included:

[197] We now have multiple rows.

[198] Each row has as many flexbox children fitted into it as is sensible.

[199] Any overflow is moved down to the next line.

## Grouping X

  G1: sentences 1 (1 sentence)
  G2: sentences 2 (1 sentence)
  G3: sentences 3–5 (3 sentences)
  G4: sentences 6–7 (2 sentences)
  G5: sentences 8–10 (3 sentences)
  G6: sentences 11 (1 sentence)
  G7: sentences 12–15 (4 sentences)
  G8: sentences 16–17 (2 sentences)
  G9: sentences 18–42 (25 sentences)
  G10: sentences 43–44 (2 sentences)
  G11: sentences 45 (1 sentence)
  G12: sentences 46–48 (3 sentences)
  G13: sentences 49–50 (2 sentences)
  G14: sentences 51–75 (25 sentences)
  G15: sentences 76–78 (3 sentences)
  G16: sentences 79–80 (2 sentences)
  G17: sentences 81–84 (4 sentences)
  G18: sentences 85 (1 sentence)
  G19: sentences 86–88 (3 sentences)
  G20: sentences 89–90 (2 sentences)
  G21: sentences 91–92 (2 sentences)
  G22: sentences 93 (1 sentence)
  G23: sentences 94 (1 sentence)
  G24: sentences 95–101 (7 sentences)
  G25: sentences 102–103 (2 sentences)
  G26: sentences 104–106 (3 sentences)
  G27: sentences 107–108 (2 sentences)
  G28: sentences 109 (1 sentence)
  G29: sentences 110–111 (2 sentences)
  G30: sentences 112 (1 sentence)
  G31: sentences 113–115 (3 sentences)
  G32: sentences 116–117 (2 sentences)
  G33: sentences 118–120 (3 sentences)
  G34: sentences 121 (1 sentence)
  G35: sentences 122–123 (2 sentences)
  G36: sentences 124–125 (2 sentences)
  G37: sentences 126–156 (31 sentences)
  G38: sentences 157–158 (2 sentences)
  G39: sentences 159–160 (2 sentences)
  G40: sentences 161–191 (31 sentences)
  G41: sentences 192 (1 sentence)
  G42: sentences 193–195 (3 sentences)
  G43: sentences 196–199 (4 sentences)

## Grouping Y

  G1: sentences 1 (1 sentence)
  G2: sentences 2 (1 sentence)
  G3: sentences 3–5 (3 sentences)
  G4: sentences 6–7 (2 sentences)
  G5: sentences 8–10 (3 sentences)
  G6: sentences 11 (1 sentence)
  G7: sentences 12–14 (3 sentences)
  G8: sentences 15 (1 sentence)
  G9: sentences 16–17 (2 sentences)
  G10: sentences 18–20 (3 sentences)
  G11: sentences 21–23 (3 sentences)
  G12: sentences 24–26 (3 sentences)
  G13: sentences 27–29 (3 sentences)
  G14: sentences 30–32 (3 sentences)
  G15: sentences 33–35 (3 sentences)
  G16: sentences 36–38 (3 sentences)
  G17: sentences 39–41 (3 sentences)
  G18: sentences 42 (1 sentence)
  G19: sentences 43–44 (2 sentences)
  G20: sentences 45 (1 sentence)
  G21: sentences 46–48 (3 sentences)
  G22: sentences 49–50 (2 sentences)
  G23: sentences 51–53 (3 sentences)
  G24: sentences 54–56 (3 sentences)
  G25: sentences 57–59 (3 sentences)
  G26: sentences 60–62 (3 sentences)
  G27: sentences 63–65 (3 sentences)
  G28: sentences 66–68 (3 sentences)
  G29: sentences 69–71 (3 sentences)
  G30: sentences 72–74 (3 sentences)
  G31: sentences 75 (1 sentence)
  G32: sentences 76–78 (3 sentences)
  G33: sentences 79–80 (2 sentences)
  G34: sentences 81–83 (3 sentences)
  G35: sentences 84 (1 sentence)
  G36: sentences 85–87 (3 sentences)
  G37: sentences 88 (1 sentence)
  G38: sentences 89–91 (3 sentences)
  G39: sentences 92 (1 sentence)
  G40: sentences 93 (1 sentence)
  G41: sentences 94 (1 sentence)
  G42: sentences 95–97 (3 sentences)
  G43: sentences 98–100 (3 sentences)
  G44: sentences 101 (1 sentence)
  G45: sentences 102–104 (3 sentences)
  G46: sentences 105–106 (2 sentences)
  G47: sentences 107–108 (2 sentences)
  G48: sentences 109 (1 sentence)
  G49: sentences 110–111 (2 sentences)
  G50: sentences 112 (1 sentence)
  G51: sentences 113–115 (3 sentences)
  G52: sentences 116–117 (2 sentences)
  G53: sentences 118–120 (3 sentences)
  G54: sentences 121 (1 sentence)
  G55: sentences 122–123 (2 sentences)
  G56: sentences 124–125 (2 sentences)
  G57: sentences 126–128 (3 sentences)
  G58: sentences 129–131 (3 sentences)
  G59: sentences 132–134 (3 sentences)
  G60: sentences 135–137 (3 sentences)
  G61: sentences 138–140 (3 sentences)
  G62: sentences 141–143 (3 sentences)
  G63: sentences 144–146 (3 sentences)
  G64: sentences 147–149 (3 sentences)
  G65: sentences 150–152 (3 sentences)
  G66: sentences 153–155 (3 sentences)
  G67: sentences 156 (1 sentence)
  G68: sentences 157–158 (2 sentences)
  G69: sentences 159–160 (2 sentences)
  G70: sentences 161–163 (3 sentences)
  G71: sentences 164–166 (3 sentences)
  G72: sentences 167–169 (3 sentences)
  G73: sentences 170–172 (3 sentences)
  G74: sentences 173–175 (3 sentences)
  G75: sentences 176–178 (3 sentences)
  G76: sentences 179–181 (3 sentences)
  G77: sentences 182–184 (3 sentences)
  G78: sentences 185–187 (3 sentences)
  G79: sentences 188–190 (3 sentences)
  G80: sentences 191 (1 sentence)
  G81: sentences 192 (1 sentence)
  G82: sentences 193–195 (3 sentences)
  G83: sentences 196 (1 sentence)
  G84: sentences 197–199 (3 sentences)
