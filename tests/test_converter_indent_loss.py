"""Table-driven suite for the code-block indentation-loss detector
(ticket #68).

The defect this detects is the only one in the project's warning family
that is INVISIBLE: a TracWiki ``{{{ }}}`` block fed to the Markdown
converter has its body indentation eaten by paragraph handling, and the
result is stored with an empty warnings list under a render that looks
entirely plausible. It was committed twice inside ticket #62 -- in a
ticket whose subject was that exact corruption -- and caught both times
only by reading the stored bytes by hand.

Per `Rules/testing/SeededDefectFirst`, and per #59 comment 2's
fixture-isolation finding, every row is ONE shape in ONE document.

**The seeds were watched failing first.** Measured at ``e073aa3``,
before any of this ticket's code existed, every seed below converted
with its indentation stripped and returned ``conversion.warnings == []``
AND ``_check_tracwiki_markup_in_markdown(...) == []``. The second half
matters: the one existing rule that might have spoken is silent by
construction, because it runs ``blank_code_fences`` first and that
blanks a ``{{{ ... }}}`` region including its delimiters, erasing the
``{{{`` it scans for.

**The controls are the half that matters more here.** A loss check is a
new FIRING rule, not a suppression, so the usual risk is inverted: the
danger is over-firing on correct content, and #57 and #59 are this
project's own evidence that an over-firing check gets muted, taking its
true positives with it. Two controls came from the ticket; the rest were
added because the prototype got them wrong at least once.
"""

import pytest

from trac_mcp_server.converters.common import (
    find_code_block_indentation_loss,
)
from trac_mcp_server.converters.markdown_to_tracwiki import (
    convert_with_warnings,
)

# Seeds: each MUST report loss. Rows 1 and 2 are ticket #68 section 2's
# two measured shapes -- note that row 2 is the PLAIN block, broader than
# #62 measured, so the loss is not limited to the `#!lang` processor
# form. Row 3 places a block after prose, the shape #62 actually
# committed on itself. Row 4 is the row that forced content-pairing over
# positional pairing: the source holds one delimited block, the
# converted output holds two (the indented-Markdown block becomes a
# `{{{ }}}` one), so a positional rule sees a count mismatch and skips
# the whole document -- a false NEGATIVE on a real defect.
SEEDS = {
    "processor_block": "{{{#!python\ndef f(x):\n    return 1\n}}}\n",
    "plain_block": "{{{\n  two\n    four\n}}}\n",
    "block_after_prose": (
        "Intro paragraph.\n\n{{{#!sh\nfor f in *; do\n"
        "    echo $f\ndone\n}}}\n"
    ),
    "damaged_block_beside_indented_markdown": (
        "    md_indented()\n        deeper()\n\ntext\n\n"
        "{{{#!python\ndef f(x):\n    return 1\n}}}\n"
    ),
}

# Controls: each MUST stay silent.
#
# `markdown_fence` and `markdown_indented_block` are the ticket's own two
# control rows -- the safe shapes. `markdown_indented_block` is why a
# four-space-indented Markdown block is deliberately not a "block" to
# the detector: its four-space marker is dropped by CORRECT conversion,
# so counting it would report every one of them.
#
# `fence_in_list_item` is why indentation is compared relative to each
# block's own minimum rather than absolutely. mistune dedents a fenced
# block nested in a list item by the list's indent -- legitimately, and
# by the same amount on every line. Absolute comparison called this
# `2 -> 0` loss on a document with nothing wrong with it.
#
# `identical_blocks_twice` pins the accepted residual: content-pairing
# needs an unambiguous match, so two identical blocks pair ambiguously
# and are skipped. Here they are clean anyway; the residual is that two
# identical DAMAGED blocks would also stay silent, which is a false
# negative accepted over the risk of reporting against the wrong
# counterpart.
#
# `tracwiki_quoted_in_fence` is the row that keeps this check from being
# muted: this project's own docs quote TracWiki block syntax inside
# Markdown fences, and a check that fired on them would not survive.
CONTROLS = {
    "markdown_fence": "```python\ndef f(x):\n    return 1\n```\n",
    "markdown_indented_block": "    def f(x):\n        return 1\n",
    "fence_in_list_item": (
        "- item:\n\n  ```python\n  def f():\n      return 1\n  ```\n"
    ),
    "prose_only": "Some **prose** with `code` in it.\n",
    "two_distinct_fences": (
        "```\na\n    b\n```\n\ntext\n\n```\nc\n    d\n```\n"
    ),
    "identical_blocks_twice": (
        "```\na\n    b\n```\n\ntext\n\n```\na\n    b\n```\n"
    ),
    "tracwiki_quoted_in_fence": (
        "Docs:\n\n```\n{{{#!python\ndef f():\n    return 1\n}}}\n```\n"
    ),
}


@pytest.mark.parametrize("name", sorted(SEEDS))
def test_seed_reports_indentation_loss(name):
    source = SEEDS[name]
    losses = find_code_block_indentation_loss(
        source, convert_with_warnings(source).text
    )
    assert losses, f"{name}: expected indentation loss, got none"
    assert losses[0]["converted_indent"] < losses[0]["source_indent"]


@pytest.mark.parametrize("name", sorted(CONTROLS))
def test_control_stays_silent(name):
    source = CONTROLS[name]
    assert (
        find_code_block_indentation_loss(
            source, convert_with_warnings(source).text
        )
        == []
    )


def test_the_seeds_are_genuinely_silent_without_this_check():
    """The pre-fix (red) state, asserted rather than remembered.

    Every seed still converts with its indentation stripped and still
    draws nothing from the converter's own warning list. If a later
    change makes the converter preserve the indentation, these seeds
    stop being seeds -- and this assertion fails loudly rather than
    leaving a check that can no longer fail looking exactly like one
    that always passes.
    """
    for name, source in SEEDS.items():
        result = convert_with_warnings(source)
        assert result.warnings == [], (
            f"{name}: the converter now warns on its own -- re-derive "
            "this suite's seeds"
        )
        assert result.text != source.rstrip("\n"), (
            f"{name}: conversion is now a no-op -- this is no longer a "
            "loss seed"
        )


def test_uniform_dedent_is_a_documented_blind_spot():
    """Relative comparison cannot see a whole block shifted left.

    Stated as a test rather than a comment so the trade is visible: it
    is the price of `fence_in_list_item` staying silent, and it is the
    safe direction -- a uniform shift leaves the code's internal
    structure, the part whose loss makes it syntactically invalid.
    """
    source = "```\n    a\n    b\n```\n"
    assert (
        find_code_block_indentation_loss(source, "{{{\na\nb\n}}}") == []
    )


def test_loss_is_reported_once_per_block_not_once_per_line():
    source = (
        "{{{#!python\ndef f():\n    a = 1\n    b = 2\n    c = 3\n}}}\n"
    )
    losses = find_code_block_indentation_loss(
        source, convert_with_warnings(source).text
    )
    assert len(losses) == 1
    assert losses[0]["content"] == "a = 1"


def test_two_damaged_blocks_report_separately():
    source = (
        "{{{#!python\ndef f():\n    return 1\n}}}\n\ntext\n\n"
        "{{{#!sh\nfor f in *; do\n    echo $f\ndone\n}}}\n"
    )
    losses = find_code_block_indentation_loss(
        source, convert_with_warnings(source).text
    )
    assert [loss["content"] for loss in losses] == [
        "return 1",
        "echo $f",
    ]


def test_unterminated_block_is_not_treated_as_a_block():
    """Its extent is unknown, and guessing it invents body lines that
    were never inside a block -- the direction that over-fires."""
    source = "{{{#!python\ndef f():\n    return 1\n"
    assert (
        find_code_block_indentation_loss(
            source, convert_with_warnings(source).text
        )
        == []
    )
