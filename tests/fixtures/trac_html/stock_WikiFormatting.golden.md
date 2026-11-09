# [WikiFormatting](https://trac.example.org/trac_mcp_server/wiki/WikiFormatting)

Wiki markup is a core feature in Trac, tightly integrating all the other parts of Trac into a flexible and unified whole.

Trac has a powerful wiki rendering engine that implements a subset of the commands from other popular Wikis, such as [MoinMoin](https://moinmo.in/) and [WikiCreole](https://trac.edgewall.org/intertrac/WikiCreole).

The sections below provide an overview for the most common syntax, each link in the *Category* column leads you to the more detailed explanation later in this page.

The following wiki pages present the advanced features of the Trac wiki markup in more depth:

- [TracLinks](https://trac.example.org/trac_mcp_server/wiki/TracLinks) covers all the possible ways to refer to any Trac resource or parts thereof.
- [WikiPageNames](https://trac.example.org/trac_mcp_server/wiki/WikiPageNames) covers the various names a wiki page can take, whether in [CamelCase](https://trac.example.org/trac_mcp_server/wiki/CamelCase) or not.
- [WikiMacros](https://trac.example.org/trac_mcp_server/wiki/WikiMacros) lists the macros available for generating dynamic content.
- [WikiProcessors](https://trac.example.org/trac_mcp_server/wiki/WikiProcessors) and [WikiHtml](https://trac.example.org/trac_mcp_server/wiki/WikiHtml) details how parts of the wiki text can be processed in special ways.
- [AdvancedWikiOperations](https://trac.edgewall.org/intertrac/wiki%3ATracDev/Proposals/AdvancedWikiOperations) provides some operations in uncommon or administrative scenarios.

## Common wiki markup

<table>
<tr><th> <strong>Category</strong> </th><th> <strong>Wiki Markup</strong> </th><th> <strong>Display</strong>
</th></tr><tr>
<th rowspan="3"><p>
<a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#FontStyles">Font Styles</a>
</p>
</th><td> <code>'''bold'''</code>, <code>''italic''</code>, <code>'''''Wikipedia style'''''</code>
</td><td> <strong>bold</strong>, <em>italic</em>, <strong><em>Wikipedia style</em></strong>
</td></tr><tr><td> <code>**bold**</code>, <code>//italic//</code>, <code>**//!WikiCreole style//**</code>
</td><td> <strong>bold</strong>, <em>italic</em>, <strong><em>WikiCreole style</em></strong>
</td></tr><tr><td> <code>`monospaced (''other markup ignored'')`</code>
</td><td> <code>monospaced (''other markup ignored'')</code>
</td></tr><tr>
<th> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Headings">Headings</a>
</th><td><pre>== Level 2
=== Level 3 ^([#hn note])^
</pre></td><td style="padding-left: 2em"><h2 id="Level2">Level 2</h2>
<h3 id="Level3note">Level 3 <sup>(<a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#hn">note</a>)</sup></h3>
</td></tr><tr>
<th> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Paragraphs">Paragraphs</a>
</th><td><pre>First paragraph
on multiple lines.
&#32;
Second paragraph.
</pre></td><td><p>
First paragraph
on multiple lines.
</p>
<p>
Second paragraph.
</p>
</td></tr><tr>
<th> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Lists">Lists</a>
</th><td><pre>* bullet list
  on multiple lines
  1. nested list
    a. different numbering
       styles
</pre></td><td><ul><li>bullet list
on multiple lines
<ol><li>nested list
<ol><li>different numbering
styles
</li></ol></li></ol></li></ul></td></tr><tr>
<th><p>
<a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#DefinitionLists">Definition Lists</a>
</p>
</th><td><pre> term:: definition on
        multiple lines
</pre></td><td><dl><dt>term</dt><dd>definition on
multiple lines
</dd></dl>
</td></tr><tr>
<th> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#PreformattedText">Preformatted Text</a>
</th><td><pre>{{{
multiple lines, ''no wiki'',
      white space respected
}}}
</pre></td><td><pre>multiple lines, ''no wiki'',
      white space respected
</pre></td></tr><tr>
<th> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Blockquotes">Blockquotes</a>
</th><td><pre>  if there's some leading
  space the text is quoted
</pre></td><td><blockquote>
<p>
if there's some leading
space the text is quoted
</p>
</blockquote>
</td></tr><tr>
<th> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#DiscussionCitations">Discussion Citations</a>
</th><td><pre>&gt;&gt; ... (I said)
&gt; (he replied)
</pre></td><td><blockquote>
<blockquote>
<p>
... (I said)
</p>
</blockquote>
<p>
(he replied)
</p>
</blockquote>
</td></tr><tr>
<th> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Tables">Tables</a>
</th><td><pre>||= Table Header =|| Cell ||
||||  (details below)  ||
</pre></td><td><table>
<tr><th> Table Header </th><td> Cell 
</td></tr><tr><td colspan="2">  (details below)  
</td></tr></table>
</td></tr><tr>
<th rowspan="2"><p>
<a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Links">Links</a>
</p>
</th><td> <code>https://trac.edgewall.org</code>
</td><td> <a href="https://trac.edgewall.org">https://trac.edgewall.org</a>
</td></tr><tr><td> <code>WikiFormatting (CamelCase)</code>
</td><td> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting">WikiFormatting</a> (<a href="https://trac.example.org/trac_mcp_server/wiki/CamelCase">CamelCase</a>) 
</td></tr><tr>
<th rowspan="5"><p>
<a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#TracLinks">TracLinks</a>
</p>
</th><td> <code>wiki:WikiFormatting</code>, <code>wiki:"WikiFormatting"</code>
</td><td> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting">wiki:WikiFormatting</a>, <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting">wiki:"WikiFormatting"</a>
</td></tr><tr><td> <code>#1 (ticket)</code>, <code>[1] (changeset)</code>, <code>{1} (report)</code>
</td><td> <a href="https://trac.example.org/trac_mcp_server/ticket/1">#1</a> (ticket), [1] (changeset), <a href="https://trac.example.org/trac_mcp_server/report/1">{1}</a> (report) 
</td></tr><tr><td> <code>ticket:1, ticket:1#comment:1, comment:1:ticket:1</code>
</td><td> <a href="https://trac.example.org/trac_mcp_server/ticket/1">ticket:1</a>, <a href="https://trac.example.org/trac_mcp_server/ticket/1#comment:1">ticket:1#comment:1</a>, <a href="https://trac.example.org/trac_mcp_server/ticket/1#comment:1">comment:1:ticket:1</a>
</td></tr><tr><td> <code>Ticket [ticket:1]</code>, <code>[ticket:1 ticket one]</code>
</td><td> Ticket <a href="https://trac.example.org/trac_mcp_server/ticket/1">1</a>, <a href="https://trac.example.org/trac_mcp_server/ticket/1">ticket one</a>
</td></tr><tr><td> <code>Ticket [[ticket:1]]</code>, <code>[[ticket:1|ticket one]]</code>
</td><td> Ticket <a href="https://trac.example.org/trac_mcp_server/ticket/1">1</a>, <a href="https://trac.example.org/trac_mcp_server/ticket/1">ticket one</a>
</td></tr><tr>
<th rowspan="2"><p>
<a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#SettingAnchors">Setting Anchors</a>
</p>
</th><td> <code>[=#point1 (1)] First...</code>
</td><td>  First... 
</td></tr><tr><td> <code>see [#point1 (1)]</code>
</td><td> see <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#point1">(1)</a>
</td></tr><tr>
<th rowspan="3"><p>
<a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Escaping">Escaping Markup</a>
</p>
</th><td> <code>!'' doubled quotes</code>
</td><td> '' doubled quotes 
</td></tr><tr><td> <code>!wiki:WikiFormatting</code>, <code>!WikiFormatting</code>
</td><td> wiki:WikiFormatting, WikiFormatting 
</td></tr><tr><td> <code>`{{{-}}}` triple curly brackets</code>
</td><td> <code>{{{-}}}</code> triple curly brackets 
</td></tr><tr>
<th> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Images">Images</a> </th><td> <code>[[Image(</code><em>link</em><code>)]]</code> </td><td> <a href="https://trac.example.org/trac_mcp_server/chrome/site/../common/trac_logo_mini.png" style="padding:0; border:none"><img alt="trac_logo_mini.png" src="https://trac.example.org/trac_mcp_server/chrome/site/../common/trac_logo_mini.png" title="trac_logo_mini.png"/></a>
</td></tr><tr>
<th rowspan="2"><p>
<a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Macros">Macros</a>
</p>
</th><td> <code>[[MacroList(*)]]</code> </td><td> <em>(short list of all available macros)</em>
</td></tr><tr><td> <code>[[Image?]]</code> </td><td> <em>(help for the Image macro)</em>
</td></tr><tr>
<th> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Processors">Processors</a>
</th><td><pre>{{{#!div style="font-size: 80%"
Code highlighting:
  {{{#!python
  hello = lambda: "world"
  }}}
}}}
</pre></td><td style="padding-left: 2em"><div class="wikipage" style="font-size: 80%"><p>
Code highlighting:
</p>
<pre><code class="language-python">hello = lambda: "world"</code></pre></div></td></tr><tr>
<th> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Comments">Comments</a>
</th><td><pre>{{{#!comment
Note to Editors: ...
}}}
</pre></td><td> <em>(comment is shown in edit mode only)</em>
</td></tr><tr>
<th> <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Miscellaneous">Miscellaneous</a>
</th><td><pre>Line [[br]] break
Line \\ break
----
</pre></td><td style="padding-left: 2em"><p>
Line <br/> break
Line <br/> break
</p>
<hr/>
</td></tr></table>

## Font Styles

The Trac wiki supports the following font styles:

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre> * '''bold''',
   ''' triple quotes !'''
   can be bold too if prefixed by ! ''',
 * ''italic''
 * '''''bold italic''''' or ''italic and
   ''' italic bold ''' ''
 * __underline__
 * {{{monospace}}} or `monospace`
   (hence `{{{` or {{{`}}} quoting)
 * ~~strike-through~~
 * ^superscript^
 * ,,subscript,,
 * **also bold**, //italic as well//,
   and **'' bold italic **'' //(since 0.12)//
 * [[span(style=color: #FF0000, a red text )]]
</pre></td><td><ul><li><strong>bold</strong>,
<strong> triple quotes '''
can be bold too if prefixed by ! </strong>,
</li><li><em>italic</em>
</li><li><strong><em>bold italic</em></strong> or <em>italic and
<strong> italic bold </strong> </em>
</li><li><span class="underline">underline</span>
</li><li><code>monospace</code> or <code>monospace</code>
(hence <code>{{{</code> or <code>`</code> quoting)
</li><li><del>strike-through</del>
</li><li><sup>superscript</sup>
</li><li><sub>subscript</sub>
</li><li><strong>also bold</strong>, <em>italic as well</em>,
and <strong><em> bold italic </em></strong><em></em> <em>(since 0.12)</em>
</li><li><span style="color: #FF0000">a red text</span>
</li></ul></td></tr></table>

Notes:

- `{{{...}}}` and `` `...` `` commands not only select a monospace font, but also treat their content as verbatim text, meaning that no further wiki processing is done on this text.
- `!`  tells wiki parser to not take the following characters as wiki format, so pay attention to put a space after `!`, e.g. when ending bold.
- all the font styles marks have to be used in opening/closing pairs,
  and they must nest properly; in particular, an `''` italic can't be paired
  with a `//` one, and `'''` can't be paired with `**`.

## Headings

You can create a heading by starting a line with one up to six *equal* characters (`=`) followed by a single space and the headline text.

The headline text can be followed by the same number of `=` characters, but this is not mandatory. That is, `=== Section3 ===` is identical to `=== Section3`.

Finally, the heading might optionally be followed by an explicit id. If not, an implicit but nevertheless readable id will be generated.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>= Heading =
== Subheading
=== About ''this'' ===
=== Explicit id === #using-explicit-id-in-heading
== Subheading #sub2
</pre></td><td style="padding: 1em"><div class="wikipage"><h1 id="Heading">Heading</h1>
<h2 id="Subheading">Subheading</h2>
<h3 id="Aboutthis">About <em>this</em></h3>
<h3 id="using-explicit-id-in-heading">Explicit id</h3>
<h2 id="sub2">Subheading</h2>
</div></td></tr></table>

## Paragraphs

A new text paragraph is created whenever two blocks of text are separated by one or more empty lines.

A forced line break can also be inserted, using:

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>Line 1[[BR]]Line 2
</pre><pre>Paragraph
one
&#32;
Paragraph
two
</pre></td><td><blockquote>
<p>
Line 1<br/>Line 2
</p>
</blockquote>
<blockquote>
<p>
Paragraph
one
</p>
</blockquote>
<blockquote>
<p>
Paragraph
two
</p>
</blockquote>
</td></tr></table>

## Lists

The wiki supports both ordered/numbered and unordered lists.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre> * Item 1
   * Item 1.1
      * Item 1.1.1
      * Item 1.1.2
      * Item 1.1.3
   * Item 1.2
 * Item 2
- items can start at the beginning of a line
  and they can span multiple lines
  - be careful though to continue the line
  with the appropriate indentation, otherwise
that will start a new paragraph...
&#32;
 1. Item 1
   a. Item 1.a
   a. Item 1.b
      i. Item 1.b.i
      i. Item 1.b.ii
 1. Item 2
And numbered lists can also be restarted
with an explicit number:
 3. Item 3
</pre></td><td><ul><li>Item 1
<ul><li>Item 1.1
<ul><li>Item 1.1.1
</li><li>Item 1.1.2
</li><li>Item 1.1.3
</li></ul></li><li>Item 1.2
</li></ul></li><li>Item 2
</li></ul><ul><li>items can start at the beginning of a line
and they can span multiple lines
<ul><li>be careful though to continue the line
with the appropriate indentation, otherwise
</li></ul></li></ul><p>
that will start a new paragraph...
</p>
<ol><li>Item 1
<ol><li>Item 1.a
</li><li>Item 1.b
<ol><li>Item 1.b.i
</li><li>Item 1.b.ii
</li></ol></li></ol></li><li>Item 2
</li></ol><p>
And numbered lists can also be restarted with an explicit number:
</p>
<ol start="3"><li>Item 3
</li></ol></td></tr></table>

## Definition Lists

The wiki also supports definition lists.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre> llama::
   some kind of mammal, with hair
 ppython::
   some kind of reptile, without hair
   (can you spot the typo?)
</pre></td><td><dl><dt>llama</dt><dd>
some kind of mammal, with hair
</dd><dt>ppython</dt><dd>
some kind of reptile, without hair
(can you spot the typo?)
</dd></dl>
</td></tr></table>

Note that you need a space in front of the defined term.

## Preformatted Text

Block containing preformatted text are suitable for source code snippets, notes and examples. Use three *curly braces* wrapped around the text to define a block quote. The curly braces need to be on a separate line.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>{{{
def HelloWorld():
    print '''Hello World'''
}}}
</pre></td><td><pre>def HelloWorld():
    print '''Hello World'''
</pre></td></tr></table>

Note that this kind of block is also used for selecting lines that should be processed through [WikiProcessors](https://trac.example.org/trac_mcp_server/wiki/WikiProcessors).

## Blockquotes

In order to mark a paragraph as blockquote, indent that paragraph with two spaces.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>Paragraph
  This text is a quote from someone else.
</pre></td><td><p>
Paragraph
</p>
<blockquote>
<p>
This text is a quote from someone else.
</p>
</blockquote>
</td></tr></table>

## Discussion Citations

To delineate a citation in an ongoing discussion thread, such as the ticket comment area, email-like citation marks (`>`, `>>`, etc.) may be used.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>&gt;&gt; Someone's original text
&gt; Someone else's reply text
&gt;  - which can be any kind of Wiki markup
My reply text
</pre></td><td><blockquote>
<blockquote>
<p>
Someone's original text
</p>
</blockquote>
<p>
Someone else's reply text
</p>
<ul><li>which can be any kind of Wiki markup
</li></ul></blockquote>
<p>
My reply text
</p>
</td></tr></table>

## Tables

### Simple Tables

Simple tables can be created:

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>||Cell 1||Cell 2||Cell 3||
||Cell 4||Cell 5||Cell 6||
</pre></td><td style="padding: 2em"><table>
<tr><td>Cell 1</td><td>Cell 2</td><td>Cell 3
</td></tr><tr><td>Cell 4</td><td>Cell 5</td><td>Cell 6
</td></tr></table>
</td></tr></table>

Cell headings can be specified by wrapping the content in a pair of `=` characters.
Note that the `=` characters have to stick to the cell separators:

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>||        ||= stable =||= latest =||
||= 0.10 =||  0.10.5  || 0.10.6dev||
||= 0.11 =||  0.11.6  || 0.11.7dev||
</pre></td><td style="padding: 2em"><table>
<tr><td> </td><th> stable </th><th> latest 
</th></tr><tr><th> 0.10 </th><td>  0.10.5  </td><td> 0.10.6dev
</td></tr><tr><th> 0.11 </th><td>  0.11.6  </td><td> 0.11.7dev
</td></tr></table>
</td></tr></table>

Finally, specifying an empty cell means that the next non empty cell will span the empty cells:

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>|| 1 || 2 || 3 ||
|||| 1-2 || 3 ||
|| 1 |||| 2-3 ||
|||||| 1-2-3 ||
</pre></td><td style="padding: 2em"><table>
<tr><td> 1 </td><td> 2 </td><td> 3 
</td></tr><tr><td colspan="2"> 1-2 </td><td> 3 
</td></tr><tr><td> 1 </td><td colspan="2"> 2-3 
</td></tr><tr><td colspan="3"> 1-2-3 
</td></tr></table>
</td></tr></table>

Note that if the content of a cell sticks to one side of the cell and only one, then the text will be aligned on that side:

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>||=Text =||= Numbers =||
||left align    ||        1.0||
||  center      ||        4.5||
||      right align||     4.5||
|| default alignment ||   2.5||
||default||         2.5||
||  default ||      2.5||
|| default ||       2.5||
</pre></td><td style="padding: 2em"><table>
<tr><th>Text </th><th> Numbers 
</th></tr><tr><td>left align    </td><td>        1.0
</td></tr><tr><td>  center      </td><td>        4.5
</td></tr><tr><td>      right align</td><td>     4.5
</td></tr><tr><td> default alignment </td><td>   2.5
</td></tr><tr><td>default</td><td>         2.5
</td></tr><tr><td>  default </td><td>      2.5
</td></tr><tr><td> default </td><td>       2.5
</td></tr></table>
</td></tr></table>

If contrary to the example above, the cells in your table contain more text, it might be convenient to spread a table row over multiple lines of markup. The `\` character placed at the end of a line after a cell separator tells Trac to not start a new row for the cells on the next line:

<table>
<tr><th> Wiki Markup 
</th></tr><tr><td><pre>|| this is column 1 [https://trac.edgewall.org/newticket new ticket] || \
|| this is column 2 [https://trac.edgewall.org/roadmap the road ahead] || \
|| that's column 3 and last one ||
</pre></td></tr><tr>
<th> Display 
</th></tr><tr><td style="padding: 2em"><table>
<tr><td> this is column 1 <a href="https://trac.edgewall.org/newticket">new ticket</a>
</td><td> this is column 2 <a href="https://trac.edgewall.org/roadmap">the road ahead</a>
</td><td> that's column 3 and last one 
</td></tr></table>
</td></tr></table>

### Complex Tables

If the possibilities offered by the simple pipe-based markup (`||`) for tables described above are not enough for your needs, you can create more elaborate tables by using [WikiProcessor based tables](https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Processors-example-tables).

## Links

Hyperlinks are automatically created for [WikiPageNames](https://trac.example.org/trac_mcp_server/wiki/WikiPageNames) and URLs. WikiPageLinks can be disabled by prepending an exclamation mark (`!`), such as `!WikiPageLink`.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>TitleIndex, https://www.edgewall.com/, !NotAlink
</pre></td><td><p>
<a href="https://trac.example.org/trac_mcp_server/wiki/TitleIndex">TitleIndex</a>, <a href="https://www.edgewall.com/">https://www.edgewall.com/</a>, NotAlink
</p>
</td></tr></table>

Links can be given a more descriptive title by writing the link followed by a space and a title and all this inside square brackets.
If the descriptive title is omitted, then the explicit prefix is discarded, unless the link is an external link. This can be useful for wiki pages not adhering to the [WikiPageNames](https://trac.example.org/trac_mcp_server/wiki/WikiPageNames) convention.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre> * [https://www.edgewall.com Edgewall Software]
 * [wiki:TitleIndex Title Index]
 * [wiki:TitleIndex]
 * [wiki:ISO9000]
</pre></td><td><ul><li><a href="https://www.edgewall.com">Edgewall Software</a>
</li><li><a href="https://trac.example.org/trac_mcp_server/wiki/TitleIndex">Title Index</a>
</li><li><a href="https://trac.example.org/trac_mcp_server/wiki/TitleIndex">TitleIndex</a>
</li><li>ISO9000
</li></ul></td></tr></table>

Following the [WikiCreole](https://trac.edgewall.org/intertrac/WikiCreole) trend, the descriptive title can also be specified by writing the link followed by a pipe (`|`) and a title and all this inside *double* square brackets.

<table>
<tr><td><pre> * [[https://www.edgewall.com|Edgewall Software]]
 * [[wiki:TitleIndex|Title Index]]
   or even [[TitleIndex|Title Index]]
 * [[wiki:TitleIndex]]
   ''' but not ![[TitleIndex]]! '''
 * [[ISO9000]]
</pre></td><td><ul><li><a href="https://www.edgewall.com">Edgewall Software</a>
</li><li><a href="https://trac.example.org/trac_mcp_server/wiki/TitleIndex">Title Index</a>
or even <a href="https://trac.example.org/trac_mcp_server/wiki/TitleIndex">Title Index</a>
</li><li><a href="https://trac.example.org/trac_mcp_server/wiki/TitleIndex">TitleIndex</a>
<strong> but not [[TitleIndex]]! </strong>
</li><li>ISO9000
</li></ul></td></tr></table>

**Note**: the [WikiCreole](https://trac.edgewall.org/intertrac/WikiCreole) style for links is quick to type and certainly looks familiar as it is the one used on Wikipedia and in many other wikis. Unfortunately it conflicts with the syntax for [macros](https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Macros).
So in the rare case when you need to refer to a page which is named after a macro (typical examples being [TitleIndex](https://trac.example.org/trac_mcp_server/wiki/TitleIndex), [InterTrac](https://trac.example.org/trac_mcp_server/wiki/InterTrac) and [InterWiki](https://trac.example.org/trac_mcp_server/wiki/InterWiki)), by writing `[[TitleIndex]]` you will actually call the macro instead of linking to the page.

## Trac Links

Wiki pages can link directly to other parts of the Trac system. Pages can refer to tickets, reports, changesets, milestones, source files and other Wiki pages using the following notations:

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre> * Tickets: #1 or ticket:1
 * Reports: {1} or report:1
 * Changesets: r1, [1] or changeset:1
 * ...
 * targeting other Trac instances,
   so called InterTrac links:
   - Tickets: #Trac1 or Trac:ticket:1
   - Changesets: [Trac1] or Trac:changeset:1
</pre></td><td><ul><li>Tickets: <a href="https://trac.example.org/trac_mcp_server/ticket/1">#1</a> or <a href="https://trac.example.org/trac_mcp_server/ticket/1">ticket:1</a>
</li><li>Reports: <a href="https://trac.example.org/trac_mcp_server/report/1">{1}</a> or <a href="https://trac.example.org/trac_mcp_server/report/1">report:1</a>
</li><li>Changesets: r1, [1] or changeset:1
</li><li>...
</li><li>targeting other Trac instances,
so called <a href="https://trac.example.org/trac_mcp_server/wiki/InterTrac">InterTrac</a> links:
<ul><li>Tickets: <a href="https://trac.edgewall.org/intertrac/ticket%3A1">#Trac1</a> or <a href="https://trac.edgewall.org/intertrac/ticket%3A1">Trac:ticket:1</a>
</li><li>Changesets: <a href="https://trac.edgewall.org/intertrac/changeset%3A1">[Trac1]</a> or <a href="https://trac.edgewall.org/intertrac/changeset%3A1">Trac:changeset:1</a>
</li></ul></li></ul></td></tr></table>

There are many more flavors of Trac links, see [TracLinks](https://trac.example.org/trac_mcp_server/wiki/TracLinks) for more in-depth information and a reference for all the default link resolvers.

## Setting Anchors

An anchor, or more correctly speaking, an [anchor name](https://www.w3.org/TR/REC-html40/struct/links.html#h-12.2.1) can be added explicitly at any place in the Wiki page, to uniquely identify a position in the document:

```
[=#point1]
```

This syntax was chosen to match the format for explicitly naming the header id [documented above](https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Headings):

```
== Long title == #title
```

It is also very close to the syntax for the corresponding link to that anchor:

```
[#point1]
```

Optionally, a label can be given to the anchor:

```
[=#point1 '''Point 1''']
```

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr>
<td><pre>[#point2 jump to the second point]
&#32;
...
&#32;
Point2:  [=#point2] Jump here
</pre></td><td><blockquote>
<p>
<a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#point2">jump to the second point</a>
</p>
</blockquote>
<blockquote>
<p>
...
</p>
</blockquote>
<blockquote>
<p>
Point2:   Jump here
</p>
</blockquote>
</td></tr></table>

For more complex anchors (eg when a custom title is wanted), you can use the Span macro: `[[span(id=point2, class=wikianchor, title=Point 2, ^(2)^)]]`.

## Escaping Links, [WikiPageNames](https://trac.example.org/trac_mcp_server/wiki/WikiPageNames) and other Markup

You may avoid making hyperlinks out of [TracLinks](https://trac.example.org/trac_mcp_server/wiki/TracLinks) by preceding an expression with a single exclamation mark (`!`).

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre> !NoHyperLink
 !#42 is not a link
</pre><pre>Various forms of escaping for list markup:
 ^^- escaped minus sign \\
 ^^1. escaped number  \\
 ^^* escaped asterisk sign
</pre></td><td><blockquote>
<p>
NoHyperLink
#42 is not a link
</p>
</blockquote>
<p>
Various forms of escaping for list markup:
</p>
<blockquote>
<p>
<sup></sup>- escaped minus sign <br/>
<sup></sup>1. escaped number  <br/>
<sup></sup>* escaped asterisk sign
</p>
</blockquote>
</td></tr></table>

## Images

Urls ending with `.png`, `.gif` or `.jpg` are no longer automatically interpreted as image links, and converted to `<img>` tags.

You now have to use the \[\[Image\]\] macro. The simplest way to include an image is to upload it as attachment to the current page, and put the filename in a macro call like `[[Image(picture.gif)]]`.

In addition to the current page, it is possible to refer to other resources:

- `[[Image(wiki:WikiFormatting:picture.gif)]]` (referring to attachment on another page)
- `[[Image(ticket:1:picture.gif)]]` (file attached to a ticket)
- `[[Image(htdocs:picture.gif)]]` (referring to a file inside the [environment](https://trac.example.org/trac_mcp_server/wiki/TracEnvironment) `htdocs` directory)
- `[[Image(source:/trunk/trac/htdocs/trac_logo_mini.png)]]` (a file in repository)

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>[[Image(htdocs:../common/trac_logo_mini.png)]]
</pre></td><td><p>
<a href="https://trac.example.org/trac_mcp_server/chrome/site/../common/trac_logo_mini.png" style="padding:0; border:none"><img alt="trac_logo_mini.png" src="https://trac.example.org/trac_mcp_server/chrome/site/../common/trac_logo_mini.png" title="trac_logo_mini.png"/></a>
</p>
</td></tr></table>

See [WikiMacros](https://trac.example.org/trac_mcp_server/wiki/WikiMacros) for further documentation on the `[[Image()]]` macro, which has several useful options (`title=`, `link=`, etc.)

## Macros

Macros are *custom functions* that insert dynamic content in a page.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>[[RecentChanges(Trac,3)]]
</pre></td><td style="padding-left: 2em"><p>
</p><div class="wikipage"><h3>11/08/26</h3><ul><li><a href="https://trac.example.org/trac_mcp_server/wiki/TracConvertLiveTest">TracConvertLiveTest</a><small> (<a href="https://trac.example.org/trac_mcp_server/wiki/TracConvertLiveTest?action=diff&amp;version=469">diff</a>)</small>
</li></ul><h3>02/14/26</h3><ul><li><a href="https://trac.example.org/trac_mcp_server/wiki/TracWorkflow">TracWorkflow</a>
</li><li><a href="https://trac.example.org/trac_mcp_server/wiki/TracWiki">TracWiki</a>
</li></ul></div><p>
</p>
</td></tr></table>

See [WikiMacros](https://trac.example.org/trac_mcp_server/wiki/WikiMacros) for more information, and a list of installed macros.

The detailed help for a specific macro can also be obtained more directly by appending a `?` to the macro name.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>[[MacroList?]]
</pre></td><td style="padding-left: 2em"><p>
</p><div class="trac-macrolist"><h3 id="MacroList-macro"><code>[[MacroList]]</code></h3><p>
Display a list of all installed Wiki macros, including documentation if
available.
</p>
<p>
Optionally, the name of a specific macro can be provided as an argument. In
that case, only the documentation for that macro will be rendered.
</p>
<p>
Note that this macro will not be able to display the documentation of
macros if the <code>PythonOptimize</code> option is enabled for mod_python!
</p>
</div><p>
</p>
</td></tr></table>

## Processors

Trac supports alternative markup formats using [WikiProcessors](https://trac.example.org/trac_mcp_server/wiki/WikiProcessors). For example, processors are used to write pages in
[reStructuredText](https://trac.example.org/trac_mcp_server/wiki/WikiRestructuredText) or [HTML](https://trac.example.org/trac_mcp_server/wiki/WikiHtml).

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr>
<td colspan="2" style="border: 0px; font-size: 90%"><blockquote>
<p>
 HTML
</p>
</blockquote>
</td></tr><tr>
<td style="border: 0px"><pre>{{{#!html
&lt;h1 style="text-align: right; color: blue"&gt;
 HTML Test
&lt;/h1&gt;
}}}
</pre></td><td style="border: 0px" valign="top"><h1 style="text-align: right; color: blue">HTML Test</h1>
</td></tr><tr>
<td colspan="2" style="border: 0px; font-size: 90%"><blockquote>
<p>
 Code Highlighting
</p>
</blockquote>
</td></tr><tr>
<td style="border: 0px"><pre>{{{#!python
class Test:
&#32;
    def __init__(self):
        print "Hello World"
if __name__ == '__main__':
   Test()
}}}
</pre></td><td style="border: 0px" valign="top"><pre><code class="language-python">class Test:
    def __init__(self):
        print "Hello World"
if __name__ == '__main__':
   Test()</code></pre></td></tr><tr>
<td colspan="2" style="border: 0px; font-size: 90%"><blockquote>
<p>
 Complex Tables
</p>
</blockquote>
</td></tr><tr>
<td style="border: 0px"><pre>{{{#!th rowspan=4 align=justify
With the `#td` and `#th` processors,
table cells can contain any content:
}}}
|----------------
{{{#!td
  - lists
  - embedded tables
  - simple multiline content
}}}
|----------------
{{{#!td
As processors can be easily nested,
so can be tables:
  {{{#!th
  Example:
  }}}
  {{{#!td style="background: #eef"
  || must be at the third level now... ||
  }}}
}}}
|----------------
{{{#!td
Even when you don't have complex markup,
this form of table cells can be convenient
to write content on multiple lines.
}}}
</pre></td><td style="border: 0px" valign="top"><table>
<tr><th rowspan="4"><p>
With the <code>#td</code> and <code>#th</code> processors,
table cells can contain any content:
</p>
</th></tr><tr>
<td><ul><li>lists
</li><li>embedded tables
</li><li>simple multiline content
</li></ul></td></tr><tr>
<td><p>
As processors can be easily nested,
so can be tables:
</p>
<table>
<tr><th><p>
Example:
</p>
</th><td style="background: #eef"><table>
<tr><td> must be at the third level now... 
</td></tr></table>
</td></tr></table>
</td></tr><tr>
<td><p>
Even when you don't have complex markup,
this form of table cells can be convenient
to write content on multiple lines.
</p>
</td></tr></table>
</td></tr></table>

See [WikiProcessors](https://trac.example.org/trac_mcp_server/wiki/WikiProcessors) for more information.

## Comments

Comments can be added to the plain text. These will not be rendered and will not display in any other format than plain text.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre>Nothing to
{{{#!comment
Your comment for editors here
}}}
see.
</pre></td><td><blockquote>
<p>
Nothing to
</p>
<p>
see.
</p>
</blockquote>
</td></tr></table>

## Miscellaneous

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><blockquote>
<p>
Horizontal line:
</p>
<pre>Four or more dashes will be replaced
by a horizontal line (&lt;HR&gt;)
----
See?
</pre></blockquote>
</td><td><p>
Four or more dashes will be replaced
by a horizontal line (&lt;HR&gt;)
</p>
<hr/>
<p>
See?
</p>
</td></tr><tr>
<td><blockquote>
<p>
Two examples of line breaks:
</p>
<pre>"macro" style [[BR]] line break
</pre><p>
or:
</p>
<pre>!WikiCreole style \\ line\\break
</pre></blockquote>
</td><td><p>
"macro" style <br/> line break
</p>
<p>
WikiCreole style <br/> line<br/>break
</p>
</td></tr><tr>
</tr></table>
