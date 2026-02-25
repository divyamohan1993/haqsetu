"""Client for verifying government schemes against India Code digital repository.

India Code (indiacode.nic.in) is the official digital repository maintained
by the Legislative Department, Ministry of Law and Justice, Government of
India.  It contains the full text of all Central Acts, State Acts, and
subordinate legislation (rules, regulations, notifications, and orders).

This client searches India Code to verify that a government scheme has a
proper enabling Act or is grounded in legislation, and to retrieve the
full legislative detail including sections, amendments, and subordinate
instruments.

Strategy
--------
1. Search for Acts matching a scheme name or related keywords.
2. Fetch full Act detail including preamble, sections, and schedules.
3. Search for subordinate legislation (rules, regulations, notifications)
   issued under a specific Act that operationalise a scheme.
4. Combine results into a confidence score indicating how strongly a scheme
   is grounded in statutory law.

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

import httpx
import structlog

if TYPE_CHECKING:
    from src.services.cache import CacheManager

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PAGE_CACHE_TTL = 12 * 60 * 60  # 12 hours in seconds


# ---------------------------------------------------------------------------
# IndiaCodeClient
# ---------------------------------------------------------------------------


class IndiaCodeClient:
    """Client for verifying schemes against the India Code digital repository.

    Searches indiacode.nic.in for Acts, sections, and subordinate legislation
    that enable or support government schemes.

    Parameters
    ----------
    cache:
        A :class:`CacheManager` instance for caching HTTP responses.
    rate_limit_delay:
        Seconds to wait between successive HTTP requests.  Defaults to 2.0.
    """

    BASE_URL = "https://www.indiacode.nic.in"

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
    # Act search
    # ------------------------------------------------------------------

    async def search_acts(
        self,
        query: str,
        year: int | None = None,
    ) -> list[dict]:
        """Search India Code for Acts matching a query string.

        Parameters
        ----------
        query:
            Search query (e.g. a scheme name, act title, or keyword).
        year:
            Optional year to filter Acts by enactment year.

        Returns
        -------
        list[dict]
            List of act records.  Each record contains:

            - ``title`` -- full title of the Act
            - ``act_number`` -- official act number
            - ``year`` -- year of enactment
            - ``short_title`` -- short title of the Act
            - ``long_title`` -- long title / preamble heading
            - ``date_of_assent`` -- date of presidential assent
            - ``status`` -- current status: ``"in_force"``, ``"repealed"``,
              or ``"amended"``
            - ``ministry`` -- nodal ministry responsible
            - ``url`` -- full URL to the Act on indiacode.nic.in
            - ``sections_count`` -- number of sections in the Act
        """
        cache_key = f"indiacode:acts:{query}:{year or 'all'}"
        cached: list[dict] | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "indiacode.acts_from_cache",
                query=query,
                count=len(cached),
            )
            return cached

        logger.info("indiacode.searching_acts", query=query, year=year)

        acts: list[dict] = []

        try:
            url = "/handle/123456789/1362/search"
            params: dict[str, str] = {"query": query}
            if year is not None:
                params["year"] = str(year)

            response = await self._throttled_get(url, params=params)

            if response.status_code != 200:
                logger.warning(
                    "indiacode.acts_search_http_error",
                    status=response.status_code,
                )
                return []

            acts = self._parse_act_search_results(response)
        except Exception:
            logger.warning(
                "indiacode.acts_search_failed",
                query=query,
                exc_info=True,
            )

        # If the primary endpoint returned nothing, try the browse endpoint
        if not acts:
            try:
                fallback_url = "/browse/acts"
                params_fb: dict[str, str] = {"search": query}
                if year is not None:
                    params_fb["year"] = str(year)

                response = await self._throttled_get(fallback_url, params=params_fb)

                if response.status_code == 200:
                    acts = self._parse_act_search_results(response)
                    logger.info(
                        "indiacode.acts_fallback_search",
                        query=query,
                        count=len(acts),
                    )
            except Exception:
                logger.warning(
                    "indiacode.acts_fallback_failed",
                    query=query,
                    exc_info=True,
                )

        if acts:
            await self._cache.set(cache_key, acts, ttl_seconds=_PAGE_CACHE_TTL)

        logger.info(
            "indiacode.acts_search_complete",
            query=query,
            year=year,
            count=len(acts),
        )
        return acts

    def _parse_act_search_results(self, response: httpx.Response) -> list[dict]:
        """Parse act search results from an HTTP response.

        Attempts to parse JSON first; falls back to structured HTML
        extraction if the response is not JSON.

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

        # Attempt JSON parsing first
        try:
            data = response.json()
            records = data if isinstance(data, list) else data.get("data", [])
            for record in records:
                if not isinstance(record, dict):
                    continue
                act = self._normalise_act_record(record)
                acts.append(act)
            return acts
        except (ValueError, KeyError):
            pass

        # Fallback: extract structured data from HTML
        try:
            text = response.text
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
                                acts.append(self._normalise_act_record(item))
                    elif isinstance(block_data, dict) and "title" in block_data:
                        acts.append(self._normalise_act_record(block_data))
                except (json.JSONDecodeError, ValueError):
                    continue
        except Exception:
            logger.debug("indiacode.html_parse_fallback_failed", exc_info=True)

        return acts

    def _normalise_act_record(self, record: dict) -> dict:
        """Normalise a raw act record into the standard output format.

        Parameters
        ----------
        record:
            A raw record dictionary from the API or parsed HTML.

        Returns
        -------
        dict
            Normalised act record.
        """
        act_id = record.get("id", record.get("actId", ""))
        url_path = record.get("url", record.get("link", ""))

        return {
            "title": record.get("title", ""),
            "act_number": record.get("act_number", record.get("actNumber", "")),
            "year": record.get("year", record.get("enactmentYear")),
            "short_title": record.get("short_title", record.get("shortTitle", "")),
            "long_title": record.get("long_title", record.get("longTitle", "")),
            "date_of_assent": record.get(
                "date_of_assent",
                record.get("dateOfAssent", ""),
            ),
            "status": self._normalise_act_status(
                record.get("status", "unknown")
            ),
            "ministry": record.get("ministry", record.get("department", "")),
            "url": self._build_url(url_path) if url_path else self._build_act_url(act_id),
            "sections_count": record.get(
                "sections_count",
                record.get("sectionsCount", 0),
            ),
        }

    @staticmethod
    def _normalise_act_status(raw_status: str) -> str:
        """Normalise an act status string to one of the canonical values.

        Parameters
        ----------
        raw_status:
            The raw status string from the data source.

        Returns
        -------
        str
            One of ``"in_force"``, ``"repealed"``, ``"amended"``, or
            ``"unknown"``.
        """
        status_lower = raw_status.strip().lower()
        if status_lower in ("in force", "in_force", "active", "enacted"):
            return "in_force"
        if status_lower in ("repealed", "omitted", "expired"):
            return "repealed"
        if status_lower in ("amended", "partially amended"):
            return "amended"
        return "unknown"

    # ------------------------------------------------------------------
    # Act detail
    # ------------------------------------------------------------------

    async def fetch_act_detail(self, act_id: str) -> dict | None:
        """Fetch the full detail of an Act including its sections.

        Parameters
        ----------
        act_id:
            The unique identifier of the Act on India Code (e.g. a
            handle path segment or numeric ID).

        Returns
        -------
        dict | None
            Detailed Act information with keys:

            - ``title`` -- full title of the Act
            - ``preamble`` -- the preamble text
            - ``sections`` -- list of section dicts (``{number, title, text}``)
            - ``schedule`` -- schedule text (if any)
            - ``amendments`` -- list of amendment records
            - ``status`` -- current status

            Returns ``None`` if the Act could not be fetched or parsed.
        """
        cache_key = f"indiacode:act_detail:{act_id}"
        cached: dict | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug("indiacode.act_detail_from_cache", act_id=act_id)
            return cached

        logger.info("indiacode.fetching_act_detail", act_id=act_id)

        # Try the primary handle-based URL
        url = f"/handle/123456789/{act_id}"

        try:
            response = await self._throttled_get(url)
        except Exception:
            logger.warning(
                "indiacode.act_detail_fetch_failed",
                act_id=act_id,
                exc_info=True,
            )
            return None

        if response.status_code == 404:
            # Try alternative URL patterns
            try:
                response = await self._throttled_get(f"/show-data/{act_id}")
            except Exception:
                logger.warning(
                    "indiacode.act_detail_alt_fetch_failed",
                    act_id=act_id,
                    exc_info=True,
                )
                return None

        if response.status_code != 200:
            logger.warning(
                "indiacode.act_detail_http_error",
                status=response.status_code,
                act_id=act_id,
            )
            return None

        detail = self._parse_act_detail(response)

        if detail:
            await self._cache.set(
                cache_key, detail, ttl_seconds=_PAGE_CACHE_TTL
            )

        return detail

    def _parse_act_detail(self, response: httpx.Response) -> dict | None:
        """Parse an Act detail page into a structured dictionary.

        Parameters
        ----------
        response:
            The HTTP response from the Act detail page.

        Returns
        -------
        dict | None
            Parsed Act detail, or ``None`` if parsing failed.
        """
        # Attempt JSON first
        try:
            data = response.json()
            if isinstance(data, dict):
                sections_raw = data.get("sections", [])
                sections = []
                for sec in sections_raw:
                    if isinstance(sec, dict):
                        sections.append({
                            "number": sec.get("number", sec.get("sectionNumber", "")),
                            "title": sec.get("title", sec.get("heading", "")),
                            "text": sec.get("text", sec.get("content", "")),
                        })

                amendments_raw = data.get("amendments", [])
                amendments = []
                for amend in amendments_raw:
                    if isinstance(amend, dict):
                        amendments.append({
                            "title": amend.get("title", ""),
                            "year": amend.get("year", ""),
                            "act_number": amend.get("act_number", amend.get("actNumber", "")),
                            "date": amend.get("date", ""),
                        })

                return {
                    "title": data.get("title", ""),
                    "preamble": data.get("preamble", data.get("longTitle", "")),
                    "sections": sections,
                    "schedule": data.get("schedule", data.get("schedules", "")),
                    "amendments": amendments,
                    "status": self._normalise_act_status(
                        data.get("status", "unknown")
                    ),
                }
        except (ValueError, KeyError):
            pass

        # Minimal fallback for HTML responses
        logger.debug("indiacode.act_detail_html_fallback")
        return {
            "title": "",
            "preamble": "",
            "sections": [],
            "schedule": "",
            "amendments": [],
            "status": "unknown",
        }

    # ------------------------------------------------------------------
    # High-level verification
    # ------------------------------------------------------------------

    async def verify_scheme_enabling_act(
        self,
        scheme_name: str,
        act_name: str | None = None,
    ) -> dict:
        """Verify that a scheme has an enabling Act in India Code.

        Searches for Acts matching the scheme name and/or a specific act
        name, then computes a confidence score based on the quality and
        relevance of results found.

        Parameters
        ----------
        scheme_name:
            The name of the government scheme to verify.
        act_name:
            Optional specific Act name to search for.  If provided, this
            is used as the primary search query for a more targeted lookup.

        Returns
        -------
        dict
            Verification result with keys:

            - ``found`` -- ``True`` if any matching Acts were found
            - ``acts`` -- list of matching act records
            - ``confidence`` -- float between 0.0 and 1.0 indicating how
              strongly the scheme is supported by legislation
            - ``search_query`` -- the query string that was used
        """
        search_query = act_name if act_name else scheme_name

        cache_key = f"indiacode:verify:{search_query}"
        cached: dict | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "indiacode.verify_from_cache",
                scheme_name=scheme_name,
            )
            return cached

        logger.info(
            "indiacode.verifying_scheme",
            scheme_name=scheme_name,
            act_name=act_name,
        )

        # Search with primary query
        acts = await self.search_acts(search_query)

        # If specific act_name was provided and no results, also search by
        # the scheme_name as a fallback
        if not acts and act_name:
            logger.info(
                "indiacode.verify_fallback_search",
                scheme_name=scheme_name,
            )
            acts = await self.search_acts(scheme_name)
            search_query = scheme_name

        # Compute confidence
        confidence = self._compute_confidence(acts, scheme_name, act_name)
        found = len(acts) > 0

        result = {
            "found": found,
            "acts": acts,
            "confidence": confidence,
            "search_query": search_query,
        }

        await self._cache.set(cache_key, result, ttl_seconds=_PAGE_CACHE_TTL)

        logger.info(
            "indiacode.verification_complete",
            scheme_name=scheme_name,
            found=found,
            confidence=confidence,
            act_count=len(acts),
        )
        return result

    def _compute_confidence(
        self,
        acts: list[dict],
        scheme_name: str,
        act_name: str | None = None,
    ) -> float:
        """Compute a confidence score for legislative verification.

        The score is based on:
        - Whether any Acts were found at all (base signal).
        - Whether Act titles closely match the scheme or act name.
        - Whether the Act is currently in force (stronger evidence).
        - Whether the Act has a substantial number of sections.

        Parameters
        ----------
        acts:
            List of act records found.
        scheme_name:
            The original scheme name for title matching.
        act_name:
            The specific act name, if provided, for title matching.

        Returns
        -------
        float
            Confidence score between 0.0 and 1.0.
        """
        if not acts:
            return 0.0

        score = 0.0
        scheme_lower = scheme_name.lower()
        act_name_lower = act_name.lower() if act_name else ""

        # Base score for finding any results
        score += 0.2

        # Title match against scheme name (up to 0.25)
        for act in acts:
            title = act.get("title", "").lower()
            short_title = act.get("short_title", "").lower()
            combined = f"{title} {short_title}"

            if scheme_lower in combined or combined in scheme_lower:
                score += 0.25
                break
            # Partial word overlap check
            scheme_words = set(scheme_lower.split())
            title_words = set(combined.split())
            overlap = scheme_words & title_words
            if len(overlap) >= 2:
                score += 0.15
                break

        # Title match against explicit act name (up to 0.2)
        if act_name_lower:
            for act in acts:
                title = act.get("title", "").lower()
                if act_name_lower in title or title in act_name_lower:
                    score += 0.2
                    break

        # Act currently in force = stronger evidence (up to 0.15)
        for act in acts:
            if act.get("status") == "in_force":
                score += 0.15
                break

        # Substantial act (many sections) suggests comprehensive legislation
        for act in acts:
            sections_count = act.get("sections_count", 0)
            if isinstance(sections_count, int) and sections_count > 10:
                score += 0.1
                break

        # Multiple matching acts = corroborating evidence
        if len(acts) > 1:
            score += 0.1

        return min(score, 1.0)

    # ------------------------------------------------------------------
    # Subordinate legislation
    # ------------------------------------------------------------------

    async def search_subordinate_legislation(
        self,
        act_id: str,
    ) -> list[dict]:
        """Find subordinate legislation issued under a specific Act.

        Subordinate legislation includes rules, regulations, notifications,
        and orders that operationalise the provisions of a parent Act.

        Parameters
        ----------
        act_id:
            The unique identifier of the parent Act on India Code.

        Returns
        -------
        list[dict]
            List of subordinate legislation records.  Each record contains:

            - ``title`` -- title of the subordinate instrument
            - ``type`` -- instrument type: ``"rules"``, ``"regulations"``,
              ``"notification"``, or ``"order"``
            - ``date`` -- date of issuance
            - ``url`` -- full URL on indiacode.nic.in
        """
        cache_key = f"indiacode:subordinate:{act_id}"
        cached: list[dict] | None = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "indiacode.subordinate_from_cache",
                act_id=act_id,
                count=len(cached),
            )
            return cached

        logger.info("indiacode.searching_subordinate_legislation", act_id=act_id)

        legislation: list[dict] = []

        try:
            url = f"/handle/123456789/{act_id}/subordinate"
            response = await self._throttled_get(url)

            if response.status_code != 200:
                # Try alternative endpoint
                url = f"/subordinate-legislation/{act_id}"
                response = await self._throttled_get(url)

            if response.status_code != 200:
                logger.warning(
                    "indiacode.subordinate_http_error",
                    status=response.status_code,
                    act_id=act_id,
                )
                return []

            legislation = self._parse_subordinate_results(response)
        except Exception:
            logger.warning(
                "indiacode.subordinate_search_failed",
                act_id=act_id,
                exc_info=True,
            )

        if legislation:
            await self._cache.set(
                cache_key, legislation, ttl_seconds=_PAGE_CACHE_TTL
            )

        logger.info(
            "indiacode.subordinate_search_complete",
            act_id=act_id,
            count=len(legislation),
        )
        return legislation

    def _parse_subordinate_results(self, response: httpx.Response) -> list[dict]:
        """Parse subordinate legislation results from an HTTP response.

        Parameters
        ----------
        response:
            The HTTP response from the subordinate legislation endpoint.

        Returns
        -------
        list[dict]
            Parsed subordinate legislation records.
        """
        legislation: list[dict] = []

        try:
            data = response.json()
            records = data if isinstance(data, list) else data.get("data", [])
            for record in records:
                if not isinstance(record, dict):
                    continue
                entry = {
                    "title": record.get("title", ""),
                    "type": self._normalise_subordinate_type(
                        record.get("type", record.get("instrumentType", ""))
                    ),
                    "date": record.get("date", record.get("dateOfIssuance", "")),
                    "url": self._build_url(
                        record.get("url", record.get("link", ""))
                    ),
                }
                legislation.append(entry)
        except (ValueError, KeyError):
            logger.debug(
                "indiacode.subordinate_json_parse_failed", exc_info=True
            )

        return legislation

    @staticmethod
    def _normalise_subordinate_type(raw_type: str) -> str:
        """Normalise a subordinate legislation type string.

        Parameters
        ----------
        raw_type:
            The raw type string from the data source.

        Returns
        -------
        str
            One of ``"rules"``, ``"regulations"``, ``"notification"``,
            or ``"order"``.
        """
        type_lower = raw_type.strip().lower()
        if "rule" in type_lower:
            return "rules"
        if "regulation" in type_lower:
            return "regulations"
        if "notification" in type_lower or "gazette" in type_lower:
            return "notification"
        if "order" in type_lower:
            return "order"
        return raw_type.strip().lower() if raw_type else "rules"

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
            The full URL on indiacode.nic.in.
        """
        if not path:
            return ""
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.BASE_URL}{path}"

    def _build_act_url(self, act_id: str) -> str:
        """Build the canonical URL for an Act given its ID.

        Parameters
        ----------
        act_id:
            The unique Act identifier.

        Returns
        -------
        str
            The full URL to the Act on indiacode.nic.in, or an empty
            string if no ID was provided.
        """
        if not act_id:
            return ""
        return f"{self.BASE_URL}/handle/123456789/{act_id}"
