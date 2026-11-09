# The Trac Configuration File

Trac is configured through the **`trac.ini`** file, located in the `<projectenv>/conf` directory. The `trac.ini` configuration file and its parent directory should be writable by the web server.

Trac monitors the timestamp of the file to trigger an environment reload when the timestamp changes. Most changes to the configuration will be reflected immediately, though changes to the `[components]` or `[logging]` sections will require restarting the web server. You may also need to restart the web server after creating a [global configuration](https://trac.example.org/trac_mcp_server/wiki/TracIni#GlobalConfiguration) file when none was previously present.

## Global Configuration

Configuration can be shared among environments using one or more global configuration files. Options in the global configuration will be merged with the environment-specific options, with local options overriding global options. The global configuration file is specified as follows:

```ini
[inherit]
file = /path/to/global/trac.ini
```

Multiple files can be specified using a comma-separated list. Non-absolute paths are relative to the Environment `conf` directory.

Note that you can also specify a global option file when creating a new project, by adding the option `--inherit=/path/to/global/trac.ini` to [trac-admin](https://trac.example.org/trac_mcp_server/wiki/TracAdmin#initenv)'s `initenv` command. If you specify `--inherit` but nevertheless intend to use a global option file with your new environment, you will have to go through the newly generated `conf/trac.ini` file and delete the entries that will otherwise override those in the global file.

There are three more options in the [[inherit]](https://trac.example.org/trac_mcp_server/wiki/TracIni#inherit-section) section, [templates_dir](https://trac.example.org/trac_mcp_server/wiki/TracIni#inherit-templates_dir-option) for sharing global templates, [htdocs_dir](https://trac.example.org/trac_mcp_server/wiki/TracIni#inherit-htdocs_dir-option) for sharing global htdocs and [plugins_dir](https://trac.example.org/trac_mcp_server/wiki/TracIni#inherit-plugins_dir-option), for sharing plugins. Those options can be specified in the shared configuration file, and in fact, configuration files can even be chained if you specify another `[inherit] file` there.

Note that the templates found in the `templates/` directory of the [TracEnvironment](https://trac.example.org/trac_mcp_server/wiki/TracEnvironment) have precedence over those found in `[inherit] templates_dir`. In turn, the latter have precedence over the installed templates, so be careful about what you put there. Notably, if you override a default template, refresh your modifications when you upgrade to a new version of Trac. The preferred way to perform [TracInterfaceCustomization](https://trac.example.org/trac_mcp_server/wiki/TracInterfaceCustomization) is to write a custom plugin doing an appropriate `ITemplateStreamFilter` transformation.

## Reference for settings

This is a reference of available configuration options, and their default settings.

Documentation improvements should be discussed on the [trac-dev mailing list](https://trac.edgewall.org/intertrac/MailingList%23Trac-dev) or described in a [ticket](https://trac.edgewall.org/intertrac/NewTicket). Even better, [submit a patch](https://trac.edgewall.org/intertrac/TracDev/SubmittingPatches) against the docstrings in the code.

### `[attachment]`

|  |  |  |
| --- | --- | --- |
| [`max_size`](#attachment-max_size-option) | Maximum allowed file size (in bytes) for attachments. | `262144` |
| [`max_zip_size`](#attachment-max_zip_size-option) | Maximum allowed total size (in bytes) for an attachment list to be downloadable as a `.zip`. Set this to -1 to disable download as `.zip`. (*since 1.0*) | `2097152` |
| [`render_unsafe_content`](#attachment-render_unsafe_content-option) | Whether attachments should be rendered in the browser, or only made downloadable.  Pretty much any file may be interpreted as HTML by the browser, which allows a malicious user to attach a file containing cross-site scripting attacks.  For public sites where anonymous users can create attachments it is recommended to leave this option disabled. | `disabled` |

### `[browser]`

|  |  |  |
| --- | --- | --- |
| [`color_scale`](#browser-color_scale-option) | Enable colorization of the *age* column.  This uses the same color scale as the source code annotation: blue is older, red is newer. | `enabled` |
| [`downloadable_paths`](#browser-downloadable_paths-option) | List of repository paths that can be downloaded.  Leave this option empty if you want to disable all downloads, otherwise set it to a comma-separated list of authorized paths (those paths are glob patterns, i.e. "\*" can be used as a wild card). In a multi-repository environment, the path must be qualified with the repository name if the path does not point to the default repository (e.g. /reponame/trunk). Note that a simple prefix matching is performed on the paths, so aliases won't get automatically resolved. | `/trunk,/branches/*,/tags/*` |
| [`hide_properties`](#browser-hide_properties-option) | Comma-separated list of version control properties to hide from the repository browser. | `svk:merge` |
| [`intermediate_color`](#browser-intermediate_color-option) | (r,g,b) color triple to use for the color corresponding to the intermediate color, if two linear interpolations are used for the color scale (see `intermediate_point`). If not set, the intermediate color between `oldest_color` and `newest_color` will be used. | (no default) |
| [`intermediate_point`](#browser-intermediate_point-option) | If set to a value between 0 and 1 (exclusive), this will be the point chosen to set the `intermediate_color` for interpolating the color value. | (no default) |
| [`newest_color`](#browser-newest_color-option) | (r,g,b) color triple to use for the color corresponding to the newest color, for the color scale used in *blame* or the browser *age* column if `color_scale` is enabled. | `(255, 136, 136)` |
| [`oldest_color`](#browser-oldest_color-option) | (r,g,b) color triple to use for the color corresponding to the oldest color, for the color scale used in *blame* or the browser *age* column if `color_scale` is enabled. | `(136, 136, 255)` |
| [`oneliner_properties`](#browser-oneliner_properties-option) | Comma-separated list of version control properties to render as oneliner wiki content in the repository browser. | `trac:summary` |
| [`render_unsafe_content`](#browser-render_unsafe_content-option) | Whether raw files should be rendered in the browser, or only made downloadable.  Pretty much any file may be interpreted as HTML by the browser, which allows a malicious user to create a file containing cross-site scripting attacks.  For open repositories where anyone can check-in a file, it is recommended to leave this option disabled. | `disabled` |
| [`wiki_properties`](#browser-wiki_properties-option) | Comma-separated list of version control properties to render as wiki content in the repository browser. | `trac:description` |

### `[changeset]`

|  |  |  |
| --- | --- | --- |
| [`max_diff_bytes`](#changeset-max_diff_bytes-option) | Maximum total size in bytes of the modified files (their old size plus their new size) for which the changeset view will attempt to show the diffs inlined. | `10000000` |
| [`max_diff_files`](#changeset-max_diff_files-option) | Maximum number of modified files for which the changeset view will attempt to show the diffs inlined. | `0` |
| [`wiki_format_messages`](#changeset-wiki_format_messages-option) | Whether wiki formatting should be applied to changeset messages.  If this option is disabled, changeset messages will be rendered as pre-formatted text. | `enabled` |

### `[components]`

Enable or disable components provided by Trac and plugins.
The component to enable/disable is specified by the option name.
The enabled state is determined by the option value: setting
the value to `enabled` or `on` will enable the component, any
other value (typically `disabled` or `off`) will disable the
component.

The option name is either the fully qualified name of the
component or the module/package prefix of the component. The
former enables/disables a specific component, while the latter
enables/disables any component in the specified package/module.

Consider the following configuration snippet:

```
[components]
trac.ticket.report.ReportModule = disabled
acct_mgr.* = enabled
```

The first option tells Trac to disable the
[report module](https://trac.example.org/trac_mcp_server/wiki/TracReports).
The second option instructs Trac to enable all components in
the `acct_mgr` package. The trailing wildcard is required for
module/package matching.

To view the list of active components, go to the *Plugins*
section of *About Trac* (requires `CONFIG_VIEW`
[permission](https://trac.example.org/trac_mcp_server/wiki/TracPermissions)).

See also: [TracPlugins](https://trac.example.org/trac_mcp_server/wiki/TracPlugins)

### `[git]`

|  |  |  |
| --- | --- | --- |
| [`cached_repository`](#git-cached_repository-option) | Wrap `GitRepository` in `CachedRepository`. | `disabled` |
| [`git_bin`](#git-git_bin-option) | Path to the git executable. | `git` |
| [`git_fs_encoding`](#git-git_fs_encoding-option) | Define charset encoding of paths within git repositories. | `utf-8` |
| [`persistent_cache`](#git-persistent_cache-option) | Enable persistent caching of commit tree. | `disabled` |
| [`shortrev_len`](#git-shortrev_len-option) | The length at which a sha1 is abbreviated (must be >= 4 and <= 40). | `7` |
| [`trac_user_rlookup`](#git-trac_user_rlookup-option) | Enable reverse mapping of git email addresses to trac user ids. Performance will be reduced if there are many users and the `cached_repository` option is `disabled`.  A repository resync is required after changing the value of this option. | `disabled` |
| [`use_committer_id`](#git-use_committer_id-option) | Use git-committer id instead of git-author id for the changeset *Author* field. | `enabled` |
| [`use_committer_time`](#git-use_committer_time-option) | Use git-committer timestamp instead of git-author timestamp for the changeset *Timestamp* field. | `enabled` |
| [`wikishortrev_len`](#git-wikishortrev_len-option) | The minimum length at which a hex-string in wiki content is formatted as a changeset TracLink (must be >= 4 and <= 40). | `40` |

### `[gitweb-repositories]`

|  |  |  |
| --- | --- | --- |
| [`projects_base`](#gitweb-repositories-projects_base-option) | Path to the base of your git projects | (no default) |
| [`projects_list`](#gitweb-repositories-projects_list-option) | Path to a gitweb-formatted projects.list | (no default) |
| [`projects_url`](#gitweb-repositories-projects_url-option) | Template for project URLs. `%s` will be replaced with the repo name | (no default) |
| [`sync_per_request`](#gitweb-repositories-sync_per_request-option) | Repositories to sync on every request (not recommended). | (no default) |

### `[header_logo]`

|  |  |  |
| --- | --- | --- |
| [`alt`](#header_logo-alt-option) | Alternative text for the header logo. | `(please configure the [header_logo] section in trac.ini)` |
| [`height`](#header_logo-height-option) | Height of the header logo image in pixels. | `-1` |
| [`link`](#header_logo-link-option) | URL to link to, from the header logo. | (no default) |
| [`src`](#header_logo-src-option) | URL of the image to use as header logo. It can be absolute, server relative or relative.  If relative, it is relative to one of the `/chrome` locations: `site/your-logo.png` if `your-logo.png` is located in the `htdocs` folder within your [TracEnvironment](https://trac.example.org/trac_mcp_server/wiki/TracEnvironment); `common/your-logo.png` if `your-logo.png` is located in the folder mapped to the [htdocs_location](https://trac.example.org/trac_mcp_server/wiki/TracIni#trac-section) URL. Only specifying `your-logo.png` is equivalent to the latter. | `site/your_project_logo.png` |
| [`width`](#header_logo-width-option) | Width of the header logo image in pixels. | `-1` |

### `[http-headers]`

Headers to be added to the HTTP request. (*since 1.2.3*)

The header name must conform to
[RFC7230](https://tools.ietf.org/html/rfc7230) and the following
reserved names are not allowed: content-type, content-length,
location, etag, pragma, cache-control, expires.

### `[inherit]`

|  |  |  |
| --- | --- | --- |
| [`htdocs_dir`](#inherit-htdocs_dir-option) | Path to the *shared htdocs directory*.  Static resources in that directory are mapped to /chrome/shared under the environment URL, in addition to common and site locations.  This can be useful in site.html for common interface customization of multiple Trac environments.  Non-absolute paths are relative to the Environment `conf` directory. (*since 1.0*) | (no default) |
| [`plugins_dir`](#inherit-plugins_dir-option) | Path to the *shared plugins directory*.  Plugins in that directory are loaded in addition to those in the directory of the environment `plugins`, with this one taking precedence.  Non-absolute paths are relative to the Environment `conf` directory. | (no default) |
| [`templates_dir`](#inherit-templates_dir-option) | Path to the *shared templates directory*.  Templates in that directory are loaded in addition to those in the environments `templates` directory, but the latter take precedence.  Non-absolute paths are relative to the Environment `conf` directory. | (no default) |

### `[intertrac]`

This section configures [InterTrac](https://trac.example.org/trac_mcp_server/wiki/InterTrac) prefixes. Option names in
this section that contain a `.` are of the format
`<name>.<attribute>`. Option names that don't contain a `.` define
an alias.

The `.url` attribute is mandatory and is used for locating the
other Trac. This can be a relative path when the other Trac
environment is located on the same server.

The `.title` attribute is used for generating a tooltip when the
cursor is hovered over an [InterTrac](https://trac.example.org/trac_mcp_server/wiki/InterTrac) link.

Example configuration:

```
[intertrac]
# -- Example of setting up an alias:
t = trac

# -- Link to an external Trac:
genshi.title = Edgewall's Trac for Genshi
genshi.url = https://genshi.edgewall.org
```

### `[interwiki]`

Every option in the `[interwiki]` section defines one [InterWiki](https://trac.example.org/trac_mcp_server/wiki/InterWiki)
prefix. The option name defines the prefix. The option value defines
the URL, optionally followed by a description separated from the URL
by whitespace. Parametric URLs are supported as well.

**Example:**

```
[interwiki]
MeatBall = http://www.usemod.com/cgi-bin/mb.pl?
PEP = http://www.python.org/peps/pep-$1.html Python Enhancement Proposal $1
tsvn = tsvn: Interact with TortoiseSvn
```

### `[logging]`

<table><tbody><tr id="logging-log_file-option"><td><a href="#logging-log_file-option"><code>log_file</code></a></td><td><p>
If <code>log_type</code> is <code>file</code>, this should be a path to the
log-file.  Relative paths are resolved relative to the <code>log</code>
directory of the environment.
</p>
</td><td><code>trac.log</code></td></tr><tr id="logging-log_format-option"><td><a href="#logging-log_format-option"><code>log_format</code></a></td><td><p>
Custom logging format.
</p>
<p>
If nothing is set, the following will be used:
</p>
<p>
<code>Trac[$(module)s] $(levelname)s: $(message)s</code>
</p>
<p>
In addition to regular key names supported by the
<a href="http://docs.python.org/library/logging.html">Python logger library</a>
one could use:
</p>
<ul><li><code>$(path)s</code>     the path for the current environment
</li><li><code>$(basename)s</code> the last path component of the current environment
</li><li><code>$(project)s</code>  the project name
</li></ul><p>
Note the usage of <code>$(...)s</code> instead of <code>%(...)s</code> as the latter form
would be interpreted by the ConfigParser itself.
</p>
<p>
Example:
<code>($(thread)d) Trac[$(basename)s:$(module)s] $(levelname)s: $(message)s</code>
</p>
</td><td>(no default)</td></tr><tr id="logging-log_level-option"><td><a href="#logging-log_level-option"><code>log_level</code></a></td><td><p>
Level of verbosity in log.
</p>
<p>
Should be one of (<code>CRITICAL</code>, <code>ERROR</code>, <code>WARNING</code>, <code>INFO</code>, <code>DEBUG</code>).
</p>
</td><td><code>INFO</code></td></tr><tr id="logging-log_type-option"><td><a href="#logging-log_type-option"><code>log_type</code></a></td><td><p>
Logging facility to use.
</p>
<p>
Should be one of (<code>none</code>, <code>file</code>, <code>stderr</code>, <code>syslog</code>, <code>winlog</code>).
</p>
</td><td><code>file</code></td></tr></tbody></table>

### `[mainnav]`

Configures the main navigation bar,
which by default contains *Wiki*, *Timeline*, *Roadmap*,
*Browse Source*, *View Tickets*, *New Ticket*, *Search* and
*Admin*.

The `label`, `href`, and `order` attributes can be specified. Entries
can be disabled by setting the value of the navigation item to
`disabled`.

The following example renames the link to [WikiStart](https://trac.example.org/trac_mcp_server/wiki/WikiStart) to *Home*,
links the *View Tickets* entry to a specific report and disables
the *Search* entry.

```
[mainnav]
wiki.label = Home
tickets.href = /report/24
search = disabled
```

See [TracNavigation](https://trac.example.org/trac_mcp_server/wiki/TracNavigation) for more details.

### `[markdown]`

<table><tbody><tr id="markdown-root_pages-option"><td><a href="#markdown-root_pages-option"><code>root_pages</code></a></td><td><p>
List of wiki page names to be used as root pages for a page hierarchy. Wiki subpages of these roots will use Markdown as the default wiki language.<br/><br/>For example if the list contains <code>Docs, Note</code> the wiki pages <code>Docs/MyDocument</code> and <code>Note/FirstNote</code> will use Markdown; the wiki page <code>NoteOne</code> will use Trac <a href="https://trac.example.org/trac_mcp_server/wiki/WikiFormatting">WikiFormatting</a>.
</p>
<p>
Example:
</p>
<pre><code>[markdown]
root_pages = Docs, Note
</code></pre><p>
<strong>Note:</strong> page names are case sensitive.
</p>
</td><td>(no default)</td></tr></tbody></table>

### `[metanav]`

Configures the meta navigation
entries, which by default are *Login*, *Logout*, *Preferences*,
*Help/Guide* and *About Trac*. The allowed attributes are the
same as for `[mainnav]`. Additionally, a special entry is supported -
`logout.redirect` is the page the user sees after hitting the logout
button. For example:

```
[metanav]
logout.redirect = wiki/Logout
```

See [TracNavigation](https://trac.example.org/trac_mcp_server/wiki/TracNavigation) for more details.

### `[milestone]`

|  |  |  |
| --- | --- | --- |
| [`default_group_by`](#milestone-default_group_by-option) | Default field to use for grouping tickets in the grouped progress bar. (*since 1.2*) | `component` |
| [`default_retarget_to`](#milestone-default_retarget_to-option) | Default milestone to which tickets are retargeted when closing or deleting a milestone. (*since 1.1.2*) | (no default) |
| [`stats_provider`](#milestone-stats_provider-option) | Name of the component implementing `ITicketGroupStatsProvider`, which is used to collect statistics on groups of tickets for display in the milestone views. | `DefaultTicketGroupStatsProvider` |

### `[milestone-groups]`

As the workflow for tickets is now configurable, there can
be many ticket states, and simply displaying closed tickets
vs. all the others is maybe not appropriate in all cases. This
section enables one to easily create *groups* of states that
will be shown in different colors in the milestone progress
bar.

Note that the groups can only be based on the ticket
*status*, nothing else. In particular, it's not possible to
distinguish between different closed tickets based on the
*resolution*.

Example configuration with three groups, *closed*, *new*
and *active* (the default only has closed and active):

```
# the 'closed' group correspond to the 'closed' tickets
closed = closed

# .order: sequence number in the progress bar
closed.order = 0

# .query_args: optional parameters for the corresponding
#              query. In this example, the changes from the
#              default are two additional columns ('created' and
#              'modified'), and sorting is done on 'created'.
closed.query_args = group=resolution,order=time,col=id,col=summary,col=owner,col=type,col=priority,col=component,col=severity,col=time,col=changetime

# .overall_completion: indicates groups that count for overall
#                      completion percentage
closed.overall_completion = true

new = new
new.order = 1
new.css_class = new
new.label = new

# Note: one catch-all group for other statuses is allowed
active = *
active.order = 2

# .css_class: CSS class for this interval
active.css_class = open

# .label: displayed label for this group
active.label = in progress
```

The definition consists in a comma-separated list of accepted
status. Also, '\*' means any status and could be used to
associate all remaining states to one catch-all group.

The CSS class can be one of: new (yellow), open (no color) or
closed (green). Other styles can easily be added using custom
CSS rule: `table.progress td.<class> { background: <color> }`
to a [site/style.css](https://trac.example.org/trac_mcp_server/wiki/TracInterfaceCustomization#SiteAppearance) file
for example.

### `[mimeviewer]`

|  |  |  |
| --- | --- | --- |
| [`max_preview_size`](#mimeviewer-max_preview_size-option) | Maximum file size for HTML preview. | `262144` |
| [`mime_map`](#mimeviewer-mime_map-option) | List of additional MIME types and keyword mappings. Mappings are comma-separated, and for each MIME type, there's a colon (":") separated list of associated keywords or file extensions. | `text/x-dylan:dylan,text/x-idl:ice,text/x-ada:ads:adb` |
| [`mime_map_patterns`](#mimeviewer-mime_map_patterns-option) | List of additional MIME types associated to filename patterns. Mappings are comma-separated, and each mapping consists of a MIME type and a Python regexp used for matching filenames, separated by a colon (":"). (*since 1.0*) | `text/plain:README(?!\.rst)\|INSTALL(?!\.rst)\|COPYING.*` |
| [`pygments_default_style`](#mimeviewer-pygments_default_style-option) | The default style to use for Pygments syntax highlighting. | `trac` |
| [`pygments_modes`](#mimeviewer-pygments_modes-option) | List of additional MIME types known by Pygments.  For each, a tuple `mimetype:mode:quality` has to be specified, where `mimetype` is the MIME type, `mode` is the corresponding Pygments mode to be used for the conversion and `quality` is the quality ratio associated to this conversion. That can also be used to override the default quality ratio used by the Pygments render. | (no default) |
| [`tab_width`](#mimeviewer-tab_width-option) | Displayed tab width in file preview. | `8` |
| [`treat_as_binary`](#mimeviewer-treat_as_binary-option) | Comma-separated list of MIME types that should be treated as binary data. | `application/octet-stream,application/pdf,application/postscript,application/msword,application/rtf` |

### `[notification]`

|  |  |  |
| --- | --- | --- |
| [`admit_domains`](#notification-admit_domains-option) | Comma-separated list of domains that should be considered as valid for email addresses (such as localdomain). | (no default) |
| [`ambiguous_char_width`](#notification-ambiguous_char_width-option) | Width of ambiguous characters that should be used in the table of the notification mail.  If `single`, the same width as characters in US-ASCII. This is expected by most users. If `double`, twice the width of US-ASCII characters. This is expected by CJK users. | `single` |
| [`batch_subject_template`](#notification-batch_subject_template-option) | Like `ticket_subject_template` but for batch modifications. (*since 1.0*) | `${prefix} Batch modify: ${tickets_descr}` |
| [`default_format.email`](#notification-default_format.email-option) | Default format to distribute email notifications. | `text/plain` |
| [`email_address_resolvers`](#notification-email_address_resolvers-option) | Comma separated list of email resolver components in the order they will be called. If an email address is resolved, the remaining resolvers will not be called. | `SessionEmailResolver` |
| [`email_sender`](#notification-email_sender-option) | Name of the component implementing `IEmailSender`.  This component is used by the notification system to send emails. Trac currently provides `SmtpEmailSender` for connecting to an SMTP server, and `SendmailEmailSender` for running a `sendmail`-compatible executable. | `SmtpEmailSender` |
| [`ignore_domains`](#notification-ignore_domains-option) | Comma-separated list of domains that should not be considered part of email addresses (for usernames with Kerberos domains). | (no default) |
| [`message_id_hash`](#notification-message_id_hash-option) | Hash algorithm to create unique Message-ID header. *(since 1.0.13)* | `md5` |
| [`mime_encoding`](#notification-mime_encoding-option) | Specifies the MIME encoding scheme for emails.  Supported values are: `none`, the default value which uses 7-bit encoding if the text is plain ASCII or 8-bit otherwise. `base64`, which works with any kind of content but may cause some issues with touchy anti-spam/anti-virus engine. `qp` or `quoted-printable`, which works best for european languages (more compact than base64) if 8-bit encoding cannot be used. | `none` |
| [`sendmail_path`](#notification-sendmail_path-option) | Path to the sendmail executable.  The sendmail program must accept the `-i` and `-f` options. | `sendmail` |
| [`smtp_always_bcc`](#notification-smtp_always_bcc-option) | Comma-separated list of email addresses to always send notifications to. Addresses are not public (Bcc:). | (no default) |
| [`smtp_always_cc`](#notification-smtp_always_cc-option) | Comma-separated list of email addresses to always send notifications to. Addresses can be seen by all recipients (Cc:). | (no default) |
| [`smtp_default_domain`](#notification-smtp_default_domain-option) | Default host/domain to append to addresses that do not specify one. Fully qualified addresses are not modified. The default domain is appended to all username/login for which an email address cannot be found in the user settings. | (no default) |
| [`smtp_enabled`](#notification-smtp_enabled-option) | Enable email notification. | `disabled` |
| [`smtp_from`](#notification-smtp_from-option) | Sender address to use in notification emails.  At least one of `smtp_from` and `smtp_replyto` must be set, otherwise Trac refuses to send notification mails. | `trac@localhost` |
| [`smtp_from_author`](#notification-smtp_from_author-option) | Use the author of the change as the sender in notification emails (e.g. reporter of a new ticket, author of a comment). If the author hasn't set an email address, `smtp_from` and `smtp_from_name` are used instead. (*since 1.0*) | `disabled` |
| [`smtp_from_name`](#notification-smtp_from_name-option) | Sender name to use in notification emails. | (no default) |
| [`smtp_password`](#notification-smtp_password-option) | Password for authenticating with SMTP server. | (no default) |
| [`smtp_port`](#notification-smtp_port-option) | SMTP server port to use for email notification. | `25` |
| [`smtp_replyto`](#notification-smtp_replyto-option) | Reply-To address to use in notification emails.  At least one of `smtp_from` and `smtp_replyto` must be set, otherwise Trac refuses to send notification mails. | `trac@localhost` |
| [`smtp_server`](#notification-smtp_server-option) | SMTP server hostname to use for email notifications. | `localhost` |
| [`smtp_subject_prefix`](#notification-smtp_subject_prefix-option) | Text to prepend to subject line of notification emails.  If the setting is not defined, then `[$project_name]` is used as the prefix. If no prefix is desired, then specifying an empty option will disable it. | `__default__` |
| [`smtp_user`](#notification-smtp_user-option) | Username for authenticating with SMTP server. | (no default) |
| [`ticket_subject_template`](#notification-ticket_subject_template-option) | A Jinja2 text template snippet used to get the notification subject.  The template variables are documented on the [TracNotification](https://trac.example.org/trac_mcp_server/wiki/TracNotification#Customizingthee-mailsubject) page. | `${prefix} #${ticket.id}: ${summary}` |
| [`use_public_cc`](#notification-use_public_cc-option) | Addresses in the To and Cc fields are visible to all recipients.  If this option is disabled, recipients are put in the Bcc list. | `disabled` |
| [`use_short_addr`](#notification-use_short_addr-option) | Permit email address without a host/domain (i.e. username only).  The SMTP server should accept those addresses, and either append a FQDN or use local delivery. See also `smtp_default_domain`. Do not use this option with a public SMTP server. | `disabled` |
| [`use_tls`](#notification-use_tls-option) | Use SSL/TLS to send notifications over SMTP. | `disabled` |

### `[notification-subscriber]`

The notifications subscriptions are controlled by plugins. All
`INotificationSubscriber` components are in charge. These components
may allow to be configured via this section in the `trac.ini` file.

See [TracNotification](https://trac.example.org/trac_mcp_server/wiki/TracNotification) for more details.

Available subscribers:

| Subscriber | Description |
| --- | --- |
| `AlwaysEmailSubscriber` |  |
| `CarbonCopySubscriber` | Ticket that I'm listed in the CC field is modified |
| `NewTicketSubscriber` | Any ticket is created |
| `TicketOwnerSubscriber` | Ticket that I own is created or modified |
| `TicketPreviousUpdatersSubscriber` | Ticket that I previously updated is modified |
| `TicketReporterSubscriber` | Ticket that I reported is modified |
| `TicketUpdaterSubscriber` | I update a ticket |

### `[project]`

|  |  |  |
| --- | --- | --- |
| [`admin`](#project-admin-option) | E-Mail address of the project's administrator. | (no default) |
| [`admin_trac_url`](#project-admin_trac_url-option) | Base URL of a Trac instance where errors in this Trac should be reported.  This can be an absolute or relative URL, or '.' to reference this Trac instance. An empty value will disable the reporting buttons. | `.` |
| [`descr`](#project-descr-option) | Short description of the project. | `My example project` |
| [`footer`](#project-footer-option) | Page footer text (right-aligned). | `Visit the Trac open source project at<br /><a href="https://trac.edgewall.org/">https://trac.edgewall.org/</a>` |
| [`icon`](#project-icon-option) | URL of the icon of the project. | `common/trac.ico` |
| [`name`](#project-name-option) | Name of the project. | `My Project` |
| [`url`](#project-url-option) | URL of the project web site.  This is usually the domain in which the `base_url` resides. For example, the project URL might be https://myproject.com, with the Trac site (`base_url`) residing at either https://trac.myproject.com or https://myproject.com/trac. The project URL is added to the footer of notification e-mails. | (no default) |

### `[pygments-lexer]`

Configure Pygments [lexer](http://pygments.org/docs/lexers/) options.

For example, to set the
[PhpLexer](http://pygments.org/docs/lexers/#lexers-for-php-and-related-languages) options
`startinline` and `funcnamehighlighting`:

```
[pygments-lexer]
php.startinline = True
php.funcnamehighlighting = True
```

The lexer name is derived from the class name, with `Lexer` stripped
from the end. The lexer *short names* can also be used in place
of the lexer name.

### `[query]`

|  |  |  |
| --- | --- | --- |
| [`default_anonymous_query`](#query-default_anonymous_query-option) | The default query for anonymous users. The query is either in [query language](https://trac.example.org/trac_mcp_server/wiki/TracQuery#QueryLanguage) syntax, or a URL query string starting with `?` as used in `query:` [Trac links](https://trac.example.org/trac_mcp_server/wiki/TracQuery#UsingTracLinks). | `status!=closed&cc~=$USER` |
| [`default_query`](#query-default_query-option) | The default query for authenticated users. The query is either in [query language](https://trac.example.org/trac_mcp_server/wiki/TracQuery#QueryLanguage) syntax, or a URL query string starting with `?` as used in `query:` [Trac links](https://trac.example.org/trac_mcp_server/wiki/TracQuery#UsingTracLinks). | `status!=closed&owner=$USER` |
| [`items_per_page`](#query-items_per_page-option) | Number of tickets displayed per page in ticket queries, by default. Set to `0` to specify no limit. | `100` |
| [`ticketlink_query`](#query-ticketlink_query-option) | The base query to be used when linkifying values of ticket fields. The query is a URL query string starting with `?` as used in `query:` [Trac links](https://trac.example.org/trac_mcp_server/wiki/TracQuery#UsingTracLinks). | `?status=!closed` |

### `[report]`

|  |  |  |
| --- | --- | --- |
| [`items_per_page`](#report-items_per_page-option) | Number of tickets displayed per page in ticket reports, by default. Set to `0` to specify no limit. | `100` |
| [`items_per_page_rss`](#report-items_per_page_rss-option) | Number of tickets displayed in the rss feeds for reports. Set to `0` to specify no limit. | `0` |

### `[repositories]`

One of the methods for registering repositories is to
populate the `[repositories]` section of `trac.ini`.

This is especially suited for setting up aliases, using a
[shared configuration](https://trac.example.org/trac_mcp_server/wiki/TracIni#GlobalConfiguration), or specifying
repositories at the time of environment creation.

See [TracRepositoryAdmin](https://trac.example.org/trac_mcp_server/wiki/TracRepositoryAdmin#ReposTracIni) for
details on the format of this section, and look elsewhere on the
page for information on other repository providers.

### `[revisionlog]`

|  |  |  |
| --- | --- | --- |
| [`default_log_limit`](#revisionlog-default_log_limit-option) | Default value for the limit argument in the [TracRevisionLog](https://trac.example.org/trac_mcp_server/wiki/TracRevisionLog). | `100` |
| [`graph_colors`](#revisionlog-graph_colors-option) | Comma-separated list of colors to use for the [TracRevisionLog](https://trac.example.org/trac_mcp_server/wiki/TracRevisionLog) graph display. (*since 1.0*) | `#cc0,#0c0,#0cc,#00c,#c0c,#c00` |

### `[roadmap]`

|  |  |  |
| --- | --- | --- |
| [`stats_provider`](#roadmap-stats_provider-option) | Name of the component implementing `ITicketGroupStatsProvider`, which is used to collect statistics on groups of tickets for display in the roadmap views. | `DefaultTicketGroupStatsProvider` |

### `[search]`

|  |  |  |
| --- | --- | --- |
| [`default_disabled_filters`](#search-default_disabled_filters-option) | Specifies which search filters should be disabled by default on the search page. This will also restrict the filters for the quick search function. The filter names defined by default components are: `wiki`, `ticket`, `milestone` and `changeset`. For plugins, look for their implementation of the ISearchSource interface, in the `get_search_filters()` method, the first member of returned tuple. Once disabled, search filters can still be manually enabled by the user on the search page. | (no default) |
| [`min_query_length`](#search-min_query_length-option) | Minimum length of query string allowed when performing a search. | `3` |

### `[sqlite]`

|  |  |  |
| --- | --- | --- |
| [`extensions`](#sqlite-extensions-option) | Paths to [sqlite extensions](https://sqlite.org/loadext.html). The paths may be absolute or relative to the Trac environment. | (no default) |

### `[svn]`

|  |  |  |
| --- | --- | --- |
| [`authz_file`](#svn-authz_file-option) | The path to the Subversion [authorization (authz) file](http://svnbook.red-bean.com/en/1.7/svn.serverconfig.pathbasedauthz.html). To enable authz permission checking, the `AuthzSourcePolicy` permission policy must be added to `[trac] permission_policies`. Non-absolute paths are relative to the Environment `conf` directory. | (no default) |
| [`authz_module_name`](#svn-authz_module_name-option) | The module prefix used in the `authz_file` for the default repository. If left empty, the global section is used. | (no default) |

### `[ticket]`

|  |  |  |
| --- | --- | --- |
| [`allowed_empty_fields`](#ticket-allowed_empty_fields-option) | Comma-separated list of `select` fields that can have an empty value. (*since 1.1.2*) | `milestone,version` |
| [`commit_ticket_update_check_perms`](#ticket-commit_ticket_update_check_perms-option) | Check that the committer has permission to perform the requested operations on the referenced tickets.  This requires that the user names be the same for Trac and repository operations. | `enabled` |
| [`commit_ticket_update_commands.close`](#ticket-commit_ticket_update_commands.close-option) | Commands that close tickets, as a space-separated list. | `close closed closes fix fixed fixes` |
| [`commit_ticket_update_commands.refs`](#ticket-commit_ticket_update_commands.refs-option) | Commands that add a reference, as a space-separated list.  If set to the special value `<ALL>`, all tickets referenced by the message will get a reference to the changeset. | `addresses re references refs see` |
| [`commit_ticket_update_envelope`](#ticket-commit_ticket_update_envelope-option) | Require commands to be enclosed in an envelope.  Must be empty or contain two characters. For example, if set to `[]`, then commands must be in the form of `[closes #4]`. | (no default) |
| [`commit_ticket_update_notify`](#ticket-commit_ticket_update_notify-option) | Send ticket change notification when updating a ticket. | `enabled` |
| [`default_cc`](#ticket-default_cc-option) | Default cc: list for newly created tickets. | (no default) |
| [`default_component`](#ticket-default_component-option) | Default component for newly created tickets. | (no default) |
| [`default_description`](#ticket-default_description-option) | Default description for newly created tickets. | (no default) |
| [`default_keywords`](#ticket-default_keywords-option) | Default keywords for newly created tickets. | (no default) |
| [`default_milestone`](#ticket-default_milestone-option) | Default milestone for newly created tickets. | (no default) |
| [`default_owner`](#ticket-default_owner-option) | Default owner for newly created tickets. The component owner is used when set to the value `< default >`. | `< default >` |
| [`default_priority`](#ticket-default_priority-option) | Default priority for newly created tickets. | `major` |
| [`default_resolution`](#ticket-default_resolution-option) | Default resolution for resolving (closing) tickets. | `fixed` |
| [`default_severity`](#ticket-default_severity-option) | Default severity for newly created tickets. | (no default) |
| [`default_summary`](#ticket-default_summary-option) | Default summary (title) for newly created tickets. | (no default) |
| [`default_type`](#ticket-default_type-option) | Default type for newly created tickets. | `defect` |
| [`default_version`](#ticket-default_version-option) | Default version for newly created tickets. | (no default) |
| [`max_comment_size`](#ticket-max_comment_size-option) | Maximum allowed comment size in characters. | `262144` |
| [`max_description_size`](#ticket-max_description_size-option) | Maximum allowed description size in characters. | `262144` |
| [`max_summary_size`](#ticket-max_summary_size-option) | Maximum allowed summary size in characters. (*since 1.0.2*) | `262144` |
| [`preserve_newlines`](#ticket-preserve_newlines-option) | Whether Wiki formatter should respect the new lines present in the Wiki text. If set to 'default', this is equivalent to 'yes' for new environments but keeps the old behavior for upgraded environments (i.e. 'no'). | `default` |
| [`restrict_owner`](#ticket-restrict_owner-option) | Make the owner field of tickets use a drop-down menu. Be sure to understand the performance implications before activating this option. See [Assign-to as Drop-Down List](https://trac.example.org/trac_mcp_server/wiki/TracTickets#Assign-toasDrop-DownList).  Please note that e-mail addresses are **not** obfuscated in the resulting drop-down menu, so this option should not be used if e-mail addresses must remain protected. | `disabled` |
| [`workflow`](#ticket-workflow-option) | Ordered list of workflow controllers to use for ticket actions. | `ConfigurableTicketWorkflow` |

### `[ticket-custom]`

In this section, you can define additional fields for tickets. See
[TracTicketsCustomFields](https://trac.example.org/trac_mcp_server/wiki/TracTicketsCustomFields) for more details.

### `[ticket-workflow]`

The workflow for tickets is controlled by plugins. By default,
there's only a `ConfigurableTicketWorkflow` component in charge.
That component allows the workflow to be configured via this section
in the `trac.ini` file. See [TracWorkflow](https://trac.example.org/trac_mcp_server/wiki/TracWorkflow) for more details.

### `[timeline]`

|  |  |  |
| --- | --- | --- |
| [`abbreviated_messages`](#timeline-abbreviated_messages-option) | Whether wiki-formatted event messages should be truncated or not.  This only affects the default rendering, and can be overridden by specific event providers, see their own documentation. | `enabled` |
| [`changeset_collapse_events`](#timeline-changeset_collapse_events-option) | Whether consecutive changesets from the same author having exactly the same message should be presented as one event. That event will link to the range of changesets in the log view. | `disabled` |
| [`changeset_long_messages`](#timeline-changeset_long_messages-option) | Whether wiki-formatted changeset messages should be multiline or not.  If this option is not specified or is false and `wiki_format_messages` is set to true, changeset messages will be single line only, losing some formatting (bullet points, etc). | `disabled` |
| [`changeset_show_files`](#timeline-changeset_show_files-option) | Number of files to show (`-1` for unlimited, `0` to disable).  This can also be `location`, for showing the common prefix for the changed files. | `0` |
| [`default_daysback`](#timeline-default_daysback-option) | Default number of days displayed in the Timeline, in days. | `30` |
| [`max_daysback`](#timeline-max_daysback-option) | Maximum number of days (-1 for unlimited) displayable in the Timeline. | `90` |
| [`newticket_formatter`](#timeline-newticket_formatter-option) | Which formatter flavor (e.g. 'html' or 'oneliner') should be used when presenting the description for new tickets. If 'oneliner', the [timeline] abbreviated_messages option applies. | `oneliner` |
| [`ticket_show_component`](#timeline-ticket_show_component-option) | Enable the display of component of tickets in the timeline. (*since 1.1.1*) | `disabled` |
| [`ticket_show_details`](#timeline-ticket_show_details-option) | Enable the display of all ticket changes in the timeline, not only open / close operations. | `enabled` |

### `[trac]`

|  |  |  |
| --- | --- | --- |
| [`anonymous_session_lifetime`](#trac-anonymous_session_lifetime-option) | Lifetime of the anonymous session, in days.  Set the option to 0 to disable purging old anonymous sessions. (*since 1.0.17*) | `90` |
| [`auth_cookie_domain`](#trac-auth_cookie_domain-option) | Auth cookie domain attribute.  The auth cookie can be shared among multiple subdomains by setting the value to the domain. (*since 1.2*) | (no default) |
| [`auth_cookie_lifetime`](#trac-auth_cookie_lifetime-option) | Lifetime of the authentication cookie, in seconds.  This value determines how long the browser will cache authentication information, and therefore, after how much inactivity a user will have to log in again. The value of 0 makes the cookie expire at the end of the browsing session. | `0` |
| [`auth_cookie_path`](#trac-auth_cookie_path-option) | Path for the authentication cookie. Set this to the common base path of several Trac instances if you want them to share the cookie. | (no default) |
| [`auto_preview_timeout`](#trac-auto_preview_timeout-option) | Inactivity timeout in seconds after which the automatic wiki preview triggers an update. This option can contain floating-point values. The lower the setting, the more requests will be made to the server. Set this to 0 to disable automatic preview. | `2.0` |
| [`auto_reload`](#trac-auto_reload-option) | Automatically reload template files after modification. | `disabled` |
| [`backup_dir`](#trac-backup_dir-option) | Database backup location | `db` |
| [`base_url`](#trac-base_url-option) | Base URL of the Trac site.  This is used to produce documents outside of the web browsing context, such as URLs in notification e-mails that point to Trac resources. | (no default) |
| [`check_auth_ip`](#trac-check_auth_ip-option) | Whether the IP address of the user should be checked for authentication. | `disabled` |
| [`database`](#trac-database-option) | Database connection [string](https://trac.example.org/trac_mcp_server/wiki/TracEnvironment#DatabaseConnectionStrings) for this project | `sqlite:db/trac.db` |
| [`debug_sql`](#trac-debug_sql-option) | Show the SQL queries in the Trac log, at DEBUG level. | `disabled` |
| [`default_charset`](#trac-default_charset-option) | Charset to be used when in doubt. | `utf-8` |
| [`default_date_format`](#trac-default_date_format-option) | The date format. Valid options are 'iso8601' for selecting ISO 8601 format, or leave it empty which means the default date format will be inferred from the browser's default language. (*since 1.0*) | (no default) |
| [`default_dateinfo_format`](#trac-default_dateinfo_format-option) | The date information format. Valid options are 'relative' for displaying relative format and 'absolute' for displaying absolute format. (*since 1.0*) | `relative` |
| [`default_handler`](#trac-default_handler-option) | Name of the component that handles requests to the base URL.  Options include `TimelineModule`, `RoadmapModule`, `BrowserModule`, `QueryModule`, `ReportModule`, `TicketModule` and `WikiModule`.  The [session preference](https://trac.example.org/trac_mcp_server/prefs/userinterface) for default handler take precedence, when set. | `WikiModule` |
| [`default_language`](#trac-default_language-option) | The preferred language to use if no user preference has been set. | (no default) |
| [`default_timezone`](#trac-default_timezone-option) | The default timezone to use | (no default) |
| [`htdocs_location`](#trac-htdocs_location-option) | Base URL for serving the core static resources below `/chrome/common/`.  It can be left empty, and Trac will simply serve those resources itself.  Advanced users can use this together with [trac-admin ... deploy \<deploydir>](https://trac.example.org/trac_mcp_server/wiki/TracAdmin) to allow serving the static resources for Trac directly from the web server. Note however that this only applies to the `<deploydir>/htdocs/common` directory, the other deployed resources (i.e. those from plugins) will not be made available this way and additional rewrite rules will be needed in the web server. | (no default) |
| [`ignore_auth_case`](#trac-ignore_auth_case-option) | Whether login names should be converted to lower case. | `disabled` |
| [`jquery_location`](#trac-jquery_location-option) | Location of the jQuery JavaScript library (version 3.6.3).  An empty value loads jQuery from the copy bundled with Trac.  Alternatively, jQuery could be loaded from a CDN, for example: <http://code.jquery.com/jquery-3.6.3.min.js>, <http://ajax.aspnetcdn.com/ajax/jQuery/jquery-3.6.3.min.js> or <https://ajax.googleapis.com/ajax/libs/jquery/3.6.3/jquery.min.js>.  (*since 1.0*) | (no default) |
| [`jquery_ui_location`](#trac-jquery_ui_location-option) | Location of the jQuery UI JavaScript library (version 1.13.0).  An empty value loads jQuery UI from the copy bundled with Trac.  Alternatively, jQuery UI could be loaded from a CDN, for example: <https://ajax.googleapis.com/ajax/libs/jqueryui/1.13.0/jquery-ui.min.js> or <http://ajax.aspnetcdn.com/ajax/jquery.ui/1.13.0/jquery-ui.min.js>.  (*since 1.0*) | (no default) |
| [`jquery_ui_theme_location`](#trac-jquery_ui_theme_location-option) | Location of the theme to be used with the jQuery UI JavaScript library (version 1.13.0).  An empty value loads the custom Trac jQuery UI theme from the copy bundled with Trac.  Alternatively, a jQuery UI theme could be loaded from a CDN, for example: <https://ajax.googleapis.com/ajax/libs/jqueryui/1.13.0/themes/start/jquery-ui.css> or <http://ajax.aspnetcdn.com/ajax/jquery.ui/1.13.0/themes/start/jquery-ui.css>.  (*since 1.0*) | (no default) |
| [`never_obfuscate_mailto`](#trac-never_obfuscate_mailto-option) | Never obfuscate `mailto:` links explicitly written in the wiki, even if `show_email_addresses` is false or the user doesn't have EMAIL_VIEW permission. | `disabled` |
| [`permission_policies`](#trac-permission_policies-option) | List of components implementing `IPermissionPolicy`, in the order in which they will be applied. These components manage fine-grained access control to Trac resources. | `DefaultWikiPolicy,DefaultTicketPolicy,DefaultPermissionPolicy,LegacyAttachmentPolicy` |
| [`permission_store`](#trac-permission_store-option) | Name of the component implementing `IPermissionStore`, which is used for managing user and group permissions. | `DefaultPermissionStore` |
| [`request_filters`](#trac-request_filters-option) | Ordered list of filters to apply to all requests. | (no default) |
| [`resizable_textareas`](#trac-resizable_textareas-option) | Make `<textarea>` fields resizable. Requires JavaScript. | `enabled` |
| [`secure_cookies`](#trac-secure_cookies-option) | Restrict cookies to HTTPS connections.  When true, set the `secure` flag on all cookies so that they are only sent to the server on HTTPS connections. Use this if your Trac instance is only accessible through HTTPS. | `disabled` |
| [`show_email_addresses`](#trac-show_email_addresses-option) | Show email addresses instead of usernames. If false, email addresses are obfuscated for users that don't have EMAIL_VIEW permission. | `disabled` |
| [`show_full_names`](#trac-show_full_names-option) | Show full names instead of usernames. (*since 1.2*) | `enabled` |
| [`timeout`](#trac-timeout-option) | Timeout value for database connection, in seconds. Use '0' to specify *no timeout*. | `20` |
| [`use_base_url_for_redirect`](#trac-use_base_url_for_redirect-option) | Optionally use `[trac] base_url` for redirects.  In some configurations, usually involving running Trac behind a HTTP proxy, Trac can't automatically reconstruct the URL that is used to access it. You may need to use this option to force Trac to use the `base_url` setting also for redirects. This introduces the obvious limitation that this environment will only be usable when accessible from that URL, as redirects are frequently used. | `disabled` |
| [`use_chunked_encoding`](#trac-use_chunked_encoding-option) | If enabled, send contents as chunked encoding in HTTP/1.1. Otherwise, send contents with `Content-Length` header after entire of the contents are rendered. (*since 1.0.6*) | `enabled` |
| [`use_xsendfile`](#trac-use_xsendfile-option) | When true, send a `X-Sendfile` header and no content when sending files from the filesystem, so that the web server handles the content. This requires a web server that knows how to handle such a header, like Apache with `mod_xsendfile` or lighttpd. (*since 1.0*) | `disabled` |
| [`wiki_toolbars`](#trac-wiki_toolbars-option) | Add a simple toolbar on top of Wiki \<textarea>s. (*since 1.0.2*) | `enabled` |
| [`xsendfile_header`](#trac-xsendfile_header-option) | The header to use if `use_xsendfile` is enabled. If Nginx is used, set `X-Accel-Redirect`. (*since 1.0.6*) | `X-Sendfile` |

### `[versioncontrol]`

|  |  |  |
| --- | --- | --- |
| [`allowed_repository_dir_prefixes`](#versioncontrol-allowed_repository_dir_prefixes-option) | Comma-separated list of allowed prefixes for repository directories when adding and editing repositories in the repository admin panel. If the list is empty, all repository directories are allowed. | (no default) |
| [`default_repository_type`](#versioncontrol-default_repository_type-option) | Default repository connector type.  This is used as the default repository type for repositories defined in the [repositories](https://trac.example.org/trac_mcp_server/wiki/TracIni#repositories-section) section or using the "Repositories" admin panel. | `svn` |

### `[wiki]`

|  |  |  |
| --- | --- | --- |
| [`default_edit_area_height`](#wiki-default_edit_area_height-option) | Default height of the textarea on the wiki edit page. (*Since 1.1.5*) | `20` |
| [`ignore_missing_pages`](#wiki-ignore_missing_pages-option) | Enable/disable highlighting [CamelCase](https://trac.example.org/trac_mcp_server/wiki/CamelCase) links to missing pages. | `disabled` |
| [`max_size`](#wiki-max_size-option) | Maximum allowed wiki page size in characters. | `262144` |
| [`render_unsafe_content`](#wiki-render_unsafe_content-option) | Enable/disable the use of unsafe HTML tags such as `<script>` or `<embed>` with the HTML [WikiProcessor](https://trac.example.org/trac_mcp_server/wiki/WikiProcessors).  For public sites where anonymous users can edit the wiki it is recommended to leave this option disabled. | `disabled` |
| [`safe_origins`](#wiki-safe_origins-option) | List of URIs considered "safe cross-origin", that will be rendered as `img` element without `crossorigin="anonymous"` attribute or used in `url()` of inline style attribute even if `[wiki] render_unsafe_content` is `false` (*since 1.0.15*).  To make any origins safe, specify "\*" in the list. | `data:` |
| [`safe_schemes`](#wiki-safe_schemes-option) | List of URI schemes considered "safe", that will be rendered as external links even if `[wiki] render_unsafe_content` is `false`. | `cvs,file,ftp,git,irc,http,https,news,sftp,smb,ssh,svn,svn+ssh` |
| [`split_page_names`](#wiki-split_page_names-option) | Enable/disable splitting the [WikiPageNames](https://trac.example.org/trac_mcp_server/wiki/WikiPageNames) with space characters. | `disabled` |

## Configure Error Reporting

The error reporting page has a *Create* button for reporting
issues. The site to which issues are reported depends on the
configuration of the Trac site and the user’s permissions.

If the user doesn’t possess TRAC_ADMIN, the site to which a user is directed to create a ticket is determined by the [[trac] admin_trac_url](https://trac.example.org/trac_mcp_server/wiki/TracIni#project-admin_trac_url-option) setting:

- If empty, there will be no *Create* button.
- If set to the default value (`.`), the ticket will be
  created on the site which the error occurred.
- Otherwise the ticket will be created at the site pointed
  to by `admin_trac_url`.

If [[project] admin](https://trac.example.org/trac_mcp_server/wiki/TracIni#project-admin-option) is not empty, the administrator's email address will be rendered on the error page.

If the user possesses TRAC_ADMIN, the *Create* button will direct the user to report the issue on trac.edgewall.org. If the error was generated in a plugin, the error will be reported to the project URL provided that the plugin author has included the project URL in the plugin installation data. The user possessing TRAC_ADMIN also sees a traceback and system information on the error page.

---

See also: [TracAdmin](https://trac.example.org/trac_mcp_server/wiki/TracAdmin), [TracEnvironment](https://trac.example.org/trac_mcp_server/wiki/TracEnvironment)
