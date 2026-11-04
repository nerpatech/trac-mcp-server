"""
Processor availability testing through wiki page creation.

Utility functions for testing if specific wiki processors (markdown, rst, textile, html)
are available by creating test pages with processor blocks and checking rendered HTML.
"""

import logging
import time

from ..core.client import WikiPageStoredReadbackFailed

logger = logging.getLogger(__name__)


def check_processor_available(trac_client, processor_name: str) -> bool:
    """
    Test if a wiki processor is available by creating a test page.

    Args:
        trac_client: TracClient instance with wiki operations
        processor_name: Name of processor to test (e.g., 'markdown', 'rst')

    Returns:
        True if processor executed without errors, False otherwise

    Process:
        1. Create unique test page with processor block
        2. Fetch rendered HTML
        3. Check for error indicators (system-message, error)
        4. Clean up test page
        5. Return success/failure
    """
    # Generate unique test page name with timestamp
    test_page = f"_test_processor_{processor_name}_{int(time.time())}"

    try:
        # Create test content with processor block
        test_content = f"""{{{{#!{processor_name}
# Test content
test
}}}}"""

        logger.debug(
            "Testing processor '%s': creating test page '%s'",
            processor_name,
            test_page,
        )

        # Create test page
        try:
            trac_client.put_wiki_page(
                test_page,
                test_content,
                f"Testing {processor_name} processor",
            )
        except WikiPageStoredReadbackFailed as e:
            # The page WAS stored; only its version could not be read
            # back, and this probe never needs it. Carry on, so the page
            # is rendered and cleaned up rather than left behind (Trac
            # /trac_mcp_server #140).
            logger.debug(
                "Test page for processor '%s' stored; read-back failed: %s",
                processor_name,
                e,
            )
        except Exception as e:
            logger.debug(
                "Failed to create test page for processor '%s': %s",
                processor_name,
                e,
            )
            return False

        # Fetch rendered HTML
        try:
            html_content = trac_client.get_wiki_page_html(test_page)
        except Exception as e:
            logger.debug(
                "Failed to fetch HTML for processor '%s' test: %s",
                processor_name,
                e,
            )
            # Still try to clean up
            _cleanup_test_page(trac_client, test_page)
            return False

        # Check for error indicators in HTML
        html_lower = html_content.lower()
        has_error = (
            "system-message" in html_lower or "error" in html_lower
        )

        if has_error:
            logger.debug(
                "Processor '%s' test failed: error indicators found in HTML",
                processor_name,
            )
            result = False
        else:
            logger.debug(
                "Processor '%s' test passed: no error indicators",
                processor_name,
            )
            result = True

        # Clean up test page
        _cleanup_test_page(trac_client, test_page)

        return result

    except Exception as e:
        # Catch-all for unexpected errors
        logger.warning(
            "Unexpected error testing processor '%s': %s",
            processor_name,
            e,
        )
        # Still try to clean up
        _cleanup_test_page(trac_client, test_page)
        return False


def _cleanup_test_page(trac_client, page_name: str) -> None:
    """
    Clean up test page after processor testing.

    Args:
        trac_client: TracClient instance
        page_name: Name of test page to delete
    """
    try:
        logger.debug("Cleaning up test page '%s'", page_name)
        trac_client.delete_wiki_page(page_name)
    except Exception as e:
        # Log but don't fail if cleanup fails
        logger.warning(
            "Failed to clean up test page '%s': %s", page_name, e
        )
