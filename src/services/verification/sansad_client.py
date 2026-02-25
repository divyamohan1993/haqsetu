"""Client for verifying government schemes against Parliament records on sansad.in.

Sansad.in is the official website of the Parliament of India, hosting records
of Lok Sabha and Rajya Sabha proceedings, bills, acts, debates, and
parliamentary questions.  This client searches those records to verify
whether a government scheme has a legislative backing or has been discussed
in Parliament.

Strategy
--------
1. Search for bills related to a scheme name across Lok Sabha and/or Rajya
   Sabha sessions.
2. Search for Acts that enable or create a particular scheme.
3. Search parliamentary questions (starred and unstarred) that mention the
   scheme, often revealing ministerial accountability and status updates.
4. Combine results into a confidence score indicating how strongly a scheme
   is grounded in parliamentary records.

Rate Limiting
-------------
We are respectful consumers of a government website:
  - Maximum 2 concurrent requests (``asyncio.Semaphore``).
  - 2-second delay between successive requests.
  - Proper ``User-Agent`` identifying HaqSetu.
  - All fetched pages are cached for 12 hours.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
import structlog

if TYPE_CHECKING:
    from src.services.cache import CacheManager

logger = structlog.get_logger(__name__)

# Allowed domains for external URL fetching (SSRF protection)
_ALLOWED_EXTERNAL_DOMAINS: frozenset[str] = frozenset({
    "sansad.in",
    "www.sansad.in",
    "rajyasabha.nic.in",
    "loksabha.nic.in",
})

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PAGE_CACHE_TTL = 12 * 60 * 60  # 12 hours in seconds

# House identifiers used in search filtering.
_VALID_HOUSES = {"lok_sabha", "rajya_sabha"}


# ---------------------------------------------------------------------------
# SansadClient
# ---------------------------------------------------------------------------


class SansadClient:
    """Client for verifying schemes against Parliament records on sansad.in.

    Searches Lok Sabha and Rajya Sabha records for bills, acts, and
    parliamentary questions related to government schemes.

    Parameters
    ----------
    cache:
        A :class:`CacheManager` instance for caching HTTP responses.
    rate_limit_delay:
        Seconds to wait between successive HTTP requests.  Defaults to 2.0.
    """

    BASE_URL = "https://sansad.in"

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
                "Accept": "text/html,application/json",
            },
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=5,
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

    async def _throttled_get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Issue a GET request with concurrency limiting and polite delays.

        Parameters
        ----------
        url:
            The URL path (relative to ``BASE_URL``) to request.
        **kwargs:
            Additional keyword arguments forwarded to ``httpx.AsyncClient.get``.

        Returns
        -------
        httpx.Response
            The HTTP response object.
        """
        async with self._semaphore:
            # Enforce minimum delay between requests
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self._rate_limit_delay:
                await asyncio.sleep(self._rate_limit_delay - elapsed)

            self._last_request_time = time.monotonic()
            response = await self._client.get(url, **kwargs)
            return response

    # ------------------------------------------------------------------
    # Bill search
    # ------------------------------------------------------------------

    async def search_bills(
        self,
        scheme_name: str,
        house: str | None = None,
    ) -> list[dict]:
        """Search sansad.in for bills related to a government scheme.

        Parameters
        ----------
        scheme_name:
            The name of the government scheme to search for (e.g.
            ``"Pradhan Mantri Jan Dhan Yojana"``).
        house:
            Filter by parliamentary house: ``"lok_sabha"``,
            ``"rajya_sabha"``, or ``None`` to search both houses.

        Returns
        -------
        list[dict]
            List of bill records.  Each record contains:

            - ``title`` -- bill title
            - ``bill_number`` -- official bill number
            - ``house`` -- ``"lok_sabha"`` or ``"rajya_sabha"``
            - ``session`` -- parliamentary session identifier
            - ``date_introduced`` -- date the bill was introduced
            - ``date_passed`` -- date the bill was passed (if applicable)
            - ``status`` -- current status (e.g. ``"passed"``, ``"pending"``,
              ``"withdrawn"``, ``"lapsed"``)
            - ``url`` -- full URL to the bill page on sansad.in
        """
        if house is not None and house not in _VALID_HOUSES:
            logger.warning(
                "sansad.invalid_house_filter",
                house=house,
                valid=sorted(_VALID_HOUSES),
            )
            house = None

        cache_key = f"sansad:bills:{scheme_name}:{house or 'both'}"
        cached: list[dict] | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "sansad.bills_from_cache",
                scheme_name=scheme_name,
                count=len(cached),
            )
            return cached

        houses = [house] if house else ["lok_sabha", "rajya_sabha"]
        all_bills: list[dict] = []

        for h in houses:
            try:
                bills = await self._fetch_bills_for_house(scheme_name, h)
                all_bills.extend(bills)
            except Exception:
                logger.warning(
                    "sansad.bill_search_failed",
                    scheme_name=scheme_name,
                    house=h,
                    exc_info=True,
                )

        if all_bills:
            await self._cache.set(cache_key, all_bills, ttl_seconds=_PAGE_CACHE_TTL)

        logger.info(
            "sansad.bills_search_complete",
            scheme_name=scheme_name,
            house=house,
            count=len(all_bills),
        )
        return all_bills

    async def _fetch_bills_for_house(
        self,
        scheme_name: str,
        house: str,
    ) -> list[dict]:
        """Fetch bills matching a scheme name from a specific house.

        Parameters
        ----------
        scheme_name:
            The scheme name to search for.
        house:
            The parliamentary house (``"lok_sabha"`` or ``"rajya_sabha"``).

        Returns
        -------
        list[dict]
            List of bill records for the specified house.
        """
        house_path = "Loksabha" if house == "lok_sabha" else "Rajyasabha"
        url = f"/{house_path}/bills"
        params = {"search": scheme_name}

        response = await self._throttled_get(url, params=params)

        if response.status_code != 200:
            logger.warning(
                "sansad.bill_fetch_http_error",
                status=response.status_code,
                house=house,
            )
            return []

        return self._parse_bill_results(response, house)

    def _parse_bill_results(
        self,
        response: httpx.Response,
        house: str,
    ) -> list[dict]:
        """Parse bill search results from an HTTP response.

        Attempts to parse JSON first; falls back to HTML parsing if the
        response is not JSON.

        Parameters
        ----------
        response:
            The HTTP response from the bills search endpoint.
        house:
            The parliamentary house the bills belong to.

        Returns
        -------
        list[dict]
            Parsed bill records.
        """
        bills: list[dict] = []

        # Attempt JSON parsing first
        try:
            data = response.json()
            records = data if isinstance(data, list) else data.get("data", [])
            for record in records:
                if not isinstance(record, dict):
                    continue
                bill = {
                    "title": record.get("title", ""),
                    "bill_number": record.get("bill_number", record.get("billNumber", "")),
                    "house": house,
                    "session": record.get("session", ""),
                    "date_introduced": record.get("date_introduced", record.get("dateIntroduced", "")),
                    "date_passed": record.get("date_passed", record.get("datePassed")),
                    "status": record.get("status", "unknown"),
                    "url": self._build_url(record.get("url", record.get("link", ""))),
                }
                bills.append(bill)
            return bills
        except (ValueError, KeyError):
            pass

        # Fallback: attempt to extract structured data from HTML
        # The sansad.in website may return HTML with tabular data
        try:
            text = response.text
            # Look for JSON-LD or embedded data blocks
            import json
            import re

            json_blocks = re.findall(
                r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.*?)</script>',
                text,
                re.DOTALL,
            )
            for block in json_blocks:
                try:
                    block_data = json.loads(block)
                    if isinstance(block_data, list):
                        for item in block_data:
                            if isinstance(item, dict) and "title" in item:
                                bills.append({
                                    "title": item.get("title", ""),
                                    "bill_number": item.get("bill_number", ""),
                                    "house": house,
                                    "session": item.get("session", ""),
                                    "date_introduced": item.get("date_introduced", ""),
                                    "date_passed": item.get("date_passed"),
                                    "status": item.get("status", "unknown"),
                                    "url": self._build_url(item.get("url", "")),
                                })
                except (json.JSONDecodeError, ValueError):
                    continue
        except Exception:
            logger.debug("sansad.html_parse_fallback_failed", exc_info=True)

        return bills

    # ------------------------------------------------------------------
    # Act search
    # ------------------------------------------------------------------

    async def search_acts(self, scheme_name: str) -> list[dict]:
        """Search for Acts of Parliament that enable or create a scheme.

        Parameters
        ----------
        scheme_name:
            The name of the government scheme to search for.

        Returns
        -------
        list[dict]
            List of act records.  Each record contains:

            - ``title`` -- act title
            - ``act_number`` -- official act number
            - ``year`` -- year of enactment
            - ``date_assent`` -- date of presidential assent
            - ``status`` -- current status (e.g. ``"in_force"``, ``"repealed"``,
              ``"amended"``)
            - ``url`` -- full URL to the act page on sansad.in
        """
        cache_key = f"sansad:acts:{scheme_name}"
        cached: list[dict] | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "sansad.acts_from_cache",
                scheme_name=scheme_name,
                count=len(cached),
            )
            return cached

        logger.info("sansad.searching_acts", scheme_name=scheme_name)

        acts: list[dict] = []

        try:
            url = "/acts"
            params = {"search": scheme_name}
            response = await self._throttled_get(url, params=params)

            if response.status_code != 200:
                logger.warning(
                    "sansad.acts_search_http_error",
                    status=response.status_code,
                )
                return []

            acts = self._parse_act_results(response)
        except Exception:
            logger.warning(
                "sansad.acts_search_failed",
                scheme_name=scheme_name,
                exc_info=True,
            )

        if acts:
            await self._cache.set(cache_key, acts, ttl_seconds=_PAGE_CACHE_TTL)

        logger.info(
            "sansad.acts_search_complete",
            scheme_name=scheme_name,
            count=len(acts),
        )
        return acts

    def _parse_act_results(self, response: httpx.Response) -> list[dict]:
        """Parse act search results from an HTTP response.

        Parameters
        ----------
        response:
            The HTTP response from the acts search endpoint.

        Returns
        -------
        list[dict]
            Parsed act records.
        """
        acts: list[dict] = []

        try:
            data = response.json()
            records = data if isinstance(data, list) else data.get("data", [])
            for record in records:
                if not isinstance(record, dict):
                    continue
                act = {
                    "title": record.get("title", ""),
                    "act_number": record.get("act_number", record.get("actNumber", "")),
                    "year": record.get("year", record.get("enactmentYear")),
                    "date_assent": record.get("date_assent", record.get("dateOfAssent", "")),
                    "status": record.get("status", "unknown"),
                    "url": self._build_url(record.get("url", record.get("link", ""))),
                }
                acts.append(act)
        except (ValueError, KeyError):
            logger.debug("sansad.acts_json_parse_failed", exc_info=True)

        return acts

    # ------------------------------------------------------------------
    # High-level verification
    # ------------------------------------------------------------------

    async def verify_scheme_in_parliament(
        self,
        scheme_name: str,
        ministry: str | None = None,
    ) -> dict:
        """Verify whether a scheme has been discussed or legislated in Parliament.

        This is a high-level method that searches both bills and acts for
        the given scheme name, then computes a confidence score based on the
        number and relevance of matches found.

        Parameters
        ----------
        scheme_name:
            The name of the government scheme to verify.
        ministry:
            Optional ministry name to narrow the search.  If provided, the
            search query is enriched with the ministry name to reduce false
            positives.

        Returns
        -------
        dict
            Verification result with keys:

            - ``found`` -- ``True`` if any parliamentary records were found
            - ``bills`` -- list of matching bill records
            - ``acts`` -- list of matching act records
            - ``confidence`` -- float between 0.0 and 1.0 indicating how
              strongly the scheme is supported by parliamentary records
            - ``search_query`` -- the query string that was used
        """
        search_query = scheme_name
        if ministry:
            search_query = f"{scheme_name} {ministry}"

        cache_key = f"sansad:verify:{search_query}"
        cached: dict | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "sansad.verify_from_cache",
                scheme_name=scheme_name,
            )
            return cached

        logger.info(
            "sansad.verifying_scheme",
            scheme_name=scheme_name,
            ministry=ministry,
        )

        # Search bills and acts concurrently
        bills_task = self.search_bills(scheme_name)
        acts_task = self.search_acts(scheme_name)

        bills_result, acts_result = await asyncio.gather(
            bills_task,
            acts_task,
            return_exceptions=True,
        )

        bills: list[dict] = (
            bills_result if isinstance(bills_result, list) else []
        )
        acts: list[dict] = (
            acts_result if isinstance(acts_result, list) else []
        )

        if isinstance(bills_result, Exception):
            logger.warning(
                "sansad.verify_bills_error",
                error=str(bills_result),
            )
        if isinstance(acts_result, Exception):
            logger.warning(
                "sansad.verify_acts_error",
                error=str(acts_result),
            )

        # Compute confidence score
        confidence = self._compute_confidence(bills, acts, scheme_name)
        found = len(bills) > 0 or len(acts) > 0

        result = {
            "found": found,
            "bills": bills,
            "acts": acts,
            "confidence": confidence,
            "search_query": search_query,
        }

        await self._cache.set(cache_key, result, ttl_seconds=_PAGE_CACHE_TTL)

        logger.info(
            "sansad.verification_complete",
            scheme_name=scheme_name,
            found=found,
            confidence=confidence,
            bill_count=len(bills),
            act_count=len(acts),
        )
        return result

    def _compute_confidence(
        self,
        bills: list[dict],
        acts: list[dict],
        scheme_name: str,
    ) -> float:
        """Compute a confidence score for parliamentary verification.

        The score is based on:
        - Whether any bills or acts were found (base signal).
        - Whether bill/act titles closely match the scheme name.
        - Whether any acts have been passed (stronger evidence).
        - Whether bills have been introduced in both houses.

        Parameters
        ----------
        bills:
            List of bill records found.
        acts:
            List of act records found.
        scheme_name:
            The original scheme name for title matching.

        Returns
        -------
        float
            Confidence score between 0.0 and 1.0.
        """
        if not bills and not acts:
            return 0.0

        score = 0.0
        scheme_lower = scheme_name.lower()

        # Acts are strong evidence (up to 0.5)
        if acts:
            score += 0.3
            for act in acts:
                title = act.get("title", "").lower()
                if scheme_lower in title or title in scheme_lower:
                    score += 0.1
                    break
            # Acts that are in force are even stronger
            for act in acts:
                if act.get("status", "").lower() in ("in_force", "active", "enacted"):
                    score += 0.1
                    break

        # Bills provide moderate evidence (up to 0.4)
        if bills:
            score += 0.2
            for bill in bills:
                title = bill.get("title", "").lower()
                if scheme_lower in title or title in scheme_lower:
                    score += 0.1
                    break
            # Passed bills are stronger
            for bill in bills:
                if bill.get("status", "").lower() in ("passed", "enacted", "assented"):
                    score += 0.1
                    break

        # Both houses discussing = broader parliamentary support
        houses_found = {b.get("house") for b in bills if b.get("house")}
        if len(houses_found) > 1:
            score += 0.1

        return min(score, 1.0)

    # ------------------------------------------------------------------
    # Bill detail
    # ------------------------------------------------------------------

    async def fetch_bill_detail(self, bill_url: str) -> dict | None:
        """Fetch the full detail page for a specific bill.

        Parameters
        ----------
        bill_url:
            The full URL or relative path to the bill page on sansad.in.

        Returns
        -------
        dict | None
            Detailed bill information including full text references,
            committee reports, and amendment history.  Returns ``None``
            if the page could not be fetched or parsed.
        """
        cache_key = f"sansad:bill_detail:{bill_url}"
        cached: dict | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug("sansad.bill_detail_from_cache", url=bill_url)
            return cached

        logger.info("sansad.fetching_bill_detail", url=bill_url)

        # Normalise URL -- accept both full URLs and relative paths
        if bill_url.startswith("http"):
            # SECURITY: Enforce HTTPS and validate against allowed domains
            if not bill_url.startswith("https://"):
                logger.warning("sansad.bill_detail_http_rejected", url=bill_url)
                return None

            parsed = urlparse(bill_url)
            if ".." in parsed.path:
                logger.warning("sansad.bill_detail_path_traversal", url=bill_url)
                return None

            # Extract relative path from full URL
            if self.BASE_URL in bill_url:
                url_path = bill_url.replace(self.BASE_URL, "")
            elif parsed.netloc in _ALLOWED_EXTERNAL_DOMAINS:
                # Allowed external government domain -- fetch directly
                try:
                    async with self._semaphore:
                        elapsed = time.monotonic() - self._last_request_time
                        if elapsed < self._rate_limit_delay:
                            await asyncio.sleep(self._rate_limit_delay - elapsed)
                        self._last_request_time = time.monotonic()

                        async with httpx.AsyncClient(
                            timeout=30.0,
                            headers={
                                "User-Agent": "HaqSetu/1.0",
                                "Accept": "text/html,application/json",
                            },
                            follow_redirects=False,
                        ) as temp_client:
                            response = await temp_client.get(bill_url)
                except Exception:
                    logger.warning(
                        "sansad.bill_detail_external_failed",
                        url=bill_url,
                        exc_info=True,
                    )
                    return None

                if response.status_code != 200:
                    return None

                detail = self._parse_bill_detail(response, bill_url)
                if detail:
                    await self._cache.set(
                        cache_key, detail, ttl_seconds=_PAGE_CACHE_TTL
                    )
                return detail
            else:
                logger.warning(
                    "sansad.bill_detail_domain_rejected",
                    url=bill_url,
                    domain=parsed.netloc,
                )
                return None
        else:
            url_path = bill_url

        try:
            response = await self._throttled_get(url_path)
        except Exception:
            logger.warning(
                "sansad.bill_detail_fetch_failed",
                url=bill_url,
                exc_info=True,
            )
            return None

        if response.status_code != 200:
            logger.warning(
                "sansad.bill_detail_http_error",
                status=response.status_code,
                url=bill_url,
            )
            return None

        detail = self._parse_bill_detail(response, bill_url)

        if detail:
            await self._cache.set(
                cache_key, detail, ttl_seconds=_PAGE_CACHE_TTL
            )

        return detail

    def _parse_bill_detail(
        self,
        response: httpx.Response,
        bill_url: str,
    ) -> dict | None:
        """Parse a bill detail page response into a structured dictionary.

        Parameters
        ----------
        response:
            The HTTP response from the bill detail page.
        bill_url:
            The URL of the bill page (for inclusion in the result).

        Returns
        -------
        dict | None
            Parsed bill detail, or ``None`` if parsing failed.
        """
        # Attempt JSON first
        try:
            data = response.json()
            if isinstance(data, dict):
                return {
                    "title": data.get("title", ""),
                    "bill_number": data.get("bill_number", data.get("billNumber", "")),
                    "house": data.get("house", ""),
                    "session": data.get("session", ""),
                    "date_introduced": data.get("date_introduced", data.get("dateIntroduced", "")),
                    "date_passed": data.get("date_passed", data.get("datePassed")),
                    "status": data.get("status", "unknown"),
                    "ministry": data.get("ministry", ""),
                    "text_summary": data.get("text_summary", data.get("summary", "")),
                    "committee_report": data.get("committee_report", data.get("committeeReport")),
                    "amendments": data.get("amendments", []),
                    "url": bill_url,
                }
        except (ValueError, KeyError):
            pass

        # Minimal fallback for HTML responses
        logger.debug("sansad.bill_detail_html_fallback", url=bill_url)
        return {
            "title": "",
            "bill_number": "",
            "house": "",
            "session": "",
            "date_introduced": "",
            "date_passed": None,
            "status": "unknown",
            "ministry": "",
            "text_summary": "",
            "committee_report": None,
            "amendments": [],
            "url": bill_url,
        }

    # ------------------------------------------------------------------
    # Parliamentary questions search
    # ------------------------------------------------------------------

    async def search_questions(
        self,
        scheme_name: str,
        ministry: str | None = None,
    ) -> list[dict]:
        """Search for parliamentary questions mentioning a government scheme.

        Parliamentary questions (starred and unstarred) provide evidence
        that a scheme is actively scrutinised by elected representatives.

        Parameters
        ----------
        scheme_name:
            The name of the government scheme to search for.
        ministry:
            Optional ministry name to filter questions addressed to a
            specific ministry.

        Returns
        -------
        list[dict]
            List of question records.  Each record contains:

            - ``question_number`` -- official question number
            - ``member_name`` -- name of the Member of Parliament who asked
            - ``ministry`` -- ministry to which the question was addressed
            - ``date`` -- date the question was raised
            - ``subject`` -- subject/title of the question
            - ``answer_summary`` -- summary of the ministerial answer
        """
        cache_key = f"sansad:questions:{scheme_name}:{ministry or 'all'}"
        cached: list[dict] | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "sansad.questions_from_cache",
                scheme_name=scheme_name,
                count=len(cached),
            )
            return cached

        logger.info(
            "sansad.searching_questions",
            scheme_name=scheme_name,
            ministry=ministry,
        )

        questions: list[dict] = []

        # Search both Lok Sabha and Rajya Sabha questions
        for house_path in ("Loksabha", "Rajyasabha"):
            try:
                url = f"/{house_path}/questions"
                params: dict[str, str] = {"search": scheme_name}
                if ministry:
                    params["ministry"] = ministry

                response = await self._throttled_get(url, params=params)

                if response.status_code != 200:
                    logger.warning(
                        "sansad.questions_http_error",
                        status=response.status_code,
                        house=house_path,
                    )
                    continue

                parsed = self._parse_question_results(response)
                questions.extend(parsed)
            except Exception:
                logger.warning(
                    "sansad.questions_search_failed",
                    house=house_path,
                    scheme_name=scheme_name,
                    exc_info=True,
                )

        if questions:
            await self._cache.set(
                cache_key, questions, ttl_seconds=_PAGE_CACHE_TTL
            )

        logger.info(
            "sansad.questions_search_complete",
            scheme_name=scheme_name,
            count=len(questions),
        )
        return questions

    def _parse_question_results(self, response: httpx.Response) -> list[dict]:
        """Parse question search results from an HTTP response.

        Parameters
        ----------
        response:
            The HTTP response from the questions search endpoint.

        Returns
        -------
        list[dict]
            Parsed question records.
        """
        questions: list[dict] = []

        try:
            data = response.json()
            records = data if isinstance(data, list) else data.get("data", [])
            for record in records:
                if not isinstance(record, dict):
                    continue
                question = {
                    "question_number": record.get(
                        "question_number",
                        record.get("questionNumber", ""),
                    ),
                    "member_name": record.get(
                        "member_name",
                        record.get("memberName", ""),
                    ),
                    "ministry": record.get("ministry", ""),
                    "date": record.get("date", record.get("questionDate", "")),
                    "subject": record.get("subject", record.get("title", "")),
                    "answer_summary": record.get(
                        "answer_summary",
                        record.get("answerSummary", ""),
                    ),
                }
                questions.append(question)
        except (ValueError, KeyError):
            logger.debug("sansad.questions_json_parse_failed", exc_info=True)

        return questions

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def _build_url(self, path: str) -> str:
        """Build a full URL from a relative path.

        Parameters
        ----------
        path:
            A relative path or full URL.

        Returns
        -------
        str
            The full URL on sansad.in.
        """
        if not path:
            return ""
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.BASE_URL}{path}"
