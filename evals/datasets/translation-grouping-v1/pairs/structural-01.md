# Blind grouping comparison — structural-01

You will judge two alternative partitions of the same article into
translation groups. The article's sentences are numbered below.
Read the article first, then evaluate both groupings against the rubric.

## Article sentences

[1] This article gets you started with HTML tables, covering the very basics such as rows, cells, headings, making cells span multiple columns and rows, and how to group together all the cells in a column for styling purposes.

[2] <table>
  <tbody>
    <tr>
      <th scope="row">Prerequisites:</th>
      <td>
        Basic HTML familiarity, as covered in
        <a href="/en-US/docs/Learn_web_development/Core/Structuring_content/Basic_HTML_syntax"
          >Basic HTML Syntax</a
        >.

[3] </td>
    </tr>
    <tr>
      <th scope="row">Learning outcomes:</th>
      <td>
        <ul>
          <li>What tables are for — structuring tabular data.</li>
          <li>

[4] What tables are not for — layout, or <em>anything else</em>.</li>
          <li>Basic table syntax — <code>&lt;table&gt;</code>, <code>&lt;tr&gt;</code>, and <code>&lt;td&gt;</code>.</li>
          <li>Defining table headers with <code>&lt;th&gt;</code>.</li>
          <li>Spanning multiple columns and rows with <code>colspan</code> and <code>rowspan</code>.</li>
          <li>Grouping columns with <code>&lt;colgroup&gt;</code> and <code>&lt;col&gt;</code>.</li>
        </ul>
      </td>
    </tr>
  </tbody>
</table>

[5] ##

[6] What is a table?

[7] A table is a structured set of data made up of rows and columns (**tabular data**).

[8] A table allows you to quickly and easily look up values that indicate some kind of connection between different types of data, for example a person and their age, or a day of the week, or the timetable for a local swimming pool.

[9] !

[10] [A sample table showing names and ages of some people - Chris 38, Dennis 45, Sarah 29, Karen 47.](numbers-table.png)

[11] !

[12] [A swimming timetable showing a sample data table](swimming-timetable.png)

[13] Tables are very commonly used in human society,

[14] and have been for a long time, as evidenced by this US Census document from 1800:

[15] !

[16] [A very old parchment document; the data is not easily readable, but it clearly shows a data table being used.](1800-census.jpg)

[17] It is therefore no wonder that the creators of HTML provided a means by which to structure and present tabular data on the web.

[18] ### How does a table work?

[19] The point of a table is that it is rigid.

[20] Information is easily interpreted by making visual associations between row and column headers.

[21] Look at the table below for example and find a Jovian gas giant with 62 moons.

[22] You can find the answer by associating the relevant row and column headers.

[23] ```html hidden
<table>
  <caption>
    Data about the planets of our solar system (Source:
    <a href="https://nssdc.gsfc.nasa.gov/planetary/factsheet/"
      >Nasa's Planetary Fact Sheet - Metric</a
    >).

[24] </caption>
  <thead>
    <tr>
      <td colspan="2"></td>
      <th scope="col">Name</th>
      <th scope="col">Mass (10<sup>24</sup>kg)</th>
      <th scope="col">Diameter (km)</th>
      <th scope="col">Density (kg/m<sup>3</sup>)</th>
      <th scope="col">Gravity (m/s<sup>2</sup>)</th>
      <th scope="col">Length of day (hours)</th>
      <th scope="col">Distance from Sun (10<sup>6</sup>km)</th>
      <th scope="col">Mean temperature (°C)</th>
      <th scope="col">Number of moons</th>
      <th scope="col">Notes</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th colspan="2" rowspan="4" scope="rowgroup">Terrestrial planets</th>
      <th scope="row">Mercury</th>
      <td>0.330</td>
      <td>4,879</td>
      <td>5427</td>
      <td>3.7</td>
      <td>4222.6</td>
      <td>57.9</td>
      <td>167</td>
      <td>0</td>
      <td>Closest to the Sun</td>
    </tr>
    <tr>
      <th scope="row">Venus</th>
      <td>4.87</td>
      <td>12,104</td>
      <td>5243</td>
      <td>8.9</td>
      <td>2802.0</td>
      <td>108.2</td>
      <td>464</td>
      <td>0</td>
      <td></td>
    </tr>
    <tr>
      <th scope="row">Earth</th>
      <td>5.97</td>
      <td>12,756</td>
      <td>5514</td>
      <td>9.8</td>
      <td>24.0</td>
      <td>149.6</td>
      <td>15</td>
      <td>1</td>
      <td>Our world</td>
    </tr>
    <tr>
      <th scope="row">Mars</th>
      <td>0.642</td>
      <td>6,792</td>
      <td>3933</td>
      <td>3.7</td>
      <td>24.7</td>
      <td>227.9</td>
      <td>-65</td>
      <td>2</td>
      <td>The red planet</td>
    </tr>
    <tr>
      <th rowspan="4"

[25] scope="rowgroup">Jovian planets</th>
      <th rowspan="2" scope="rowgroup">Gas giants</th>
      <th scope="row">Jupiter</th>
      <td>1898</td>
      <td>142,984</td>
      <td>1326</td>
      <td>23.1</td>
      <td>9.9</td>
      <td>778.6</td>
      <td>-110</td>
      <td>67</td>
      <td>The largest planet</td>
    </tr>
    <tr>
      <th scope="row">Saturn</th>
      <td>568</td>
      <td>120,536</td>
      <td>687</td>
      <td>9.0</td>
      <td>10.7</td>
      <td>1433.5</td>
      <td>-140</td>
      <td>62</td>
      <td></td>
    </tr>
    <tr>
      <th rowspan="2" scope="rowgroup">Ice giants</th>
      <th scope="row">Uranus</th>
      <td>86.8</td>
      <td>51,118</td>
      <td>1271</td>
      <td>8.7</td>
      <td>17.2</td>
      <td>2872.5</td>
      <td>-195</td>
      <td>27</td>
      <td></td>
    </tr>
    <tr>
      <th scope="row">Neptune</th>
      <td>102</td>
      <td>49,528</td>
      <td>1638</td>
      <td>11.0</td>
      <td>16.1</td>
      <td>4495.1</td>
      <td>-200</td>
      <td>14</td>
      <td></td>
    </tr>
    <tr>
      <th colspan="2" scope="rowgroup">Dwarf planets</th>
      <th scope="row">Pluto</th>
      <td>0.0146</td>
      <td>2,370</td>
      <td>2095</td>
      <td>0.7</td>
      <td>153.3</td>
      <td>5906.4</td>
      <td>-225</td>
      <td>5</td>
      <td>
        Declassified as a planet in 2006, but this
        <a
          href="https://www.usatoday.com/story/tech/2014/10/02/pluto-planet-solar-system/16578959/"
          >remains controversial</a
        >.

[26] </td>
    </tr>
  </tbody>
</table>
```

[27] ```css hidden
table {
  border-collapse:

[28] collapse;

[29] border:

[30] 2px solid black;

[31] }

[32] th,
td {
  padding:

[33] 5px;

[34] border:

[35] 1px solid black;

[36] }
```

[37] {{EmbedLiveSample("How_does_a_table_work", 100, 560)}}

[38] When implemented correctly, HTML tables are handled well by accessibility tools such as screen readers, so a successful HTML table should enhance the experience of sighted and visually impaired users alike.

[39] ### Table styling

[40] You can also have a

[41] [look at the live planets data example](https://mdn.github.io/learning-area/html/tables/assessment-finished/planets-data.html) on GitHub!

[42] One thing you'll notice is that the table does look a bit more readable there — this is because the table you see above on this page has minimal styling, whereas the GitHub version has more significant CSS applied.

[43] Be under no illusion; for tables to be effective on the web, you need to provide some styling information with [CSS](/en-US/docs/Learn_web_development/Core/Styling_basics), as well as good solid structure with HTML.

[44] In this lesson we are focusing on the HTML part; you'll find out about styling tables later on, in our [Styling tables](/en-US/docs/Learn_web_development/Core/Styling_basics/Tables) lesson.

[45] We won't focus on CSS in this module, but we have provided a minimal CSS stylesheet for you to use that will make your tables more readable than the default you get without any styling.

[46] You can find the [stylesheet here](https://github.com/mdn/learning-area/blob/main/html/tables/basic/minimal-table.css), and you can also find an [HTML template](https://github.com/mdn/learning-area/blob/main/html/tables/basic/blank-template.html) that applies the stylesheet — these together will give you a good starting point for experimenting with HTML tables.

[47] ### When should you avoid HTML tables?

[48] HTML tables should be used for tabular data (information that's easy to work with in rows and columns) — this is what they are designed for.

[49] Unfortunately, a lot of people used to use HTML tables to lay out web pages, for example one row to contain a page header, a row to contain each content column, one row to contain the footer, etc.

[50] This technique was used in the past because CSS support across browsers used to be a lot more limited.

[51] Modern browsers have solid CSS support so table-based layouts are no longer needed.

[52] Table layouts are now extremely rare, but you might still see them in some corners of the web.

[53] In short, using tables for layout rather than [CSS layout techniques](/en-US/docs/Learn_web_development/Core/CSS_layout) is a bad idea.

[54] The main reasons are as follows:

[55] 1. **Layout tables reduce accessibility for visually impaired users**: [screen readers](/en-US/docs/Learn_web_development/Core/Accessibility/Tooling#screen_readers), used by blind people, interpret the tags that exist in an HTML page and read out the contents to the user.

[56] Because tables are not the right tool for layout, and the markup is more complex than with CSS layout techniques, the screen readers' output will be confusing to their users.

[57] 2. **Tables produce tag soup**: As mentioned above, table layouts generally involve more complex markup structures than proper layout techniques.

[58] This can result in the code being harder to write, maintain, and debug.

[59] 3. **Tables are not automatically responsive**: When you use proper layout containers (such as {{htmlelement("header")}}, {{htmlelement("section")}}, {{htmlelement("article")}}, or {{htmlelement("div")}}), their width defaults to 100% of their parent element.

[60] Tables on the other hand are sized according to their content by default, so extra measures are needed to get table layout styling to effectively work across a variety of devices.

[61] ## Creating your first table

[62] We've talked table theory enough, so, let's dive into a practical example and get you to build up a simple table.

[63] 1.

[64] First of all, make a copy of [blank-template.html](https://github.com/mdn/learning-area/blob/main/html/tables/basic/blank-template.html) and [minimal-table.css](https://github.com/mdn/learning-area/blob/main/html/tables/basic/minimal-table.css) in a new directory on your local machine.

[65] The HTML template already contains a `<link>` element to apply the CSS to the HTML, so you don't need to worry about that.

[66] 2.

[67] The content of every table is enclosed by these two tags: **[`<table></table>`](/en-US/docs/Web/HTML/Reference/Elements/table)**.

[68] Add these inside the body of your HTML.

[69] 3.

[70] The smallest container inside a table is a table cell, which is created with a **[`<td>`](/en-US/docs/Web/HTML/Reference/Elements/td)** element ("td" stands for "table data").

[71] Add the following inside your table tags:

[72] ```html
   <td>Hi, I'm your first cell.</td>
   ```

[73] 4.

[74] If we want a row of four cells, we need to copy these tags three times.

[75] Update the contents of your table to look like so:

[76] ```html
   <td>Hi, I'm your first cell.</td>
   <td>I'm your second cell.</td>
   <td>I'm your third cell.</td>
   <td>I'm your fourth cell.</td>
   ```

[77] As you will see, the cells are not placed underneath each other, rather they are automatically aligned with each other on the same row.

[78] Each `<td>` element creates a single cell and together they make up the first row.

[79] Every cell we add makes the row grow longer.

[80] To stop this row from growing and start placing subsequent cells on a second row, we need to use the **[`<tr>`](/en-US/docs/Web/HTML/Reference/Elements/tr)** element ('tr' stands for 'table row').

[81] Let's investigate this now.

[82] 1.

[83] Place the four cells you've already created inside `<tr>` tags, like so:

[84] ```html
   <tr>
     <td>Hi, I'm your first cell.</td>
     <td>I'm your second cell.</td>
     <td>I'm your third cell.</td>
     <td>I'm your fourth cell.</td>
   </tr>
   ```

[85] 2.

[86] Now you've made one row, have a go at making one or two more — each row needs to be wrapped in an additional `<tr>` element, with each cell contained in a `<td>`.

[87] <details>
<summary>Click here to show the solution</summary>

[88] Your finished HTML should look something like this:

[89] ```html
<table>
  <tr>
    <td>Hi, I'm your first cell.</td>
    <td>I'm your second cell.</td>
    <td>I'm your third cell.</td>
    <td>I'm your fourth cell.</td>
  </tr>

[90] <tr>
    <td>Second row, first cell.</td>
    <td>Cell 2.</td>
    <td>Cell 3.</td>
    <td>Cell 4.</td>
  </tr>
</table>
```

[91] You can also find this code on GitHub at [simple-table.html](https://github.com/mdn/learning-area/blob/main/html/tables/basic/simple-table.html) ([see it running live also](https://mdn.github.io/learning-area/html/tables/basic/simple-table.html)).

[92] </details>

## Grouping X

  G1: sentences 1 (1 sentence)
  G2: sentences 2–4 (3 sentences)
  G3: sentences 5–6 (2 sentences)
  G4: sentences 7–8 (2 sentences)
  G5: sentences 9–10 (2 sentences)
  G6: sentences 11–12 (2 sentences)
  G7: sentences 13–14 (2 sentences)
  G8: sentences 15–16 (2 sentences)
  G9: sentences 17 (1 sentence)
  G10: sentences 18 (1 sentence)
  G11: sentences 19–21 (3 sentences)
  G12: sentences 22 (1 sentence)
  G13: sentences 23 (1 sentence)
  G14: sentences 24 (1 sentence)
  G15: sentences 25 (1 sentence)
  G16: sentences 26 (1 sentence)
  G17: sentences 27–29 (3 sentences)
  G18: sentences 30–31 (2 sentences)
  G19: sentences 32–34 (3 sentences)
  G20: sentences 35–36 (2 sentences)
  G21: sentences 37 (1 sentence)
  G22: sentences 38 (1 sentence)
  G23: sentences 39 (1 sentence)
  G24: sentences 40–42 (3 sentences)
  G25: sentences 43–44 (2 sentences)
  G26: sentences 45–46 (2 sentences)
  G27: sentences 47 (1 sentence)
  G28: sentences 48–50 (3 sentences)
  G29: sentences 51–52 (2 sentences)
  G30: sentences 53–54 (2 sentences)
  G31: sentences 55–57 (3 sentences)
  G32: sentences 58–60 (3 sentences)
  G33: sentences 61 (1 sentence)
  G34: sentences 62 (1 sentence)
  G35: sentences 63–65 (3 sentences)
  G36: sentences 66–68 (3 sentences)
  G37: sentences 69–71 (3 sentences)
  G38: sentences 72 (1 sentence)
  G39: sentences 73–75 (3 sentences)
  G40: sentences 76 (1 sentence)
  G41: sentences 77–79 (3 sentences)
  G42: sentences 80–81 (2 sentences)
  G43: sentences 82–83 (2 sentences)
  G44: sentences 84 (1 sentence)
  G45: sentences 85–86 (2 sentences)
  G46: sentences 87 (1 sentence)
  G47: sentences 88 (1 sentence)
  G48: sentences 89 (1 sentence)
  G49: sentences 90 (1 sentence)
  G50: sentences 91 (1 sentence)
  G51: sentences 92 (1 sentence)

## Grouping Y

  G1: sentences 1 (1 sentence)
  G2: sentences 2–4 (3 sentences)
  G3: sentences 5–6 (2 sentences)
  G4: sentences 7–8 (2 sentences)
  G5: sentences 9–10 (2 sentences)
  G6: sentences 11–12 (2 sentences)
  G7: sentences 13–14 (2 sentences)
  G8: sentences 15–16 (2 sentences)
  G9: sentences 17 (1 sentence)
  G10: sentences 18 (1 sentence)
  G11: sentences 19–20 (2 sentences)
  G12: sentences 21–22 (2 sentences)
  G13: sentences 23 (1 sentence)
  G14: sentences 24 (1 sentence)
  G15: sentences 25–26 (2 sentences)
  G16: sentences 27–36 (10 sentences)
  G17: sentences 37 (1 sentence)
  G18: sentences 38 (1 sentence)
  G19: sentences 39 (1 sentence)
  G20: sentences 40–41 (2 sentences)
  G21: sentences 42–43 (2 sentences)
  G22: sentences 44–46 (3 sentences)
  G23: sentences 47 (1 sentence)
  G24: sentences 48 (1 sentence)
  G25: sentences 49–50 (2 sentences)
  G26: sentences 51–52 (2 sentences)
  G27: sentences 53–54 (2 sentences)
  G28: sentences 55–56 (2 sentences)
  G29: sentences 57–58 (2 sentences)
  G30: sentences 59–60 (2 sentences)
  G31: sentences 61 (1 sentence)
  G32: sentences 62 (1 sentence)
  G33: sentences 63–65 (3 sentences)
  G34: sentences 66–68 (3 sentences)
  G35: sentences 69–71 (3 sentences)
  G36: sentences 72 (1 sentence)
  G37: sentences 73–75 (3 sentences)
  G38: sentences 76 (1 sentence)
  G39: sentences 77–79 (3 sentences)
  G40: sentences 80–81 (2 sentences)
  G41: sentences 82–83 (2 sentences)
  G42: sentences 84 (1 sentence)
  G43: sentences 85–86 (2 sentences)
  G44: sentences 87–88 (2 sentences)
  G45: sentences 89–90 (2 sentences)
  G46: sentences 91 (1 sentence)
  G47: sentences 92 (1 sentence)
