"""
Web scraping utilities for detecting Trac capabilities.

Scrapes the /about page to extract version and plugin information.
"""

import logging
from typing import Any
from urllib.parse import urljoin

import requests
from lxml import html

logger = logging.getLogger(__name__)


def scrape_about_page(
    base_url: str, auth_tuple: tuple[str, str]
) -> dict[str, Any]:
    """
    Scrape Trac /about page to extract version and plugin information.

    Args:
        base_url: Trac base URL (e.g., "https://trac.example.com")
        auth_tuple: (username, password) for HTTP Basic authentication

    Returns:
        Dict with keys:
        - trac_version: str or None
        - plugins: dict mapping plugin name to version string
        - markdown_processor: str or None (e.g., "MarkdownMacro")

    Returns empty dict if scraping fails (permission denied, connection error, etc.).
    """
    try:
        # Construct /about URL
        about_url = f"{base_url.rstrip('/')}/about"

        # Make request with Basic auth
        response = requests.get(about_url, auth=auth_tuple, timeout=10)
        response.raise_for_status()

        # Parse HTML with lxml
        tree = html.fromstring(response.content)

        result = {
            "trac_version": None,
            "plugins": {},
            "markdown_processor": None,
        }

        # Extract Trac version from h1 or h2 heading
        # Flexible selector to handle different Trac versions
        version_headings = tree.xpath(
            '//h1[contains(text(), "Trac")] | //h2[contains(text(), "Trac")]'
        )
        if version_headings:
            version_text = version_headings[0].text_content().strip()
            # Extract version number (e.g., "Trac 1.6.1" -> "1.6.1")
            parts = version_text.split()
            if len(parts) >= 2:
                result["trac_version"] = parts[1]
                logger.debug(
                    "Extracted Trac version: %s", result["trac_version"]
                )

        # Extract plugins list from Plugins section
        # Use flexible XPath with contains() to handle version differences
        plugins_dl = tree.xpath(
            '//div[@id="info"]//h2[contains(text(), "Plugins")]/../dl'
        )

        if plugins_dl:
            dl_element = plugins_dl[0]

            # Parse plugin list: dt contains name, dd contains version/description
            dt_elements = dl_element.xpath(".//dt")

            for dt in dt_elements:
                plugin_name = dt.text_content().strip()

                # Get corresponding dd (following-sibling)
                dd_elements = dt.xpath("./following-sibling::dd[1]")
                if dd_elements:
                    plugin_info = dd_elements[0].text_content().strip()
                    result["plugins"][plugin_name] = plugin_info
                else:
                    result["plugins"][plugin_name] = ""

            logger.debug("Extracted %d plugins", len(result["plugins"]))

        # Check for markdown processor
        if "MarkdownMacro" in result["plugins"]:
            result["markdown_processor"] = "MarkdownMacro"
            logger.debug("Detected MarkdownMacro processor")

        return result

    except requests.HTTPError as e:
        if e.response.status_code == 403:
            logger.warning(
                "Web scraping failed: CONFIG_VIEW permission denied (HTTP 403)"
            )
        else:
            logger.warning(
                "Web scraping failed: HTTP %d", e.response.status_code
            )
        return {}

    except requests.RequestException as e:
        logger.warning("Web scraping failed: connection error - %s", e)
        return {}

    except Exception as e:
        logger.warning("Web scraping failed: unexpected error - %s", e)
        return {}


def scrape_project_index(
    host_url: str, auth_tuple: tuple[str, str] | None = None
) -> list[dict[str, Any]]:
    """
    Scrape a multi-project Trac host's root "Available Projects" page.

    Args:
        host_url: Trac host root URL (e.g. "http://host:8000/")
        auth_tuple: Optional (username, password) for HTTP Basic authentication.
            The root page is typically unauthenticated, but auth is passed
            through when available in case a deployment restricts it.

    Returns:
        List of dicts with keys: path, title, description, url -- one per
        project link found in the page's ``<ul><li><a>`` listing.
        Returns an empty list on any failure (never raises).
    """
    try:
        response = requests.get(host_url, auth=auth_tuple, timeout=10)
        response.raise_for_status()

        tree = html.fromstring(response.content)

        results = []
        for link in tree.xpath("//ul/li/a"):
            href = link.get("href")
            if not href:
                continue
            results.append(
                {
                    "path": href,
                    "title": link.text_content().strip(),
                    "description": link.get("title", ""),
                    "url": urljoin(host_url, href),
                }
            )

        logger.debug(
            "Discovered %d projects at %s", len(results), host_url
        )
        return results

    except requests.HTTPError as e:
        logger.warning(
            "Project index scrape failed: HTTP %d",
            e.response.status_code,
        )
        return []

    except requests.RequestException as e:
        logger.warning(
            "Project index scrape failed: connection error - %s", e
        )
        return []

    except Exception as e:
        logger.warning(
            "Project index scrape failed: unexpected error - %s", e
        )
        return []
