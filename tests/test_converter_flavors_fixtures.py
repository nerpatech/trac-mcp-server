"""Whole-document rows for the flavour profiles (Trac /trac_mcp_server #216).

``tests/fixtures/flavors/`` holds one ChatGPT-style and one Gemini-style
document. The repo is public and the real samples are private
conversations, so both are invented text that reproduces each measured
shape: the doubled-backslash math in table cells and display paragraphs,
a favicon citation with its ``name+1`` label line, a ``utm_source`` link;
a copy of a rendered Gemini page with language-label code blocks, ``•``
bullets and ``[N] (url)`` citations. Each has a golden TracWiki file,
written by running the converter and then read line by line.
"""

import asyncio
import html
import re
from pathlib import Path

import pytest

from trac_mcp_server.converters.markdown_to_tracwiki import (
    convert_with_warnings,
    markdown_to_tracwiki,
)

FIXTURES = Path(__file__).parent / "fixtures" / "flavors"
PAIRS = [
    ("chatgpt_dialogue", "chatgpt"),
    ("gemini_page", "gemini"),
]


def _source(name: str) -> str:
    return (FIXTURES / f"{name}.md").read_text(encoding="utf-8")


def _golden(name: str) -> str:
    return (FIXTURES / f"{name}.tracwiki").read_text(encoding="utf-8")


@pytest.mark.parametrize(("name", "flavor"), PAIRS)
def test_the_fixture_converts_to_its_golden(name, flavor):
    result = convert_with_warnings(_source(name), flavor=flavor)
    assert result.text + "\n" == _golden(name)


@pytest.mark.parametrize(("name", "flavor"), PAIRS)
def test_the_default_does_not_produce_the_golden(name, flavor):
    assert markdown_to_tracwiki(_source(name)) + "\n" != _golden(name)


def test_the_gemini_fixture_reports_its_three_blocks_and_the_default_hints():
    source = _source("gemini_page")
    # The HTML tag warning is the converter's, about the html block's code.
    assert convert_with_warnings(source, flavor="gemini").warnings == [
        "gemini: rebuilt 3 code block(s) from 'Use code with caution.' "
        "markers",
        "HTML tags detected - these may not render correctly in TracWiki.",
    ]
    assert any(
        "flavor" in w and "gemini" in w
        for w in convert_with_warnings(source).warnings
    )


def test_the_chatgpt_fixture_has_no_warnings():
    source = _source("chatgpt_dialogue")
    assert (
        convert_with_warnings(source, flavor="chatgpt").warnings == []
    )


def test_the_gemini_code_survives_the_conversion_byte_for_byte():
    golden = _golden("gemini_page")
    for needle in (
        "# Initialize the extension\n",
        'if __name__ == "__main__":\n',
        '            return "Home"\n',
        "    # a trailing comment\n",
        "<h1>{{heading}}</h1>\n",
    ):
        assert needle in golden
    assert "'''" not in golden


def test_the_chatgpt_golden_has_the_designed_constructs():
    golden = _golden("chatgpt_dialogue")
    assert "||`R_{\\text{A1}}`||" in golden
    assert golden.count("{{{#!latex") == 2
    assert "[example.org](" not in golden
    assert "[https://example.org example.org]" in golden
    assert "utm_" not in golden
    assert "https://example.com/notes?x=1" in golden
    assert '{{{#!sh\necho "\\(not math\\)"\n}}}' in golden


# -- live: Trac's own renderer on /trac_test -----------------------------------


def _pre_texts(rendered: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", "", body))
        for body in re.findall(
            r"<pre[^>]*>(.*?)</pre>", rendered, re.DOTALL
        )
    ]


def _labelled_code_blocks(source: str) -> list[str]:
    """The code of each `<language>` ... `Use code with caution.` run of a
    Gemini copy, written out here independently of the converter."""
    lines = source.split("\n")
    blocks = []
    i = 0
    while i < len(lines):
        if lines[i].strip() in ("html", "bash", "python"):
            j = lines.index("Use code with caution.", i)
            blocks.append("\n".join(lines[i + 1 : j]))
            i = j
        i += 1
    return blocks


@pytest.mark.live
@pytest.mark.parametrize(("name", "flavor"), PAIRS)
def test_live_the_fixture_renders_without_a_system_message(
    name, flavor
):
    """`convert_preview` with the fixture's flavour against /trac_test
    (read-only: wiki.wikiToHtml writes nothing). The render has no Trac
    error box, and for the Gemini page every code block's text is in a
    `<pre>` exactly as the source had it."""
    from tests.live_helpers import TEST_INSTANCE, trac_test_registry
    from trac_mcp_server.config_bootstrap import bootstrap_config
    from trac_mcp_server.core.client import TracClient
    from trac_mcp_server.mcp.tools.convert_preview import (
        _handle_convert_preview,
    )

    config, _ = bootstrap_config()
    client = TracClient(
        trac_test_registry(config).resolve(TEST_INSTANCE)
    )
    source = _source(name)

    result = asyncio.run(
        _handle_convert_preview(
            client,
            {
                "content": source,
                "flavor": flavor,
                "check_targets": False,
            },
        )
    )

    assert not result.isError, result.content
    payload = result.structuredContent
    assert payload["html_truncated"] is False
    rendered = payload["rendered_html"]
    assert "system-message" not in rendered, rendered
    assert payload["tracwiki"] + "\n" == _golden(name)
    if flavor == "gemini":
        pres = _pre_texts(rendered)
        blocks = _labelled_code_blocks(source)
        assert len(blocks) == 3
        for code in blocks:
            assert any(code == text.strip("\n") for text in pres), (
                code,
                pres,
            )
