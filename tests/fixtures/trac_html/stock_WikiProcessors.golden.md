# Wiki Processors

Processors are [WikiMacros](https://trac.example.org/trac_mcp_server/wiki/WikiMacros) that provide alternative markup formats for the [Wiki engine](https://trac.example.org/trac_mcp_server/wiki/TracWiki). Processors can be thought of as *macro functions to process user-edited text*.

Wiki processors can be used in any Wiki text throughout Trac, such as:

- [syntax highlighting](https://trac.example.org/trac_mcp_server/wiki/WikiProcessors#CodeHighlightingSupport) or for rendering text verbatim
- rendering [Wiki markup inside a context](https://trac.example.org/trac_mcp_server/wiki/WikiProcessors#HTMLrelated) such as \<div> or \<span> blocks or within \<td> or \<th> table cells
- using an alternative markup syntax, like [raw HTML](https://trac.example.org/trac_mcp_server/wiki/WikiHtml) and [Restructured Text](https://trac.example.org/trac_mcp_server/wiki/WikiRestructuredText) or [textile](https://www.booked.net/textism.html)

## Using Processors

To use a processor on a block of text, first delimit the lines using a Wiki *code block*:

```
{{{
The lines
that should be processed...
}}}
```

Immediately after the `{{{` or on the line just below, add `#!` followed by the *processor name*:

```
{{{
#!processorname
The lines
that should be processed...
}}}
```

This is the "shebang" notation, familiar to most UNIX users.

Besides their content, some Wiki processors can also accept *parameters*, which are then given as `key=value` pairs after the processor name and on the same line. If `value` has to contain space, as it's often the case for the style parameter, a quoted string can be used (`key="value with space"`).

As some processors are meant to process Wiki markup, it's quite possible to *nest* processor blocks.
You may want to indent the content of nested blocks for increased clarity, this extra indentation will be ignored when processing the content.

## Examples

<table>
<tr><th> Wiki Markup </th><th> Display 
</th></tr><tr><td colspan="2" style="border: none"><blockquote>
<p>
<strong>Example 1</strong>: Inserting raw HTML
</p>
</blockquote>
</td></tr><tr>
<td style="border: none"><pre>{{{
#!html
&lt;h1 style="color: grey"&gt;This is raw HTML&lt;/h1&gt;
}}}
</pre></td><td style="border: none; padding-left: 2em" valign="top"><h1 style="color: grey">This is raw HTML</h1>
</td></tr><tr>
<td colspan="2" style="border: none"><blockquote>
<p>
<strong>Example 2</strong>: Highlighted Python code in a &lt;div&gt; block with custom style
</p>
</blockquote>
</td></tr><tr>
<td style="border: none"><pre>{{{#!div style="background: #ffd; border: 3px ridge"
&#32;
This is an example of embedded "code" block:
&#32;
  {{{
  #!python
  def hello():
      return "world"
  }}}
&#32;
}}}
</pre></td><td style="border: none; padding: 1em" valign="top"><div class="wikipage" style="background: #ffd; border: 3px ridge"><p>
This is an example of embedded "code" block:
</p>
<pre><code class="language-python">def hello():
    return "world"</code></pre></div></td></tr><tr>
<td colspan="2" style="border: none"><blockquote>
<p>
<strong>Example 3</strong>: Searching tickets from a wiki page, by keywords.
</p>
</blockquote>
</td></tr><tr>
<td style="border: none"><pre>{{{
#!html
&lt;form action="/query" method="get"&gt;&lt;div&gt;
&lt;input type="text" name="keywords" value="~" size="30"/&gt;
&lt;input type="submit" value="Search by Keywords"/&gt;
&lt;!-- To control what fields show up use hidden fields
&lt;input type="hidden" name="col" value="id"/&gt;
&lt;input type="hidden" name="col" value="summary"/&gt;
&lt;input type="hidden" name="col" value="status"/&gt;
&lt;input type="hidden" name="col" value="milestone"/&gt;
&lt;input type="hidden" name="col" value="version"/&gt;
&lt;input type="hidden" name="col" value="owner"/&gt;
&lt;input type="hidden" name="col" value="priority"/&gt;
&lt;input type="hidden" name="col" value="component"/&gt;
--&gt;
&lt;/div&gt;&lt;/form&gt;
}}}
</pre></td><td style="border: none; padding: 1em" valign="top"><form action="/query" method="get"><div>
<input name="keywords" size="30" type="text" value="~"/>
<input type="submit" value="Search by Keywords"/>
</div></form>
</td></tr></table>

## Available Processors

The following processors are included in the Trac distribution:

<table>
<tr><td> <strong><code>#!default</code></strong> </td><td> Present the text verbatim in a preformatted text block. This is the same as specifying <em>no</em> processor name (and no <code>#!</code>). 
</td></tr><tr><td> <strong><code>#!comment</code></strong> </td><td> Do not process the text in this section, i.e. contents exist only in the plain text - not in the rendered page. 
</td></tr><tr><td> <strong><code>#!rtl</code></strong> </td><td> Introduce a Right-To-Left block with appropriate CSS direction and styling. <em>(since 0.12.2)</em>
</td></tr><tr><td colspan="2">
</td></tr><tr><th colspan="2"> <strong></strong>
</th></tr><tr><td> <strong><code>#!html</code></strong> </td><td> Insert custom HTML in a wiki page. 
</td></tr><tr><td> <strong><code>#!htmlcomment</code></strong> </td><td> Insert an HTML comment in a wiki page. (<em>since 0.12</em>) 
</td></tr><tr><td> </td><td> Note that <code>#!html</code> blocks have to be <em>self-contained</em>, i.e. you can't start an HTML element in one block and close it later in a second block. Use the following processors for achieving a similar effect.  
</td></tr><tr><td> <strong><code>#!div</code></strong> </td><td> Wrap wiki content inside a &lt;div&gt; element. 
</td></tr><tr><td> <strong><code>#!span</code></strong> </td><td> Wrap wiki content inside a &lt;span&gt; element. 
</td></tr><tr><td> <strong><code>#!td</code></strong> </td><td> Wrap wiki content inside a &lt;td&gt; element. (<em>since 0.12</em>) 
</td></tr><tr><td> <strong><code>#!th</code></strong> </td><td> Wrap wiki content inside a &lt;th&gt; element. (<em>since 0.12</em>) 
</td></tr><tr><td> <strong><code>#!tr</code></strong> </td><td> Can optionally be used for wrapping <code>#!td</code> and <code>#!th</code> blocks, either for specifying row attributes or better visual grouping. (<em>since 0.12</em>) 
</td></tr><tr><td> <strong><code>#!table</code></strong> </td><td> Can optionally be used for wrapping <code>#!tr</code>, <code>#!td</code> and <code>#!th</code> blocks, for specifying table attributes. One current limitation however is that tables cannot be nested. (<em>since 0.12</em>) 
</td></tr><tr><td> </td><td> See <a href="https://trac.example.org/trac_mcp_server/wiki/WikiHtml">WikiHtml</a> for example usage and more details about these processors. 
</td></tr><tr><td colspan="2">
</td></tr><tr><th colspan="2"> <strong>Other Markups</strong>
</th></tr><tr><td> <strong><code>#!rst</code></strong> </td><td> Trac support for Restructured Text. See <a href="https://trac.example.org/trac_mcp_server/wiki/WikiRestructuredText">WikiRestructuredText</a>. 
</td></tr><tr><td> <strong><code>#!textile</code></strong> </td><td> Supported if <a href="https://pypi.org/project/textile">Textile</a> is installed. See <a href="https://www.booked.net/textism.html">a Textile reference</a>. 
</td></tr><tr><td colspan="2">
</td></tr><tr><th colspan="2"> <strong></strong>
</th></tr><tr><td> <strong><code>#!c</code></strong> <br/> <strong><code>#!cpp</code></strong> (C++) <br/> <strong><code>#!python</code></strong> <br/> <strong><code>#!perl</code></strong> <br/> <strong><code>#!ruby</code></strong> <br/> <strong><code>#!php</code></strong> <br/> <strong><code>#!asp</code></strong> <br/> <strong><code>#!java</code></strong> <br/> <strong><code>#!js</code></strong> (Javascript) <br/> <strong><code>#!sql</code></strong> <br/> <strong><code>#!xml</code></strong> (XML or HTML) <br/> <strong><code>#!sh</code></strong> (Bourne/Bash shell) <br/> </td><td> Trac includes processors to provide inline syntax highlighting for source code in various languages. <br/> <br/> Trac relies on <a href="http://pygments.org">Pygments</a> for syntax coloring. <br/> <br/> See <a href="https://trac.example.org/trac_mcp_server/wiki/TracSyntaxColoring">TracSyntaxColoring</a> for information about which languages are supported and how to enable support for more languages. 
</td></tr><tr><td colspan="2">
</td></tr></table>

Since 1.1.2 the default, coding highlighting and MIME-type processors support the argument `lineno` for adding line numbering to the code block. When a value is specified, as in `lineno=3`, the numbering will start at the specified value. When used in combination with the `lineno` argument, the `marks` argument is also supported for highlighting lines. A single line number, set of line numbers and range of line numbers are allowed. For example, `marks=3`, `marks=3-6`, `marks=3,5,7` and `marks=3-5,7` are all allowed. The specified values are relative to the numbered lines, so if `lineno=2` is specified to start the line numbering at 2, `marks=2` will result in the first line being highlighted.

Using the MIME type as processor, it is possible to syntax-highlight the same languages that are supported when browsing source code.

<table>
<tr><th colspan="2"> <strong>MIME Type Processors</strong>
</th></tr><tr><td><p>
Some examples:
</p>
<pre>{{{#!text/html
&lt;h1&gt;text&lt;/h1&gt;
}}}
</pre></td><td><p>
The result will be syntax highlighted HTML code:
</p>
<pre><code class="language-text/html">&lt;h1&gt;text&lt;/h1&gt;</code></pre><p>
The same is valid for all other <a href="https://trac.example.org/trac_mcp_server/wiki/TracSyntaxColoring#SyntaxColoringSupport">mime types supported</a>.
</p>
</td></tr><tr><td><pre>{{{#!diff
--- Version 55
+++ Version 56
@@ -115,8 +115,9 @@
     name='TracHelloWorld', version='1.0',
     packages=find_packages(exclude=['*.tests*']),
-    entry_points = """
-        [trac.plugins]
-        helloworld = myplugs.helloworld
-    """,
+    entry_points = {
+        'trac.plugins': [
+            'helloworld = myplugs.helloworld',
+        ],
+    },
 )
}}}
</pre></td><td><p>
<strong><code>#!diff</code></strong> has a particularly nice renderer:
</p>
<pre><code class="language-diff">--- Version 55
+++ Version 56
@@ -115,8 +115,9 @@
     name='TracHelloWorld', version='1.0',
     packages=find_packages(exclude=['*.tests*']),
-    entry_points = """
-        [trac.plugins]
-        helloworld = myplugs.helloworld
-    """,
+    entry_points = {
+        'trac.plugins': [
+            'helloworld = myplugs.helloworld',
+        ],
+    },
 )</code></pre></td></tr></table>

Line numbers can be added to code blocks and lines can be highlighted *(since 1.1.2)*.

```
{{{#!python lineno=3 marks=3,9-10,16
def expand_markup(stream, ctxt=None):
    """A Genshi stream filter for expanding `genshi.Markup` events.

    Note: Expansion may not be possible if the fragment is badly
    formed, or partial.
    """
    for event in stream:
        if isinstance(event[1], Markup):
            try:
                for subevent in HTML(event[1]):
                    yield subevent
            except ParseError:
                yield event
        else:
            yield event
}}}
```

```python
def expand_markup(stream, ctxt=None):
    """A Genshi stream filter for expanding `genshi.Markup` events.

    Note: Expansion may not be possible if the fragment is badly
    formed, or partial.
    """
    for event in stream:
        if isinstance(event[1], Markup):
            try:
                for subevent in HTML(event[1]):
                    yield subevent
            except ParseError:
                yield event
        else:
            yield event
```

For more processor macros developed and/or contributed by users, visit the [Trac Hacks](https://trac-hacks.org) community site.

Processors are implemented using the same interfaces as Wiki macros, only the usage syntax differs. To develop a processor, see [WikiMacros#DevelopingCustomMacros](https://trac.example.org/trac_mcp_server/wiki/WikiMacros#DevelopingCustomMacros).

---

See also: [WikiMacros](https://trac.example.org/trac_mcp_server/wiki/WikiMacros), [WikiHtml](https://trac.example.org/trac_mcp_server/wiki/WikiHtml), [WikiRestructuredText](https://trac.example.org/trac_mcp_server/wiki/WikiRestructuredText), [TracSyntaxColoring](https://trac.example.org/trac_mcp_server/wiki/TracSyntaxColoring), [WikiFormatting](https://trac.example.org/trac_mcp_server/wiki/WikiFormatting), [TracGuide](https://trac.example.org/trac_mcp_server/wiki/TracGuide)
