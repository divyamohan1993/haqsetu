"""Client for fetching government scheme data from myscheme.gov.in.

MyScheme.gov.in is the Government of India's official portal for discovering
and applying to government schemes.  It is built as a Next.js SSR application,
which means we can extract structured JSON data via the ``/_next/data/``
routes when the build ID is known.

Strategy
--------
1. Fetch the MyScheme homepage to extract the current Next.js build ID from
   the embedded ``__NEXT_DATA__`` script tag.
2. Use the build ID to access ``/_next/data/{buildId}/`` endpoints which
   return raw JSON props used by the React frontend.
3. Fetch scheme listing / search pages to discover all scheme slugs.
4. Fetch individual scheme detail pages for the full data payload.
5. Parse and normalise into the HaqSetu ``SchemeDocument`` format.

If the Next.js data routes are unavailable (e.g. after a site rebuild that
changes the internal API), the client falls back to fetching rendered HTML
and parsing the ``__NEXT_DATA__`` JSON block directly.

Rate Limiting
-------------
We are respectful consumers of a government website:
  - Maximum 3 concurrent requests (``asyncio.Semaphore``).
  - 1-2 second delay between successive requests.
  - Proper ``User-Agent`` identifying HaqSetu.
  - All fetched pages are cached for 4 hours.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from src.services.cache import CacheManager

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"\s+type="application/json">\s*(.*?)\s*</script>',
    re.DOTALL,
)

_PAGE_CACHE_TTL = 4 * 60 * 60  # 4 hours in seconds

# Category mapping from MyScheme taxonomy to our internal categories.
_MYSCHEME_CATEGORY_MAP: dict[str, str] = {
    "agriculture,rural & environment": "agriculture",
    "agriculture": "agriculture",
    "health & wellness": "health",
    "health": "health",
    "education & learning": "education",
    "education": "education",
    "housing & shelter": "housing",
    "housing": "housing",
    "business & entrepreneurship": "employment",
    "employment": "employment",
    "social welfare & empowerment": "social_security",
    "social welfare": "social_security",
    "banking,financial services and insurance": "financial_inclusion",
    "financial services": "financial_inclusion",
    "women and child": "women_child",
    "women & child development": "women_child",
    "tribal welfare": "tribal",
    "differently abled": "disability",
    "disability": "disability",
    "senior citizen": "senior_citizen",
    "skill & employment": "skill_development",
    "skills & employment": "skill_development",
    "science, it & communications": "infrastructure",
    "transport & infrastructure": "infrastructure",
    "utility & sanitation": "infrastructure",
    "sports & culture": "other",
    "travel & tourism": "other",
    "public safety,law & justice": "other",
}


def _map_category(raw_category: str) -> str:
    """Map a MyScheme category string to our internal SchemeCategory value."""
    if not raw_category:
        return "other"
    key = raw_category.strip().lower()
    return _MYSCHEME_CATEGORY_MAP.get(key, "other")


# ---------------------------------------------------------------------------
# MySchemeClient
# ---------------------------------------------------------------------------


class MySchemeClient:
    """Client for fetching scheme data from myscheme.gov.in.

    Parameters
    ----------
    cache:
        A :class:`CacheManager` instance for caching HTTP responses.
    rate_limit_delay:
        Seconds to wait between successive HTTP requests.  Defaults to 1.5.
    """

    BASE_URL = "https://www.myscheme.gov.in"

    def __init__(
        self,
        cache: CacheManager,
        rate_limit_delay: float = 1.5,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
            headers={
                "User-Agent": (
                    "HaqSetu/1.0 (Government Scheme Aggregator; "
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
        self._build_id: str | None = None
        self._rate_limit_delay = rate_limit_delay
        self._semaphore = asyncio.Semaphore(3)
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
        """Issue a GET request with concurrency limiting and polite delays."""
        async with self._semaphore:
            # Enforce minimum delay between requests
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self._rate_limit_delay:
                await asyncio.sleep(self._rate_limit_delay - elapsed)

            self._last_request_time = time.monotonic()
            response = await self._client.get(url, **kwargs)  # type: ignore[arg-type]
            return response

    # ------------------------------------------------------------------
    # Build-ID discovery
    # ------------------------------------------------------------------

    async def _get_build_id(self) -> str:
        """Extract the Next.js build ID from the homepage.

        The build ID is embedded in a ``<script id="__NEXT_DATA__">`` tag
        as part of the JSON payload under the key ``"buildId"``.

        Returns
        -------
        str
            The current Next.js build ID string.

        Raises
        ------
        RuntimeError
            If the build ID cannot be extracted.
        """
        if self._build_id is not None:
            return self._build_id

        # Check cache first
        cached_id: str | None = await self._cache.get("myscheme:build_id")
        if cached_id is not None:
            self._build_id = cached_id
            logger.debug("myscheme.build_id_from_cache", build_id=cached_id)
            return cached_id

        logger.info("myscheme.fetching_build_id")
        response = await self._throttled_get("/")
        response.raise_for_status()

        next_data = self._parse_next_data(response.text)
        if next_data is None:
            raise RuntimeError(
                "Could not extract __NEXT_DATA__ from MyScheme homepage"
            )

        build_id = next_data.get("buildId")
        if not build_id or not isinstance(build_id, str):
            raise RuntimeError(
                f"Invalid or missing buildId in __NEXT_DATA__: {build_id!r}"
            )

        self._build_id = build_id
        # Cache build ID for 2 hours (it changes on each deployment)
        await self._cache.set("myscheme:build_id", build_id, ttl_seconds=7200)
        logger.info("myscheme.build_id_extracted", build_id=build_id)
        return build_id

    # ------------------------------------------------------------------
    # Scheme slug discovery
    # ------------------------------------------------------------------

    async def fetch_scheme_slugs(self) -> list[str]:
        """Discover all scheme URL slugs from multiple strategies.

        Strategies (tried in order, results merged):
        1. ``/_next/data/{buildId}/search.json`` for the search listing.
        2. Fetch ``/search`` and parse ``__NEXT_DATA__`` from HTML.
        3. Paginated search API via Next.js data routes.
        4. Parse ``/sitemap.xml`` for ``/schemes/*`` URLs.
        5. Use previously cached slugs as a fallback.

        Returns
        -------
        list[str]
            De-duplicated list of scheme slug strings.
        """
        # Check cache for recently fetched slugs
        cached_slugs: list[str] | None = await self._cache.get("myscheme:slugs")
        if cached_slugs is not None:
            logger.info(
                "myscheme.slugs_from_cache", count=len(cached_slugs)
            )
            return cached_slugs

        all_slugs: set[str] = set()

        # Strategy 1 & 3: Next.js data route for search pages (paginated)
        try:
            build_id = await self._get_build_id()
            slugs_from_next = await self._fetch_slugs_from_next_data(build_id)
            all_slugs.update(slugs_from_next)
            logger.info(
                "myscheme.slugs_from_next_data", count=len(slugs_from_next)
            )
        except Exception:
            logger.warning("myscheme.next_data_slug_fetch_failed", exc_info=True)

        # Strategy 2: Fetch /search HTML and parse __NEXT_DATA__
        if not all_slugs:
            try:
                slugs_from_html = await self._fetch_slugs_from_search_html()
                all_slugs.update(slugs_from_html)
                logger.info(
                    "myscheme.slugs_from_search_html",
                    count=len(slugs_from_html),
                )
            except Exception:
                logger.warning(
                    "myscheme.search_html_slug_fetch_failed", exc_info=True
                )

        # Strategy 4: Sitemap
        try:
            slugs_from_sitemap = await self._fetch_slugs_from_sitemap()
            all_slugs.update(slugs_from_sitemap)
            logger.info(
                "myscheme.slugs_from_sitemap", count=len(slugs_from_sitemap)
            )
        except Exception:
            logger.warning("myscheme.sitemap_slug_fetch_failed", exc_info=True)

        slugs = sorted(all_slugs)
        if slugs:
            await self._cache.set(
                "myscheme:slugs", slugs, ttl_seconds=_PAGE_CACHE_TTL
            )

        logger.info("myscheme.total_slugs_discovered", count=len(slugs))
        return slugs

    async def _fetch_slugs_from_next_data(self, build_id: str) -> list[str]:
        """Fetch scheme slugs from paginated Next.js data search endpoints."""
        slugs: list[str] = []
        page = 0
        max_pages = 200  # Safety limit

        while page < max_pages:
            cache_key = f"myscheme:search_page:{page}"
            data: dict | None = await self._cache.get(cache_key)

            if data is None:
                url = f"/_next/data/{build_id}/search.json"
                params = {}
                if page > 0:
                    params["page"] = str(page)

                try:
                    response = await self._throttled_get(url, params=params)
                    if response.status_code != 200:
                        break
                    data = response.json()
                    await self._cache.set(
                        cache_key, data, ttl_seconds=_PAGE_CACHE_TTL
                    )
                except Exception:
                    logger.warning(
                        "myscheme.search_page_fetch_failed",
                        page=page,
                        exc_info=True,
                    )
                    break

            # Extract scheme slugs from the page data
            page_props = data.get("pageProps", {}) if data else {}
            scheme_list = page_props.get("data", page_props.get("schemes", []))

            if not scheme_list:
                # Try nested structures
                search_data = page_props.get("searchData", {})
                if isinstance(search_data, dict):
                    scheme_list = search_data.get("data", [])

            if not scheme_list:
                break

            for scheme in scheme_list:
                slug = None
                if isinstance(scheme, dict):
                    slug = (
                        scheme.get("slug")
                        or scheme.get("schemeUrl")
                        or scheme.get("scheme_slug")
                    )
                    # Sometimes the URL is a full path like /schemes/xyz
                    if slug and slug.startswith("/schemes/"):
                        slug = slug.removeprefix("/schemes/")
                    elif slug and slug.startswith("/"):
                        slug = slug.lstrip("/")
                if slug:
                    slugs.append(slug)

            # Check if there are more pages
            total_count = page_props.get("totalCount", 0)
            if isinstance(search_data := page_props.get("searchData"), dict):
                total_count = search_data.get("totalCount", total_count)

            if len(slugs) >= total_count and total_count > 0:
                break

            page += 1

        return slugs

    async def _fetch_slugs_from_search_html(self) -> list[str]:
        """Fetch the /search page HTML and extract slugs from __NEXT_DATA__."""
        response = await self._throttled_get("/search")
        response.raise_for_status()

        next_data = self._parse_next_data(response.text)
        if next_data is None:
            return []

        page_props = next_data.get("props", {}).get("pageProps", {})
        scheme_list = page_props.get("data", page_props.get("schemes", []))

        if not scheme_list and isinstance(
            page_props.get("searchData"), dict
        ):
            scheme_list = page_props["searchData"].get("data", [])

        slugs: list[str] = []
        for scheme in scheme_list:
            if isinstance(scheme, dict):
                slug = (
                    scheme.get("slug")
                    or scheme.get("schemeUrl")
                    or scheme.get("scheme_slug")
                )
                if slug:
                    if slug.startswith("/schemes/"):
                        slug = slug.removeprefix("/schemes/")
                    elif slug.startswith("/"):
                        slug = slug.lstrip("/")
                    slugs.append(slug)

        return slugs

    async def _fetch_slugs_from_sitemap(self) -> list[str]:
        """Parse sitemap.xml for /schemes/* URLs to extract slugs."""
        slugs: list[str] = []
        sitemap_urls = ["/sitemap.xml", "/sitemap-schemes.xml"]

        for sitemap_url in sitemap_urls:
            try:
                response = await self._throttled_get(sitemap_url)
                if response.status_code != 200:
                    continue

                text = response.text
                # Extract URLs matching /schemes/ pattern
                url_pattern = re.compile(
                    r"<loc>[^<]*?/schemes/([^</?]+)</loc>"
                )
                matches = url_pattern.findall(text)
                slugs.extend(matches)
            except Exception:
                logger.debug(
                    "myscheme.sitemap_url_failed",
                    url=sitemap_url,
                    exc_info=True,
                )

        return slugs

    # ------------------------------------------------------------------
    # Individual scheme detail fetching
    # ------------------------------------------------------------------

    async def fetch_scheme_detail(self, slug: str) -> dict | None:
        """Fetch detailed scheme data for a single scheme.

        Tries the Next.js data route first for structured JSON, then falls
        back to fetching the rendered HTML page and parsing ``__NEXT_DATA__``.

        Parameters
        ----------
        slug:
            The URL slug for the scheme (e.g. ``"pm-kisan-samman-nidhi"``).

        Returns
        -------
        dict | None
            Normalised scheme data dictionary, or ``None`` if the scheme
            could not be fetched or parsed.
        """
        cache_key = f"myscheme:detail:{slug}"
        cached: dict | None = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        raw_data: dict | None = None

        # Strategy 1: Next.js data route
        try:
            build_id = await self._get_build_id()
            url = f"/_next/data/{build_id}/schemes/{slug}.json"
            response = await self._throttled_get(url)

            if response.status_code == 200:
                data = response.json()
                page_props = data.get("pageProps", {})
                if page_props:
                    raw_data = page_props
                    logger.debug(
                        "myscheme.detail_from_next_data", slug=slug
                    )
        except Exception:
            logger.debug(
                "myscheme.next_data_detail_failed",
                slug=slug,
                exc_info=True,
            )

        # Strategy 2: HTML fallback
        if raw_data is None:
            try:
                response = await self._throttled_get(f"/schemes/{slug}")
                if response.status_code == 200:
                    next_data = self._parse_next_data(response.text)
                    if next_data is not None:
                        page_props = (
                            next_data.get("props", {}).get("pageProps", {})
                        )
                        if page_props:
                            raw_data = page_props
                            logger.debug(
                                "myscheme.detail_from_html", slug=slug
                            )
                elif response.status_code == 404:
                    logger.warning("myscheme.scheme_not_found", slug=slug)
                    return None
            except Exception:
                logger.warning(
                    "myscheme.html_detail_failed",
                    slug=slug,
                    exc_info=True,
                )

        if raw_data is None:
            logger.warning("myscheme.detail_fetch_failed", slug=slug)
            return None

        # Normalise into our format
        try:
            normalised = self._normalize_scheme(raw_data, slug=slug)
            await self._cache.set(
                cache_key, normalised, ttl_seconds=_PAGE_CACHE_TTL
            )
            return normalised
        except Exception:
            logger.warning(
                "myscheme.normalize_failed", slug=slug, exc_info=True
            )
            return None

    # ------------------------------------------------------------------
    # Bulk fetch
    # ------------------------------------------------------------------

    async def fetch_all_schemes(
        self,
        max_concurrent: int = 3,
    ) -> list[dict]:
        """Fetch all schemes with polite rate limiting.

        Discovers all scheme slugs, then fetches the detail page for each
        one using a bounded ``asyncio.Semaphore`` for concurrency and
        inter-request delays for politeness.

        Parameters
        ----------
        max_concurrent:
            Maximum number of concurrent HTTP requests.  Defaults to 3.

        Returns
        -------
        list[dict]
            List of normalised scheme dictionaries.  Schemes that failed
            to fetch or parse are silently skipped (logged as warnings).
        """
        # Update the semaphore if a different concurrency is requested
        self._semaphore = asyncio.Semaphore(max_concurrent)

        slugs = await self.fetch_scheme_slugs()
        if not slugs:
            logger.warning("myscheme.no_slugs_found")
            return []

        logger.info("myscheme.fetching_all_schemes", total_slugs=len(slugs))

        results: list[dict] = []
        failed = 0

        # Process in batches to be extra polite
        batch_size = max_concurrent
        for i in range(0, len(slugs), batch_size):
            batch = slugs[i : i + batch_size]
            tasks = [self.fetch_scheme_detail(slug) for slug in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for slug, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    logger.warning(
                        "myscheme.scheme_fetch_exception",
                        slug=slug,
                        error=str(result),
                    )
                    failed += 1
                elif result is not None:
                    results.append(result)
                else:
                    failed += 1

            # Brief pause between batches
            if i + batch_size < len(slugs):
                await asyncio.sleep(self._rate_limit_delay)

        logger.info(
            "myscheme.fetch_complete",
            fetched=len(results),
            failed=failed,
            total=len(slugs),
        )
        return results

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_next_data(self, html: str) -> dict | None:
        """Extract the ``__NEXT_DATA__`` JSON block from an HTML page.

        Returns
        -------
        dict | None
            Parsed JSON dictionary, or ``None`` if the block was not found
            or could not be parsed.
        """
        match = _NEXT_DATA_RE.search(html)
        if match is None:
            return None
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            logger.warning("myscheme.next_data_parse_error", exc_info=True)
            return None

    def _normalize_scheme(self, raw: dict, slug: str = "") -> dict:
        """Normalise raw MyScheme page-props data into our SchemeDocument format.

        Parameters
        ----------
        raw:
            The ``pageProps`` dictionary extracted from the Next.js data.
        slug:
            The scheme URL slug (used as a fallback for ``scheme_id``).

        Returns
        -------
        dict
            Dictionary compatible with the ``SchemeDocument`` model fields.
        """
        # MyScheme nests the actual scheme data in various possible keys
        scheme_data = raw.get("schemeData", raw.get("data", raw))
        if isinstance(scheme_data, list) and scheme_data:
            scheme_data = scheme_data[0]

        # Extract basic fields with fallbacks
        scheme_id = (
            scheme_data.get("id")
            or scheme_data.get("schemeId")
            or scheme_data.get("slug")
            or slug
            or "unknown"
        )
        # Prefix with "myscheme-" if it's a numeric ID
        if isinstance(scheme_id, int) or (
            isinstance(scheme_id, str) and scheme_id.isdigit()
        ):
            scheme_id = f"myscheme-{scheme_id}"

        name = (
            scheme_data.get("schemeName")
            or scheme_data.get("name")
            or scheme_data.get("title")
            or ""
        )

        description = (
            scheme_data.get("schemeDescription")
            or scheme_data.get("description")
            or scheme_data.get("briefDescription")
            or scheme_data.get("about")
            or ""
        )
        # Strip HTML tags from description if present
        description = re.sub(r"<[^>]+>", "", description).strip()

        benefits = (
            scheme_data.get("benefits")
            or scheme_data.get("schemeBenefits")
            or scheme_data.get("benefit")
            or ""
        )
        if isinstance(benefits, list):
            benefits = "; ".join(str(b) for b in benefits)
        benefits = re.sub(r"<[^>]+>", "", str(benefits)).strip()

        application_process = (
            scheme_data.get("applicationProcess")
            or scheme_data.get("howToApply")
            or scheme_data.get("application_process")
            or ""
        )
        if isinstance(application_process, list):
            application_process = " ".join(str(s) for s in application_process)
        application_process = re.sub(
            r"<[^>]+>", "", str(application_process)
        ).strip()

        documents = (
            scheme_data.get("documentsRequired")
            or scheme_data.get("documents_required")
            or scheme_data.get("documents")
            or []
        )
        if isinstance(documents, str):
            documents = [d.strip() for d in documents.split(",") if d.strip()]
        elif isinstance(documents, list):
            documents = [
                re.sub(r"<[^>]+>", "", str(d)).strip()
                for d in documents
                if d
            ]

        ministry = (
            scheme_data.get("ministry")
            or scheme_data.get("nodalMinistry")
            or scheme_data.get("ministryName")
            or scheme_data.get("department")
            or "Government of India"
        )
        if isinstance(ministry, dict):
            ministry = ministry.get("name", "Government of India")

        raw_category = (
            scheme_data.get("category")
            or scheme_data.get("schemeCategory")
            or scheme_data.get("tags", "")
        )
        if isinstance(raw_category, list):
            raw_category = raw_category[0] if raw_category else ""
        if isinstance(raw_category, dict):
            raw_category = raw_category.get("name", "")
        category = _map_category(str(raw_category))

        state = scheme_data.get("state") or scheme_data.get("stateName")
        if isinstance(state, dict):
            state = state.get("name")
        if isinstance(state, str) and state.lower() in ("all", "central", "all india"):
            state = None

        helpline = (
            scheme_data.get("helpline")
            or scheme_data.get("contactInfo")
            or scheme_data.get("helplineNumber")
        )
        if isinstance(helpline, dict):
            helpline = helpline.get("number") or helpline.get("email")

        website = (
            scheme_data.get("schemeLink")
            or scheme_data.get("website")
            or scheme_data.get("officialWebsite")
        )
        if not website and slug:
            website = f"{self.BASE_URL}/schemes/{slug}"

        # Eligibility parsing
        raw_elig = scheme_data.get("eligibility", {})
        if isinstance(raw_elig, str):
            raw_elig = {"custom_criteria": [raw_elig]}
        elif isinstance(raw_elig, list):
            raw_elig = {
                "custom_criteria": [
                    re.sub(r"<[^>]+>", "", str(e)).strip() for e in raw_elig
                ]
            }

        eligibility = {
            "min_age": raw_elig.get("minAge") or raw_elig.get("min_age"),
            "max_age": raw_elig.get("maxAge") or raw_elig.get("max_age"),
            "gender": raw_elig.get("gender"),
            "income_limit": raw_elig.get("incomeLimit") or raw_elig.get("income_limit"),
            "category": raw_elig.get("socialCategory") or raw_elig.get("category"),
            "occupation": raw_elig.get("occupation"),
            "state": raw_elig.get("state"),
            "is_bpl": raw_elig.get("isBPL") or raw_elig.get("is_bpl"),
            "land_holding_acres": raw_elig.get("landHolding") or raw_elig.get("land_holding_acres"),
            "custom_criteria": raw_elig.get("custom_criteria", []),
        }
        # Clean up None values
        eligibility = {
            k: v for k, v in eligibility.items() if v is not None
        }
        if "custom_criteria" not in eligibility:
            eligibility["custom_criteria"] = []

        # Determine last_updated
        last_updated_raw = (
            scheme_data.get("lastUpdated")
            or scheme_data.get("updatedAt")
            or scheme_data.get("last_updated")
        )
        if isinstance(last_updated_raw, str):
            try:
                last_updated = datetime.fromisoformat(
                    last_updated_raw.replace("Z", "+00:00")
                ).isoformat()
            except (ValueError, TypeError):
                last_updated = datetime.now(timezone.utc).isoformat()
        else:
            last_updated = datetime.now(timezone.utc).isoformat()

        return {
            "scheme_id": str(scheme_id),
            "name": name,
            "description": description,
            "category": category,
            "ministry": ministry,
            "state": state,
            "eligibility": eligibility,
            "benefits": benefits,
            "application_process": application_process,
            "documents_required": documents,
            "helpline": str(helpline) if helpline else None,
            "website": website,
            "deadline": scheme_data.get("deadline"),
            "last_updated": last_updated,
            "popularity_score": float(
                scheme_data.get("popularity", scheme_data.get("views", 0))
            ),
            "source": "myscheme.gov.in",
            "source_slug": slug,
        }
