"""Tests for file_handler module: path validation, encoding-aware read/write, format detection."""

from pathlib import Path

import pytest

from trac_mcp_server.file_handler import (
    detect_file_format,
    read_file_async,
    read_file_with_encoding,
    validate_file_path,
    validate_output_path,
    write_file,
    write_file_async,
)

# =============================================================================
# validate_file_path
# =============================================================================


class TestValidateFilePath:
    """Tests for validate_file_path(path_str)."""

    def test_valid_absolute_path(self, tmp_path):
        """Valid absolute path to existing file returns resolved Path."""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = validate_file_path(str(f))
        assert isinstance(result, Path)
        assert result == f.resolve()

    def test_relative_path_raises(self):
        """Relative path raises ValueError with 'must be absolute'."""
        with pytest.raises(ValueError, match="must be absolute"):
            validate_file_path("relative/path.txt")

    def test_nonexistent_file_raises(self, tmp_path):
        """Non-existent file raises ValueError with 'not found'."""
        missing = tmp_path / "does_not_exist.txt"
        with pytest.raises(ValueError, match="not found"):
            validate_file_path(str(missing))

    def test_directory_path_raises(self, tmp_path):
        """Directory path raises ValueError with 'not a file'."""
        with pytest.raises(ValueError, match="not a file"):
            validate_file_path(str(tmp_path))

    def test_symlink_resolves(self, tmp_path):
        """Symlink resolves to real path and validates."""
        real_file = tmp_path / "real.txt"
        real_file.write_text("content")
        link = tmp_path / "link.txt"
        link.symlink_to(real_file)

        result = validate_file_path(str(link))
        assert result == real_file.resolve()


# =============================================================================
# validate_output_path
# =============================================================================


class TestValidateOutputPath:
    """Tests for validate_output_path(path_str, base_dir=None)."""

    def test_valid_absolute_path(self, tmp_path):
        """Valid absolute output path returns resolved Path when parent exists."""
        output = tmp_path / "output.txt"
        result = validate_output_path(str(output))
        assert isinstance(result, Path)
        assert result == output.resolve()

    def test_parent_not_exists_raises(self, tmp_path):
        """Parent directory doesn't exist raises ValueError."""
        output = tmp_path / "nonexistent_dir" / "output.txt"
        with pytest.raises(
            ValueError, match="parent directory not found"
        ):
            validate_output_path(str(output))

    def test_base_dir_enforced(self, tmp_path):
        """Output outside base_dir raises ValueError."""
        base = tmp_path / "allowed"
        base.mkdir()
        outside = tmp_path / "outside.txt"
        with pytest.raises(ValueError, match="outside base directory"):
            validate_output_path(str(outside), base_dir=str(base))

    def test_base_dir_allows_inside(self, tmp_path):
        """Output inside base_dir returns resolved Path."""
        base = tmp_path / "allowed"
        base.mkdir()
        inside = base / "file.txt"
        result = validate_output_path(str(inside), base_dir=str(base))
        assert result == inside.resolve()

    def test_relative_path_raises(self):
        """Relative output path raises ValueError."""
        with pytest.raises(ValueError, match="must be absolute"):
            validate_output_path("relative/output.txt")


# =============================================================================
# read_file_with_encoding
# =============================================================================


class TestReadFileWithEncoding:
    """Tests for read_file_with_encoding(path)."""

    def test_utf8_file(self, tmp_path):
        """UTF-8 file returns content and 'utf-8' encoding."""
        f = tmp_path / "utf8.txt"
        f.write_text("Hello, world!", encoding="utf-8")
        content, encoding = read_file_with_encoding(f)
        assert content == "Hello, world!"
        assert encoding == "utf-8"

    def test_empty_file(self, tmp_path):
        """Empty file returns empty string and 'utf-8' default encoding."""
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        content, encoding = read_file_with_encoding(f)
        assert content == ""
        assert encoding == "utf-8"

    def test_non_utf8_file(self, tmp_path):
        """Non-UTF-8 file detects encoding and returns decoded content."""
        f = tmp_path / "latin1.txt"
        # Write Latin-1 encoded content with characters that are NOT valid UTF-8
        text = (
            "Caf\u00e9 r\u00e9sum\u00e9 na\u00efve \u00fc\u00f6\u00e4"
        )
        f.write_bytes(text.encode("latin-1"))
        content, encoding = read_file_with_encoding(f)
        # The content should be readable (decoded properly)
        assert "Caf" in content
        # Encoding should not be utf-8 (charset-normalizer should detect latin-1 or similar)
        assert encoding is not None
        assert isinstance(encoding, str)

    def test_valid_utf8_bypasses_charset_normalizer(
        self, tmp_path, monkeypatch
    ):
        """ticket #48: a file that decodes cleanly as UTF-8 must not be
        handed to charset-normalizer's guess at all, even though the
        prior behavior let a wrong guess (e.g. cp1250) silently override
        a correct strict decode and corrupt every multi-byte character
        on write. Mocking from_bytes to blow up if called proves the
        strict-UTF-8-first order rather than depending on a byte sample
        charset-normalizer happens to misdetect today.
        """
        f = tmp_path / "em_dash.md"
        text = "Layered config — defaults, then overrides.\n" * 50
        f.write_bytes(text.encode("utf-8"))

        def fake_from_bytes(_raw):
            raise AssertionError(
                "charset-normalizer must not run for a file that "
                "decodes cleanly as UTF-8"
            )

        monkeypatch.setattr(
            "trac_mcp_server.file_handler.from_bytes", fake_from_bytes
        )

        content, encoding = read_file_with_encoding(f)
        assert content == text
        assert encoding == "utf-8"

    def test_non_utf8_file_still_uses_charset_normalizer_fallback(
        self, tmp_path
    ):
        """The charset-normalizer fallback path must still run for bytes
        that are not valid UTF-8 -- the strict-decode-first change must
        not disable detection entirely, only skip it when unnecessary.
        """
        f = tmp_path / "latin1.txt"
        text = "Café résumé"
        raw = text.encode("latin-1")
        # Sanity: this byte sequence must not be valid UTF-8, or the test
        # would not actually exercise the fallback path.
        with pytest.raises(UnicodeDecodeError):
            raw.decode("utf-8")
        f.write_bytes(raw)
        content, encoding = read_file_with_encoding(f)
        assert "Caf" in content
        assert encoding != "utf-8"


# =============================================================================
# write_file
# =============================================================================


class TestWriteFile:
    """Tests for write_file(path, content, encoding)."""

    def test_write_basic(self, tmp_path):
        """Writes content and returns bytes written count."""
        f = tmp_path / "output.txt"
        count = write_file(f, "Hello, world!")
        assert f.read_text(encoding="utf-8") == "Hello, world!"
        assert count == len("Hello, world!".encode("utf-8"))

    def test_creates_parent_directories(self, tmp_path):
        """Creates parent directories if they don't exist."""
        f = tmp_path / "sub" / "deep" / "output.txt"
        count = write_file(f, "nested content")
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "nested content"
        assert count > 0

    def test_write_with_encoding(self, tmp_path):
        """Writes with specified encoding."""
        f = tmp_path / "latin.txt"
        text = "Caf\u00e9"
        count = write_file(f, text, encoding="latin-1")
        raw = f.read_bytes()
        assert raw == text.encode("latin-1")
        assert count == len(text.encode("latin-1"))


# =============================================================================
# detect_file_format
# =============================================================================


class TestDetectFileFormat:
    """Tests for detect_file_format(path, content)."""

    def test_md_extension(self, tmp_path):
        """File with .md extension returns 'markdown'."""
        f = tmp_path / "doc.md"
        assert detect_file_format(f, "anything") == "markdown"

    def test_markdown_extension(self, tmp_path):
        """File with .markdown extension returns 'markdown'."""
        f = tmp_path / "doc.markdown"
        assert detect_file_format(f, "anything") == "markdown"

    def test_wiki_extension(self, tmp_path):
        """File with .wiki extension returns 'tracwiki'."""
        f = tmp_path / "page.wiki"
        assert detect_file_format(f, "anything") == "tracwiki"

    def test_tracwiki_extension(self, tmp_path):
        """File with .tracwiki extension returns 'tracwiki'."""
        f = tmp_path / "page.tracwiki"
        assert detect_file_format(f, "anything") == "tracwiki"

    def test_txt_falls_through_to_heuristic(self, tmp_path):
        """File with .txt extension uses content heuristic."""
        f = tmp_path / "doc.txt"
        # Markdown content (# heading, **bold**, links)
        md_content = "# Hello\n\nSome **bold** text and [link](url)"
        assert detect_file_format(f, md_content) == "markdown"

    def test_unknown_extension_falls_through(self, tmp_path):
        """Unknown extension falls through to content heuristic."""
        f = tmp_path / "doc.rst"
        # TracWiki content (= heading =, '''bold''')
        tw_content = "= Hello =\n\nSome '''bold''' text"
        assert detect_file_format(f, tw_content) == "tracwiki"


# =============================================================================
# read_file_async
# =============================================================================


class TestReadFileAsync:
    """Tests for async read_file_async(path_str)."""

    async def test_reads_file(self, tmp_path):
        """Validates path, reads content, returns tuple."""
        f = tmp_path / "async_read.txt"
        f.write_text("async content", encoding="utf-8")
        content, encoding, resolved = await read_file_async(str(f))
        assert content == "async content"
        assert encoding == "utf-8"
        assert resolved == f.resolve()

    async def test_invalid_path_raises(self):
        """Invalid path raises ValueError."""
        with pytest.raises(ValueError, match="must be absolute"):
            await read_file_async("relative/path.txt")

    async def test_nonexistent_raises(self, tmp_path):
        """Non-existent file raises ValueError."""
        missing = tmp_path / "missing.txt"
        with pytest.raises(ValueError, match="not found"):
            await read_file_async(str(missing))


# =============================================================================
# write_file_async
# =============================================================================


class TestWriteFileAsync:
    """Tests for async write_file_async(path_str, content, encoding)."""

    async def test_writes_file(self, tmp_path):
        """Validates output path, writes content, returns tuple."""
        f = tmp_path / "async_write.txt"
        resolved, count = await write_file_async(
            str(f), "async written"
        )
        assert resolved == f.resolve()
        assert count == len("async written".encode("utf-8"))
        assert f.read_text(encoding="utf-8") == "async written"

    async def test_invalid_path_raises(self):
        """Relative path raises ValueError."""
        with pytest.raises(ValueError, match="must be absolute"):
            await write_file_async("relative/out.txt", "content")

    async def test_parent_not_exists_raises(self, tmp_path):
        """Non-existent parent raises ValueError."""
        bad = tmp_path / "no_dir" / "file.txt"
        with pytest.raises(
            ValueError, match="parent directory not found"
        ):
            await write_file_async(str(bad), "content")
