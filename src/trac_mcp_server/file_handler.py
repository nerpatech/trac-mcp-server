"""File handler module: path validation, encoding-aware read/write, format detection.

Provides the core file I/O infrastructure for file-based wiki tools.
All sync functions are pure (no side effects besides file I/O).
Async wrappers compose validation + I/O via run_sync().
"""

from pathlib import Path

from charset_normalizer import from_bytes

from trac_mcp_server.converters.common import detect_format_heuristic
from trac_mcp_server.core.async_utils import run_sync

# =============================================================================
# Path Validation
# =============================================================================


def validate_file_path(path_str: str) -> Path:
    """Validate and resolve an input file path.

    Args:
        path_str: Absolute path string to an existing file.

    Returns:
        Resolved Path object pointing to the real file.

    Raises:
        ValueError: If path is relative, doesn't exist, or is not a file.
    """
    path = Path(path_str)
    if not path.is_absolute():
        raise ValueError(f"Path must be absolute: {path_str}")
    resolved = path.resolve()
    if not resolved.exists():
        raise ValueError(f"File not found: {path_str}")
    if not resolved.is_file():
        raise ValueError(f"Path is not a file: {path_str}")
    return resolved


def validate_output_path(
    path_str: str, base_dir: str | None = None
) -> Path:
    """Validate an output file path (file need not exist, but parent must).

    Args:
        path_str: Absolute path string for the output file.
        base_dir: Optional base directory; output must be under this directory.

    Returns:
        Resolved Path object for the output file.

    Raises:
        ValueError: If path is relative, parent doesn't exist, or path is outside base_dir.
    """
    path = Path(path_str)
    if not path.is_absolute():
        raise ValueError(f"Path must be absolute: {path_str}")
    resolved = path.resolve()
    if not resolved.parent.exists():
        raise ValueError(
            f"Output parent directory not found: {resolved.parent}"
        )
    if base_dir is not None:
        base_resolved = Path(base_dir).resolve()
        if not resolved.is_relative_to(base_resolved):
            raise ValueError(
                f"Output path is outside base directory: {resolved} not under {base_resolved}"
            )
    return resolved


# =============================================================================
# File Read/Write
# =============================================================================


def read_file_with_encoding(path: Path) -> tuple[str, str]:
    """Read a file with automatic encoding detection.

    Tries a strict UTF-8 decode first. A file that decodes cleanly as
    UTF-8 *is* UTF-8 for our purposes -- there's no reason to let a
    probabilistic detector overrule a successful strict decode. Only
    when that fails does this fall back to charset-normalizer, since a
    guess is only needed once the unambiguous answer has been ruled out.

    Falling straight to charset-normalizer let it override a correct
    UTF-8 decode with a wrong single-byte-codepage guess on some inputs
    (ticket #48) -- e.g. a `cp1250` misdetection that silently turned
    every em dash into a three-character mojibake sequence on write.

    Defaults to UTF-8 for empty files or when detection fails.

    Args:
        path: Path to the file to read.

    Returns:
        Tuple of (content_string, detected_encoding).
    """
    raw = path.read_bytes()
    if not raw:
        return ("", "utf-8")

    try:
        return (raw.decode("utf-8"), "utf-8")
    except UnicodeDecodeError:
        pass

    result = from_bytes(raw).best()
    if result is None:
        # Detection failed, fall back to utf-8
        encoding = "utf-8"
        content = raw.decode(encoding, errors="replace")
    else:
        encoding = result.encoding
        # Normalize ascii to utf-8 (ascii is a strict subset of utf-8)
        if encoding == "ascii":
            encoding = "utf-8"
        content = str(result)
    return (content, encoding)


def write_file(
    path: Path, content: str, encoding: str = "utf-8"
) -> int:
    """Write content to a file, creating parent directories as needed.

    Args:
        path: Path to the output file.
        content: String content to write.
        encoding: Encoding to use (default: utf-8).

    Returns:
        Number of bytes written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode(encoding)
    path.write_bytes(encoded)
    return len(encoded)


# =============================================================================
# Format Detection
# =============================================================================


_EXTENSION_FORMAT_MAP: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".wiki": "tracwiki",
    ".tracwiki": "tracwiki",
}


def detect_file_format(path: Path, content: str) -> str:
    """Detect file format from extension, falling back to content heuristic.

    Checks file extension first for unambiguous formats (.md, .markdown,
    .wiki, .tracwiki). For other extensions (.txt, .rst, etc.), falls
    through to content-based heuristic detection.

    Args:
        path: Path to the file (used for extension check).
        content: File content string (used for heuristic fallback).

    Returns:
        Format string: 'markdown' or 'tracwiki'.
    """
    suffix = path.suffix.lower()
    if suffix in _EXTENSION_FORMAT_MAP:
        return _EXTENSION_FORMAT_MAP[suffix]
    # Fall through to content-based heuristic
    return detect_format_heuristic(content)


# =============================================================================
# Async Wrappers
# =============================================================================


async def read_file_async(path_str: str) -> tuple[str, str, Path]:
    """Async wrapper: validate path, read file with encoding detection.

    Args:
        path_str: Absolute path string to an existing file.

    Returns:
        Tuple of (content_string, detected_encoding, resolved_path).

    Raises:
        ValueError: If path validation fails.
    """
    resolved = await run_sync(validate_file_path, path_str)
    content, encoding = await run_sync(
        read_file_with_encoding, resolved
    )
    return (content, encoding, resolved)


async def write_file_async(
    path_str: str, content: str, encoding: str = "utf-8"
) -> tuple[Path, int]:
    """Async wrapper: validate output path, write file.

    Args:
        path_str: Absolute path string for the output file.
        content: String content to write.
        encoding: Encoding to use (default: utf-8).

    Returns:
        Tuple of (resolved_path, bytes_written).

    Raises:
        ValueError: If output path validation fails.
    """
    resolved = await run_sync(validate_output_path, path_str)
    count = await run_sync(write_file, resolved, content, encoding)
    return (resolved, count)
