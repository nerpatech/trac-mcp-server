"""The YAML front matter on a Markdown export of a Trac page (#212)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
import yaml

from trac_mcp_server.converters.front_matter import (
    build_front_matter,
    exported_from,
    instance_path,
    split_front_matter,
)

FIXED = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)


def _build(**over):
    kwargs = {
        "title": "Blog Post",
        "source": "http://host/proj/wiki/Blog/Post",
        "trac_instance": "/proj",
        "trac_page": "Blog/Post",
        "trac_version": 7,
        "exported": FIXED,
    }
    kwargs.update(over)
    return build_front_matter(**kwargs)


class TestBuild:
    def test_exact_output(self):
        assert _build() == (
            "---\n"
            'title: "Blog Post"\n'
            'source: "http://host/proj/wiki/Blog/Post"\n'
            'trac_instance: "/proj"\n'
            'trac_page: "Blog/Post"\n'
            "trac_version: 7\n"
            'exported: "2026-11-09T12:00:00Z"\n'
            "---\n\n"
        )

    def test_values_round_trip_through_yaml(self):
        title = 'Why: "quotes" and é中文 # not a comment'
        block = _build(title=title)

        raw, rest = split_front_matter(block + "Body\n")
        data = yaml.safe_load(raw)

        assert rest == "Body\n"
        assert data["title"] == title
        assert data["source"] == "http://host/proj/wiki/Blog/Post"
        assert data["trac_instance"] == "/proj"
        assert data["trac_page"] == "Blog/Post"
        assert data["trac_version"] == 7
        assert data["exported"] == "2026-11-09T12:00:00Z"

    def test_non_ascii_is_written_as_is(self):
        assert 'title: "café"\n' in _build(title="café")

    def test_no_version_is_null(self):
        block = _build(trac_version=None)

        assert "\ntrac_version: null\n" in block
        data = yaml.safe_load(split_front_matter(block + "x")[0])
        assert data["trac_version"] is None

    def test_aware_datetime_is_converted_to_utc(self):
        when = datetime(
            2026, 11, 9, 14, 30, 5, tzinfo=timezone(timedelta(hours=2))
        )
        assert 'exported: "2026-11-09T12:30:05Z"\n' in _build(
            exported=when
        )

    def test_naive_datetime_is_taken_as_utc(self):
        when = datetime(2026, 11, 9, 14, 30, 5)
        assert 'exported: "2026-11-09T14:30:05Z"\n' in _build(
            exported=when
        )

    def test_exported_defaults_to_now(self):
        block = _build(exported=None)
        data = yaml.safe_load(split_front_matter(block + "x")[0])
        stamp = datetime.strptime(
            data["exported"], "%Y-%m-%dT%H:%M:%SZ"
        )
        assert (
            abs(
                datetime.now(UTC).replace(tzinfo=None) - stamp
            ).total_seconds()
            < 60
        )


class TestSplit:
    def test_lf(self):
        assert split_front_matter("---\nx: 1\n---\n# T\n") == (
            "x: 1",
            "# T\n",
        )

    def test_crlf(self):
        assert split_front_matter(
            "---\r\nx: 1\r\ny: 2\r\n---\r\n# T\r\n"
        ) == (
            "x: 1\r\ny: 2",
            "# T\r\n",
        )

    def test_bom(self):
        assert split_front_matter("﻿---\nx: 1\n---\n# T") == (
            "x: 1",
            "# T",
        )

    def test_closing_fence_at_end_of_text(self):
        assert split_front_matter("---\nx: 1\n---") == ("x: 1", "")

    def test_blank_lines_after_the_block_are_stripped(self):
        assert (
            split_front_matter("---\nx: 1\n---\n\n\r\n# T")[1] == "# T"
        )

    def test_multiple_lines_between(self):
        raw, rest = split_front_matter(
            "---\na: 1\nb: 2\nc: 3\n---\nbody"
        )
        assert raw == "a: 1\nb: 2\nc: 3"
        assert rest == "body"

    @pytest.mark.parametrize(
        "text",
        [
            "# Hello\nBody",
            "",
            "text\n---\nx: 1\n---\n",
            " ---\nx: 1\n---\n",
            "----\nx: 1\n---\n",
        ],
    )
    def test_no_block(self, text):
        assert split_front_matter(text) == (None, text)

    def test_nothing_between_the_fences_is_not_a_block(self):
        assert split_front_matter("---\n---\n") == (None, "---\n---\n")

    def test_unclosed_block(self):
        text = "---\ntitle: T\n# No closing fence\n"
        assert split_front_matter(text) == (None, text)

    def test_a_closing_fence_with_trailing_text_is_not_one(self):
        text = "---\nx: 1\n--- more\n"
        assert split_front_matter(text) == (None, text)


class TestExportedFrom:
    def test_a_dict_with_trac_page(self):
        data = exported_from(_build() + "# T\n")
        assert data is not None
        assert data["trac_page"] == "Blog/Post"
        assert data["trac_version"] == 7

    def test_invalid_yaml_is_none(self):
        assert (
            exported_from("---\ntrac_page: [unclosed\n---\nbody")
            is None
        )

    def test_a_list_is_none(self):
        assert exported_from("---\n- trac_page\n- x\n---\nbody") is None

    def test_a_scalar_is_none(self):
        assert exported_from("---\njust text\n---\nbody") is None

    def test_no_block_is_none(self):
        assert exported_from("# Hello\n") is None

    def test_no_trac_page_is_none(self):
        assert (
            exported_from("---\ntitle: Post\ndate: 2026-01-01\n---\nx")
            is None
        )

    @pytest.mark.parametrize("value", ['""', "null", "7", "[a]"])
    def test_an_empty_or_non_string_trac_page_is_none(self, value):
        assert exported_from(f"---\ntrac_page: {value}\n---\nx") is None

    def test_crlf_and_bom_still_count(self):
        text = "﻿---\r\ntrac_page: P\r\n---\r\nbody"
        assert exported_from(text) == {"trac_page": "P"}


class TestInstancePath:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("http://h:8000/proj", "/proj"),
            ("http://h:8000/proj/", "/proj"),
            ("http://h", "/"),
            ("http://h/", "/"),
            ("https://h/a/b", "/a/b"),
        ],
    )
    def test_path(self, url, expected):
        assert instance_path(url) == expected
