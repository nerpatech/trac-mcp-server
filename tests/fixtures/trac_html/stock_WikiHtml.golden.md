# Using HTML in Wiki Text

Trac supports the display of HTML in any wiki context, by using the `#!html` [WikiProcessor](https://trac.example.org/trac_mcp_server/wiki/WikiProcessors).

However, this HTML has to be [well-formed](https://en.wikipedia.org/wiki/Well-formed_element).
In particular, you can't insert a start tag in an `#!html` block, resume normal wiki text and insert the corresponding end tag in a second `#!html` block.

For creating styled `<div>`s, `<span>`s or even complex tables containing arbitrary Wiki text, there is a powerful alternative: `#!div`, `#!span` and `#!table`, `#!tr`, `#!td` and `#!th` blocks. Those Wiki processors are built-in and do not require additional packages to be installed.

## How to use `#!html`

To inform the wiki engine that a block of text should be treated as HTML, use the *html* processor:

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre><code>{{{#!html
&lt;h1 style="text-align: right; color: blue"&gt;HTML Test&lt;/h1&gt;
}}}
</code></pre></td><td style="padding-left: 2em"><h1 style="text-align: right; color: blue">HTML Test</h1>
</td></tr></table>

Note that Trac sanitizes your HTML code before displaying it. That means that potentially dangerous constructs, such as Javascript event handlers, will be removed from the output.

The filtering is done by [Genshi](https://genshi.edgewall.org/) and the output will be a well-formed fragment of HTML. This means that you cannot use two HTML blocks, one for opening a \<div> and another for closing it, in order to wrap arbitrary wiki text.

## How to use `#!div` and `#!span`

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre><code>{{{#!div class="important"
**important** is a predefined class.
}}}
</code></pre><pre><code>{{{#!div style="border: 1pt dotted; margin: 1em"
**wikipage** is another predefined class that will
be used when no class is specified.
}}}
</code></pre><pre><code>{{{#!div class="compact" style="border: 1pt dotted; margin: 1em"
**compact** is another predefined class reducing
the padding within the `&lt;div&gt;` to a minimum.
}}}
</code></pre><pre><code>{{{#!div class="wikipage compact" style="border: 1pt dotted"
Classes can be combined (here **wikipage** and **compact**)
which results in this case in reduced //vertical//
padding but there's still some horizontal space for coping
with headings.
}}}
</code></pre><pre><code>{{{#!div class="" style="border: 1pt dotted; margin: 1em"
Explicitly specifying no classes is //not// the same
as specifying no class attribute, as this will remove
the //wikipage// default class.
}}}
</code></pre></td><td style="padding-left: 2em"><div class="important"><p>
<strong>important</strong> is a predefined class.
</p>
</div><div class="wikipage" style="border: 1pt dotted; margin: 1em"><p>
<strong>wikipage</strong> is another predefined class that will
be used when no class is specified.
</p>
</div><div class="compact" style="border: 1pt dotted; margin: 1em"><p>
<strong>compact</strong> is another predefined class reducing
the padding within the <code>&lt;div&gt;</code> to a minimum.
</p>
</div><div class="wikipage compact" style="border: 1pt dotted"><p>
Classes can be combined (here <strong>wikipage</strong> and <strong>compact</strong>)
which results in this case in reduced <em>vertical</em>
padding but there's still some horizontal space for coping
with headings.
</p>
</div><div style="border: 1pt dotted; margin: 1em"><p>
Explicitly specifying no classes is <em>not</em> the same
as specifying no class attribute, as this will remove
the <em>wikipage</em> default class.
</p>
</div></td></tr></table>

Note that the contents of a `#!div` block are contained in one or more paragraphs, which have a non-zero top and bottom margin. This leads to the top and bottom padding in the example above. To remove the top and bottom margin of the content, add the `compact` class to the `#!div`. Another predefined class besides `wikipage` and `compact` is `important`, which can be used to make a paragraph stand out. Extra CSS classes can be defined via [site/style.css](https://trac.example.org/trac_mcp_server/wiki/TracInterfaceCustomization#SiteAppearance).

For spans, you should use the Macro call syntax:

<table>
<tr><th> Wiki Markup 
</th></tr><tr><td><pre><code>Hello
[[span(''WORLD'' (click [#anchor here]), style=color: green; font-size: 120%, id=anchor)]]!
</code></pre></td></tr><tr>
<th> Display 
</th></tr><tr><td style="padding-left: 2em"><blockquote>
<p>
Hello
<span id="anchor" style="color: green; font-size: 120%"><em>WORLD</em> (click <a href="https://trac.example.org/trac_mcp_server/wiki/WikiHtml#anchor">here</a>)</span>!
</p>
</blockquote>
</td></tr></table>

## How to use `#!td` and other table related processors

The `#!td` or `#!th` processors should be used to create table data and table header cells, respectively. The other processors `#!table` and `#!tr` are not required for introducing a table structure, as `#!td` and `#!th` will do this automatically. The `|-` row separator can be used to start a new row when needed, but some may prefer to use a `#!tr` block for that, as this introduces a more formal grouping and offers the possibility to use an extra level of indentation. The main purpose of the `#!table` and `#!tr` is to give the possibility to specify HTML attributes, like *style* or *valign* to these elements.

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre><code>Simple 2x2 table with rich content:
{{{#!th align=left
 - Left
 - Header
}}}
{{{#!th align=left
 - Right
 - Header
}}}
|----------------------------------
{{{#!td style="background: #ffd"
 - Left
 - Content
}}}
{{{#!td style="vertical-align: top"
!RightContent
}}}
|----------------------------------
|| ... and this can be mixed||\
||with pipe-based cells ||
{{{#!td colspan=2
Pick the style the more appropriate
to your content
&#10;See WikiFormatting#Tables for details
on the pipe-based table syntax.
}}}
&#10;If one needs to add some
attributes to the table itself...
&#10;{{{#!table style="border:none;text-align:center;margin:auto"
  {{{#!tr ====================================
    {{{#!th style="border: none"
    Left header
    }}}
    {{{#!th style="border: none"
    Right header
    }}}
  }}}
  {{{#!tr ==== style="border: 1px dotted grey"
    {{{#!td style="border: none"
    1.1
    }}}
    {{{#!td style="border: none"
    1.2
    }}}
  }}}
  {{{#!tr ====================================
    {{{#!td style="border: none"
    2.1
    }}}
    {{{#!td
    2.2
    }}}
  }}}
}}}
&#10;
</code></pre></td><td valign="top"><p>
Simple 2x2 table with rich content:
</p>
<table>
<tr><th><ul><li>Left
</li><li>Header
</li></ul></th><th><ul><li>Right
</li><li>Header
</li></ul></th></tr><tr>
<td style="background: #ffd"><ul><li>Left
</li><li>Content
</li></ul></td><td style="vertical-align: top"><p>
RightContent
</p>
</td></tr><tr>
<td> ... and this can be mixed
</td><td>with pipe-based cells 
</td></tr><tr><td colspan="2"><p>
Pick the style the more appropriate
to your content
</p>
<p>
See <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting#Tables">WikiFormatting#Tables</a> for details
on the pipe-based table syntax.
</p>
</td></tr></table>
<p>
If one needs to add some
attributes to the table itself...
</p>
<table style="border:none; text-align:center; margin:auto"><tr><th style="border: none"><p>
Left header
</p>
</th><th style="border: none"><p>
Right header
</p>
</th></tr><tr style="border: 1px dotted grey"><td style="border: none"><p>
1.1
</p>
</td><td style="border: none"><p>
1.2
</p>
</td></tr><tr><td style="border: none"><p>
2.1
</p>
</td><td><p>
2.2
</p>
</td></tr></table></td></tr></table>

Note that by default tables are assigned the "wiki" CSS class, which gives a distinctive look to the header cells and a default border to the table and cells, as can be seen for the tables on this page. By removing this class (`#!table class=""`), one regains complete control on the table presentation. In particular, neither the table nor the rows nor the cells will have a border, so this is a more effective way to get such an effect rather than having to specify a `style="border: no"` parameter everywhere.

<table><tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre><code>{{{#!table class=""
||  0||  1||  2||
|| 10|| 20|| 30||
|| 11|| 22|| 33||
||||||=  numbers  =||
}}}
</code></pre></td><td><table><tr><td>  0</td><td>  1</td><td>  2
</td></tr><tr><td> 10</td><td> 20</td><td> 30
</td></tr><tr><td> 11</td><td> 22</td><td> 33
</td></tr><tr><th colspan="3">  numbers  
</th></tr></table></td></tr></table>

Other classes can be specified as alternatives (remember that you can define your own in [site/style.css](https://trac.example.org/trac_mcp_server/wiki/TracInterfaceCustomization#SiteAppearance)).

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td><pre><code>{{{#!table class="listing"
||  0||  1||  2||
|| 10|| 20|| 30||
|| 11|| 22|| 33||
||||||=  numbers  =||
}}}
</code></pre></td><td><table><tr><td>  0</td><td>  1</td><td>  2
</td></tr><tr><td> 10</td><td> 20</td><td> 30
</td></tr><tr><td> 11</td><td> 22</td><td> 33
</td></tr><tr><th colspan="3">  numbers  
</th></tr></table></td></tr></table>

## HTML comments

HTML comments are stripped from the output of the `html` processor. To add an HTML comment to a wiki page, use the `htmlcomment` processor, available since Trac 0.12:

<table>
<tr><th> Wiki Markup 
</th></tr><tr><td><pre><code>{{{#!htmlcomment
This block is translated to an HTML comment.
It can contain &lt;tags&gt; and &amp;entities; that will not be escaped in the output.
}}}
</code></pre></td></tr><tr>
<th> Display 
</th></tr><tr><td><pre><code>&lt;!--
This block is translated to an HTML comment.
It can contain &lt;tags&gt; and &amp;entities; that will not be escaped in the output.
--&gt;
</code></pre></td></tr></table>

The character sequence `--` is not allowed in HTML comments, and will generate a rendering error.

## More Information

- <https://www.w3.org/> -- World Wide Web Consortium
- <https://www.w3.org/MarkUp/> -- HTML Markup Home Page

---

See also: [WikiFormatting](https://trac.example.org/trac_mcp_server/wiki/WikiFormatting), [WikiProcessors](https://trac.example.org/trac_mcp_server/wiki/WikiProcessors), [WikiRestructuredText](https://trac.example.org/trac_mcp_server/wiki/WikiRestructuredText)
