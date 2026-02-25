"""Client for fetching and searching the Gazette of India (egazette.gov.in).

The Gazette of India is the legal document of record for all government
notifications, including the creation, amendment, and operational rules of
government welfare schemes.  Every central scheme is backed by one or more
gazette notifications that give it legal force.

This client allows HaqSetu to:
  1. Search the egazette.gov.in portal for scheme-related notifications.
  2. Fetch detailed metadata for individual gazette notifications.
  3. Verify whether a scheme is backed by an official gazette notification,
     providing a confidence score based on match quality.

Strategy
--------
1. Issue search queries against the egazette.gov.in search interface,
   parsing the returned HTML result pages to extract notification records.
2. Optionally filter by ministry, date range, and gazette type.
3. Cache all parsed results aggressively -- gazette notifications are
   immutable legal documents, so stale-cache risk is minimal.

Rate Limiting
-------------
We are respectful consumers of a government website:
  - Maximum 2 concurrent requests (``asyncio.Semaphore``).
  - 2 second delay between successive requests.
  - Proper ``User-Agent`` identifying HaqSetu.
  - All fetched pages are cached for 12 hours.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from src.services.cache import CacheManager

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://egazette.gov.in"
SEARCH_PATH = "/SearchResult.aspx"

_CACHE_TTL = 12 * 60 * 60  # 12 hours in seconds

# Gazette types as published by the Gazette of India.
GAZETTE_TYPE_ORDINARY = "ordinary"
GAZETTE_TYPE_EXTRAORDINARY = "extraordinary"

# Regex helpers for HTML parsing of egazette search results.
_ROW_RE = re.compile(
    r"<tr[^>]*class=\"[^\"]*gridrow[^\"]*\"[^>]*>(.*?)</tr>",
    re.DOTALL | re.IGNORECASE,
)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_LINK_RE = re.compile(r'href="([^"]*)"', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    return _TAG_RE.sub("", text).strip()


# ---------------------------------------------------------------------------
# GazetteClient
# ---------------------------------------------------------------------------


class GazetteClient:
    """Client for searching and fetching gazette notifications from egazette.gov.in.

    Parameters
    ----------
    cache:
        A :class:`CacheManager` instance for caching HTTP responses.
    rate_limit_delay:
        Seconds to wait between successive HTTP requests.  Defaults to 2.0.
    """

    BASE_URL = BASE_URL

    def __init__(
        self,
        cache: CacheManager,
        rate_limit_delay: float = 2.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
            headers={
                "User-Agent": (
                    "HaqSetu/1.0 (Government Scheme Verification; "
                    "contact: support@haqsetu.in)"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=2,
            ),
        )
        self._cache = cache
        self._rate_limit_delay = rate_limit_delay
        self._semaphore = asyncio.Semaphore(2)
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Rate limiting helper
    # ------------------------------------------------------------------

    async def _throttled_get(self, url: str, **kwargs: object) -> httpx.Response:
        """Issue a GET request with concurrency limiting and polite delays.

        Enforces a minimum inter-request delay of ``rate_limit_delay``
        seconds and limits concurrency via the internal semaphore.

        Parameters
        ----------
        url:
            Relative or absolute URL to fetch.
        **kwargs:
            Additional keyword arguments forwarded to ``httpx.AsyncClient.get``.

        Returns
        -------
        httpx.Response
            The HTTP response object.
        """
        async with self._semaphore:
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self._rate_limit_delay:
                await asyncio.sleep(self._rate_limit_delay - elapsed)

            self._last_request_time = time.monotonic()
            response = await self._client.get(url, **kwargs)  # type: ignore[arg-type]
            return response

    # ------------------------------------------------------------------
    # HTML parsing helpers
    # ------------------------------------------------------------------

    def _parse_search_results(self, html: str) -> list[dict]:
        """Parse gazette search result HTML into structured notification dicts.

        The egazette.gov.in search page returns results in an HTML table
        with rows marked by a ``gridrow`` CSS class.  Each row contains
        cells for title, notification number, gazette type, part/section,
        date, ministry, and a link to the PDF.

        Parameters
        ----------
        html:
            Raw HTML of the search results page.

        Returns
        -------
        list[dict]
            Parsed notification records.  Each dict contains keys:
            ``title``, ``notification_number``, ``gazette_type``,
            ``part``, ``section``, ``date``, ``pdf_url``, ``ministry``.
        """
        results: list[dict] = []
        rows = _ROW_RE.findall(html)

        for row_html in rows:
            cells = _CELL_RE.findall(row_html)
            if len(cells) < 5:
                continue

            # Extract PDF link from the first cell (or any cell with a link)
            pdf_url: str | None = None
            for cell in cells:
                link_match = _LINK_RE.search(cell)
                if link_match:
                    href = link_match.group(1)
                    if href.lower().endswith(".pdf") or "pdf" in href.lower():
                        pdf_url = href
                        if not pdf_url.startswith("http"):
                            pdf_url = f"{self.BASE_URL}/{pdf_url.lstrip('/')}"
                        break

            # Clean cell contents
            cleaned = [_strip_html(c) for c in cells]

            # Determine gazette type from cell content
            gazette_type = GAZETTE_TYPE_ORDINARY
            for cell_text in cleaned:
                if "extraordinary" in cell_text.lower():
                    gazette_type = GAZETTE_TYPE_EXTRAORDINARY
                    break

            # Map cells to fields -- column order may vary, so we use
            # heuristics on content when possible.
            title = cleaned[0] if len(cleaned) > 0 else ""
            notification_number = cleaned[1] if len(cleaned) > 1 else ""
            part = cleaned[2] if len(cleaned) > 2 else ""
            section = cleaned[3] if len(cleaned) > 3 else ""
            date = cleaned[4] if len(cleaned) > 4 else ""
            ministry = cleaned[5] if len(cleaned) > 5 else ""

            record = {
                "title": title,
                "notification_number": notification_number,
                "gazette_type": gazette_type,
                "part": part,
                "section": section,
                "date": date,
                "pdf_url": pdf_url,
                "ministry": ministry,
            }

            results.append(record)

        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_gazette_notifications(
        self,
        scheme_name: str,
        ministry: str | None = None,
    ) -> list[dict]:
        """Search egazette.gov.in for notifications matching a scheme name.

        Issues a search query against the gazette portal and parses the
        HTML result page to extract matching notifications.

        Parameters
        ----------
        scheme_name:
            Name (or partial name) of the government scheme to search for.
        ministry:
            Optional ministry name to narrow the search.

        Returns
        -------
        list[dict]
            List of notification dicts with keys: ``title``,
            ``notification_number``, ``gazette_type``, ``part``,
            ``section``, ``date``, ``pdf_url``, ``ministry``.
        """
        query = scheme_name.strip()
        if ministry:
            query = f"{query} {ministry.strip()}"

        cache_key = f"gazette:search:{query.lower()}"
        cached: list[dict] | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "gazette.search_from_cache",
                query=query,
                count=len(cached),
            )
            return cached

        logger.info("gazette.searching", query=query)

        try:
            params = {"keyword": query}
            if ministry:
                params["ministry"] = ministry.strip()

            response = await self._throttled_get(
                SEARCH_PATH, params=params  # type: ignore[arg-type]
            )

            if response.status_code != 200:
                logger.warning(
                    "gazette.search_http_error",
                    status=response.status_code,
                    query=query,
                )
                return []

            results = self._parse_search_results(response.text)
            await self._cache.set(cache_key, results, ttl_seconds=_CACHE_TTL)

            logger.info(
                "gazette.search_complete",
                query=query,
                results_count=len(results),
            )
            return results

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "gazette.search_http_status_error",
                status=exc.response.status_code,
                query=query,
            )
            return []
        except Exception:
            logger.warning(
                "gazette.search_failed", query=query, exc_info=True
            )
            return []

    async def fetch_notification_detail(
        self,
        notification_id: str,
    ) -> dict | None:
        """Fetch details of a specific gazette notification.

        Retrieves the detail page for a gazette notification and parses
        out the available metadata fields.

        Parameters
        ----------
        notification_id:
            The unique identifier for the notification on egazette.gov.in.

        Returns
        -------
        dict | None
            Parsed notification metadata with keys such as ``title``,
            ``notification_number``, ``gazette_type``, ``date``,
            ``ministry``, ``pdf_url``, and ``full_text_snippet``.
            Returns ``None`` if the notification could not be fetched
            or parsed.
        """
        cache_key = f"gazette:detail:{notification_id}"
        cached: dict | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "gazette.detail_from_cache",
                notification_id=notification_id,
            )
            return cached

        logger.info(
            "gazette.fetching_detail", notification_id=notification_id
        )

        try:
            url = f"/Details.aspx?Id={notification_id}"
            response = await self._throttled_get(url)

            if response.status_code == 404:
                logger.warning(
                    "gazette.notification_not_found",
                    notification_id=notification_id,
                )
                return None

            if response.status_code != 200:
                logger.warning(
                    "gazette.detail_http_error",
                    status=response.status_code,
                    notification_id=notification_id,
                )
                return None

            html = response.text
            detail = self._parse_notification_detail(html, notification_id)

            if detail is not None:
                await self._cache.set(
                    cache_key, detail, ttl_seconds=_CACHE_TTL
                )

            return detail

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "gazette.detail_http_status_error",
                status=exc.response.status_code,
                notification_id=notification_id,
            )
            return None
        except Exception:
            logger.warning(
                "gazette.detail_fetch_failed",
                notification_id=notification_id,
                exc_info=True,
            )
            return None

    def _parse_notification_detail(
        self,
        html: str,
        notification_id: str,
    ) -> dict | None:
        """Parse a gazette notification detail page into structured metadata.

        Parameters
        ----------
        html:
            Raw HTML of the notification detail page.
        notification_id:
            The notification ID (used as a fallback identifier).

        Returns
        -------
        dict | None
            Parsed notification metadata, or ``None`` if parsing fails.
        """
        # Extract fields using common label patterns on the detail page.
        def _extract_field(label: str) -> str:
            pattern = re.compile(
                rf"{re.escape(label)}\s*[:]\s*</[^>]+>\s*<[^>]+>(.*?)</",
                re.DOTALL | re.IGNORECASE,
            )
            match = pattern.search(html)
            if match:
                return _strip_html(match.group(1)).strip()
            # Fallback: try a simpler label-value pattern
            pattern_simple = re.compile(
                rf"{re.escape(label)}\s*[:]\s*(.*?)(?:<|$)",
                re.DOTALL | re.IGNORECASE,
            )
            match_simple = pattern_simple.search(html)
            if match_simple:
                return _strip_html(match_simple.group(1)).strip()
            return ""

        title = (
            _extract_field("Subject")
            or _extract_field("Title")
            or _extract_field("Notification")
        )
        notification_number = (
            _extract_field("Notification No")
            or _extract_field("Gazette Notification No")
            or _extract_field("No.")
        )
        gazette_type = _extract_field("Gazette Type") or _extract_field("Type")
        date = (
            _extract_field("Gazette Date")
            or _extract_field("Date")
            or _extract_field("Published Date")
        )
        ministry = (
            _extract_field("Ministry")
            or _extract_field("Department")
            or _extract_field("Issuing Authority")
        )

        # Determine gazette type enum value
        gazette_type_normalised = GAZETTE_TYPE_ORDINARY
        if "extraordinary" in gazette_type.lower():
            gazette_type_normalised = GAZETTE_TYPE_EXTRAORDINARY

        # Try to find PDF link
        pdf_url: str | None = None
        pdf_match = re.search(
            r'href="([^"]*\.pdf[^"]*)"', html, re.IGNORECASE
        )
        if pdf_match:
            pdf_url = pdf_match.group(1)
            if not pdf_url.startswith("http"):
                pdf_url = f"{self.BASE_URL}/{pdf_url.lstrip('/')}"

        # Extract a text snippet from the page body
        body_match = re.search(
            r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        full_text_snippet = ""
        if body_match:
            full_text_snippet = _strip_html(body_match.group(1))[:500]

        if not title and not notification_number:
            logger.warning(
                "gazette.detail_parse_empty",
                notification_id=notification_id,
            )
            return None

        return {
            "notification_id": notification_id,
            "title": title,
            "notification_number": notification_number,
            "gazette_type": gazette_type_normalised,
            "date": date,
            "ministry": ministry,
            "pdf_url": pdf_url,
            "full_text_snippet": full_text_snippet,
        }

    async def verify_scheme_in_gazette(
        self,
        scheme_name: str,
        ministry: str | None = None,
        gazette_number: str | None = None,
    ) -> dict:
        """Verify whether a scheme is backed by an official gazette notification.

        This is the high-level verification method.  It searches the gazette
        for the given scheme, then scores the match quality to produce a
        confidence value.

        Confidence scoring:
          - **1.0** -- exact match on gazette notification number.
          - **0.8** -- exact scheme name found in a notification title.
          - **0.5** -- partial / fuzzy name match in notification titles.
          - **0.0** -- no matching notifications found.

        Parameters
        ----------
        scheme_name:
            Name of the government scheme to verify.
        ministry:
            Optional ministry name to narrow the search.
        gazette_number:
            Optional gazette notification number.  If provided and found,
            the confidence is set to 1.0 (authoritative match).

        Returns
        -------
        dict
            Verification result with keys:
            ``found`` (bool), ``notifications`` (list[dict]),
            ``confidence`` (float), ``search_query`` (str).
        """
        search_query = scheme_name.strip()
        if ministry:
            search_query = f"{search_query} {ministry.strip()}"

        logger.info(
            "gazette.verify_scheme",
            scheme_name=scheme_name,
            ministry=ministry,
            gazette_number=gazette_number,
        )

        notifications = await self.search_gazette_notifications(
            scheme_name=scheme_name,
            ministry=ministry,
        )

        if not notifications:
            logger.info(
                "gazette.verify_not_found",
                scheme_name=scheme_name,
            )
            return {
                "found": False,
                "notifications": [],
                "confidence": 0.0,
                "search_query": search_query,
            }

        # Score confidence based on match quality
        confidence = 0.0
        scheme_name_lower = scheme_name.strip().lower()

        # Check for gazette number match first (highest confidence)
        if gazette_number:
            gazette_number_lower = gazette_number.strip().lower()
            for notification in notifications:
                notif_number = (
                    notification.get("notification_number", "").strip().lower()
                )
                if notif_number and gazette_number_lower == notif_number:
                    confidence = 1.0
                    logger.info(
                        "gazette.verify_exact_number_match",
                        scheme_name=scheme_name,
                        gazette_number=gazette_number,
                    )
                    break

        # Check for exact name match in titles
        if confidence < 1.0:
            for notification in notifications:
                title = notification.get("title", "").lower()
                if scheme_name_lower in title:
                    confidence = max(confidence, 0.8)
                    break

        # If still no strong match, treat as partial match
        if confidence < 0.8:
            # Check for partial keyword overlap
            scheme_words = set(scheme_name_lower.split())
            for notification in notifications:
                title_words = set(
                    notification.get("title", "").lower().split()
                )
                overlap = scheme_words & title_words
                if len(overlap) >= max(1, len(scheme_words) // 2):
                    confidence = max(confidence, 0.5)
                    break

        found = confidence > 0.0

        logger.info(
            "gazette.verify_complete",
            scheme_name=scheme_name,
            found=found,
            confidence=confidence,
            notification_count=len(notifications),
        )

        return {
            "found": found,
            "notifications": notifications,
            "confidence": confidence,
            "search_query": search_query,
        }

    async def search_by_ministry(
        self,
        ministry: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        """Search all gazette notifications issued by a given ministry.

        Parameters
        ----------
        ministry:
            Name of the issuing ministry or department
            (e.g. ``"Ministry of Rural Development"``).
        date_from:
            Optional start date filter in ``DD/MM/YYYY`` format.
        date_to:
            Optional end date filter in ``DD/MM/YYYY`` format.

        Returns
        -------
        list[dict]
            List of notification dicts matching the ministry, each with
            keys: ``title``, ``notification_number``, ``gazette_type``,
            ``part``, ``section``, ``date``, ``pdf_url``, ``ministry``.
        """
        ministry_stripped = ministry.strip()
        cache_key = (
            f"gazette:ministry:{ministry_stripped.lower()}"
            f":{date_from or ''}:{date_to or ''}"
        )
        cached: list[dict] | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "gazette.ministry_search_from_cache",
                ministry=ministry_stripped,
                count=len(cached),
            )
            return cached

        logger.info(
            "gazette.searching_by_ministry",
            ministry=ministry_stripped,
            date_from=date_from,
            date_to=date_to,
        )

        try:
            params: dict[str, str] = {"ministry": ministry_stripped}
            if date_from:
                params["fromdate"] = date_from
            if date_to:
                params["todate"] = date_to

            response = await self._throttled_get(
                SEARCH_PATH, params=params  # type: ignore[arg-type]
            )

            if response.status_code != 200:
                logger.warning(
                    "gazette.ministry_search_http_error",
                    status=response.status_code,
                    ministry=ministry_stripped,
                )
                return []

            results = self._parse_search_results(response.text)
            await self._cache.set(cache_key, results, ttl_seconds=_CACHE_TTL)

            logger.info(
                "gazette.ministry_search_complete",
                ministry=ministry_stripped,
                results_count=len(results),
            )
            return results

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "gazette.ministry_search_http_status_error",
                status=exc.response.status_code,
                ministry=ministry_stripped,
            )
            return []
        except Exception:
            logger.warning(
                "gazette.ministry_search_failed",
                ministry=ministry_stripped,
                exc_info=True,
            )
            return []
