"""trac-convert and the exported-Markdown guard (Trac /trac_mcp_server #212).

``--from-wiki PAGE --to md`` starts its output with front matter naming
the page; Markdown carrying that front matter is refused on its way back
to TracWiki unless ``--force``.
"""

import io

import pytest
import yaml

from tests.test_cli_convert_trac import (
    _HEADING_HTML,
    _PAGE_VERSION,
    _PUT_OK,
    _TEST_URL,
    _mock_tracclient,
    _mock_valid_env,
)
from trac_mcp_server.cli.convert import (
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    main,
)
from trac_mcp_server.converters.front_matter import split_front_matter

_EXPORT = (
    "---\n"
    'title: "Heading"\n'
    'source: "https://trac.example.com/wiki/MyPage"\n'
    'trac_instance: "/proj"\n'
    'trac_page: "MyPage"\n'
    "trac_version: 7\n"
    'exported: "2026-11-09T12:00:00Z"\n'
    "---\n\n# Heading\n\nsome body\n"
)

_REFUSAL = (
    "trac-convert: refusing to convert: this Markdown was exported from "
    "Trac page 'MyPage' (instance /proj, version 7) and the export is "
    "one-way; edit the page as TracWiki (trac-convert --from-wiki "
    "MyPage --to tracwiki), or pass --force\n"
)


@pytest.fixture
def trac(monkeypatch):
    _mock_valid_env(monkeypatch)
    mock_instance = _mock_tracclient(
        get_wiki_page_return="= Heading =\n\nsome tracwiki body",
        get_wiki_page_html_return=_HEADING_HTML,
        put_wiki_page_return=_PUT_OK,
    )
    monkeypatch.setattr(
        "trac_mcp_server.cli.convert.TracClient",
        lambda _: mock_instance,
    )
    return mock_instance


class TestExport:
    def test_from_wiki_to_md_starts_with_the_front_matter(
        self, trac, capsys
    ):
        assert main(["--from-wiki", "MyPage", "--to", "md"]) == EXIT_OK

        out = capsys.readouterr().out
        raw, rest = split_front_matter(out)
        assert raw is not None
        meta = yaml.safe_load(raw)
        assert list(meta) == [
            "title",
            "source",
            "trac_instance",
            "trac_page",
            "trac_version",
            "exported",
        ]
        assert meta["title"] == "Heading"
        assert meta["source"] == f"{_TEST_URL}/wiki/MyPage"
        assert meta["trac_instance"] == "/"
        assert meta["trac_page"] == "MyPage"
        assert meta["trac_version"] == _PAGE_VERSION
        assert rest.startswith("# Heading")
        assert out.startswith("---\n")

    def test_the_title_falls_back_to_the_page_name(
        self, monkeypatch, capsys
    ):
        _mock_valid_env(monkeypatch)
        mock_instance = _mock_tracclient(
            get_wiki_page_return="== Sub ==\n",
            get_wiki_page_html_return='<h2 id="Sub">Sub</h2>\n',
        )
        monkeypatch.setattr(
            "trac_mcp_server.cli.convert.TracClient",
            lambda _: mock_instance,
        )

        assert main(["--from-wiki", "MyPage", "--to", "md"]) == EXIT_OK

        meta = yaml.safe_load(
            split_front_matter(capsys.readouterr().out)[0]
        )
        assert meta["title"] == "MyPage"

    def test_the_output_file_has_it_too(self, trac, tmp_path):
        out = tmp_path / "p.md"

        rc = main(
            ["--from-wiki", "MyPage", "--to", "md", "-o", str(out)]
        )

        assert rc == EXIT_OK
        assert out.read_text(encoding="utf-8").startswith(
            '---\ntitle: "Heading"\n'
        )

    def test_from_wiki_to_tracwiki_has_none(self, trac, capsys):
        rc = main(["--from-wiki", "MyPage", "--to", "tracwiki"])

        assert rc == EXIT_OK
        assert "trac_page" not in capsys.readouterr().out

    def test_text_input_never_gets_any(self, monkeypatch, trac, capsys):
        trac.wiki_to_html.return_value = _HEADING_HTML
        monkeypatch.setattr(
            "sys.stdin", io.StringIO("= Heading =\n\nbody\n")
        )

        rc = main(["--from", "tracwiki", "--to", "md"])

        assert rc == EXIT_OK
        out = capsys.readouterr().out
        assert out.startswith("# Heading")
        assert "trac_page" not in out


class TestGuard:
    def _file(self, tmp_path, text=_EXPORT):
        src = tmp_path / "pulled.md"
        src.write_text(text, encoding="utf-8")
        return str(src)

    def test_exported_markdown_to_tracwiki_is_refused(
        self, trac, tmp_path, capsys
    ):
        rc = main([self._file(tmp_path), "--to", "tracwiki"])

        assert rc == EXIT_RUNTIME_ERROR == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == _REFUSAL
        trac.put_wiki_page.assert_not_called()

    def test_refused_to_an_output_file_writes_nothing(
        self, trac, tmp_path
    ):
        out = tmp_path / "out.tw"

        rc = main(
            [self._file(tmp_path), "--to", "tracwiki", "-o", str(out)]
        )

        assert rc == EXIT_RUNTIME_ERROR
        assert not out.exists()

    def test_refused_from_stdin_too(self, monkeypatch, trac, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO(_EXPORT))

        rc = main(["--from", "md", "--to", "tracwiki"])

        assert rc == EXIT_RUNTIME_ERROR
        assert capsys.readouterr().err == _REFUSAL

    def test_to_wiki_is_refused_the_same_without_a_trac_write(
        self, trac, tmp_path, capsys
    ):
        rc = main([self._file(tmp_path), "--to-wiki", "MyPage"])

        assert rc == EXIT_RUNTIME_ERROR
        captured = capsys.readouterr()
        assert captured.err == _REFUSAL
        assert captured.out == ""
        trac.put_wiki_page.assert_not_called()

    def test_missing_instance_and_version_read_unknown(
        self, trac, tmp_path, capsys
    ):
        text = '---\ntrac_page: "P"\n---\n\nBody\n'

        rc = main([self._file(tmp_path, text), "--to", "tracwiki"])

        assert rc == EXIT_RUNTIME_ERROR
        err = capsys.readouterr().err
        assert "(instance unknown, version unknown)" in err

    def test_force_converts_and_drops_the_front_matter(
        self, trac, tmp_path, capsys
    ):
        rc = main([self._file(tmp_path), "--to", "tracwiki", "--force"])

        assert rc == EXIT_OK
        captured = capsys.readouterr()
        assert captured.out.startswith("= Heading =")
        assert "trac_page" not in captured.out
        assert "---" not in captured.out
        assert captured.err == ""

    def test_force_with_to_wiki_stores_without_the_front_matter(
        self, trac, tmp_path
    ):
        rc = main(
            [self._file(tmp_path), "--to-wiki", "MyPage", "--force"]
        )

        assert rc == EXIT_OK
        stored = trac.put_wiki_page.call_args.args[1]
        assert stored.startswith("= Heading =")
        assert "trac_page" not in stored

    def test_front_matter_without_trac_page_converts_as_before(
        self, trac, tmp_path, capsys
    ):
        text = (
            "---\ntitle: Post\ndate: 2026-01-01\n---\n\n# Hello\n\nx\n"
        )

        rc = main([self._file(tmp_path, text), "--to", "tracwiki"])

        assert rc == EXIT_OK
        out = capsys.readouterr().out
        # Left alone: whatever the converter did with it before.
        assert "Hello" in out

    def test_markdown_target_is_not_guarded(
        self, trac, tmp_path, capsys
    ):
        trac.wiki_to_html.return_value = _HEADING_HTML

        rc = main([self._file(tmp_path), "--from", "md", "--to", "md"])

        assert rc == EXIT_OK

    def test_help_lists_force(self, capsys):
        with pytest.raises(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        assert "--force" in out
        assert "trac_page" in out
