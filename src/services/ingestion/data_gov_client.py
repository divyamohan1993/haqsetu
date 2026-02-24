"""Client for the data.gov.in Open Government Data Platform API.

Provides access to supplementary scheme-related datasets published by
Indian government ministries on the Open Government Data (OGD) platform.

API documentation: https://data.gov.in/apis
Free API key registration: https://data.gov.in/user/register

The OGD API returns datasets in JSON/CSV/XML formats.  We primarily use
JSON and focus on datasets related to government scheme expenditure,
beneficiary counts, and scheme metadata published by the Ministry of
Finance, Ministry of Social Justice, NITI Aayog, and others.

Rate Limiting
-------------
The data.gov.in API has its own rate limits (typically 1000 requests/day
for free keys).  We cache all responses aggressively and batch requests
where possible.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from src.services.cache import CacheManager

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_CACHE_TTL = 6 * 60 * 60  # 6 hours -- datasets update infrequently

# Known resource IDs for scheme-related datasets on data.gov.in.
# These are stable identifiers for specific government data resources.
_KNOWN_RESOURCES: dict[str, str] = {
    # Ministry of Finance -- scheme expenditure
    "scheme_expenditure": "9115b89c-7a80-4f54-9b06-21086e0f0bd7",
    # Social welfare schemes beneficiary data
    "social_welfare_beneficiaries": "d040f04e-8db6-41a7-b328-5ea31341c227",
    # PM-KISAN beneficiary statistics
    "pmkisan_beneficiaries": "63e8a29f-d828-4745-a8d7-bd09a42bc2bd",
    # MGNREGA -- employment guarantee scheme data
    "mgnrega_data": "cd05bf0c-9e5d-413c-a939-b3dc4e1b6e60",
}


# ---------------------------------------------------------------------------
# DataGovClient
# ---------------------------------------------------------------------------


class DataGovClient:
    """Client for data.gov.in Open Government Data Platform API.

    Parameters
    ----------
    api_key:
        OGD platform API key.  If ``None``, the client will attempt to
        work without authentication (limited access) or use the value
        from the ``DATA_GOV_API_KEY`` environment variable.
    cache:
        A :class:`CacheManager` instance for caching API responses.
    """

    BASE_URL = "https://api.data.gov.in"

    def __init__(
        self,
        cache: CacheManager,
        api_key: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._cache = cache
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
            headers={
                "User-Agent": (
                    "HaqSetu/1.0 (Government Scheme Aggregator; "
                    "contact: support@haqsetu.in)"
                ),
                "Accept": "application/json",
            },
            follow_redirects=True,
        )
        self._semaphore = asyncio.Semaphore(3)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_params(self, **kwargs: Any) -> dict[str, str]:
        """Build query parameters, injecting the API key if available."""
        params: dict[str, str] = {}
        if self._api_key:
            params["api-key"] = self._api_key
        for key, value in kwargs.items():
            if value is not None:
                params[key] = str(value)
        return params

    async def _get(self, path: str, **params: Any) -> dict | list | None:
        """Issue a rate-limited, cached GET request to the OGD API."""
        cache_key = f"datagov:{path}:{hash(frozenset(params.items()))}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        async with self._semaphore:
            try:
                response = await self._client.get(
                    path, params=self._build_params(**params)
                )

                if response.status_code == 403:
                    logger.warning(
                        "datagov.api_key_invalid_or_missing",
                        status=response.status_code,
                    )
                    return None

                if response.status_code == 429:
                    logger.warning("datagov.rate_limited")
                    return None

                response.raise_for_status()
                data = response.json()

                await self._cache.set(
                    cache_key, data, ttl_seconds=_API_CACHE_TTL
                )
                return data

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "datagov.http_error",
                    status=exc.response.status_code,
                    path=path,
                )
                return None
            except Exception:
                logger.warning("datagov.request_failed", path=path, exc_info=True)
                return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_datasets(
        self,
        query: str = "government schemes",
        format: str = "json",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Search for scheme-related datasets on data.gov.in.

        Parameters
        ----------
        query:
            Search query string.
        format:
            Desired response format (``"json"``, ``"csv"``, ``"xml"``).
        limit:
            Maximum number of results to return.
        offset:
            Offset for pagination.

        Returns
        -------
        list[dict]
            List of dataset metadata dictionaries.
        """
        data = await self._get(
            "/resource/search",
            query=query,
            format=format,
            limit=limit,
            offset=offset,
        )

        if data is None:
            return []

        if isinstance(data, dict):
            # OGD wraps results in various structures
            records = (
                data.get("records", [])
                or data.get("data", [])
                or data.get("results", [])
            )
            return records if isinstance(records, list) else []

        return data if isinstance(data, list) else []

    async def fetch_dataset(
        self,
        resource_id: str,
        limit: int = 500,
        offset: int = 0,
        filters: dict[str, str] | None = None,
    ) -> list[dict]:
        """Fetch a specific dataset by its OGD resource ID.

        Parameters
        ----------
        resource_id:
            The unique resource identifier on data.gov.in.
        limit:
            Maximum number of records to fetch.
        offset:
            Record offset for pagination.
        filters:
            Optional key-value filters to apply to the dataset.

        Returns
        -------
        list[dict]
            List of data records from the dataset.
        """
        params: dict[str, Any] = {
            "resource_id": resource_id,
            "format": "json",
            "limit": limit,
            "offset": offset,
        }

        if filters:
            for key, value in filters.items():
                params[f"filters[{key}]"] = value

        data = await self._get("/resource/data", **params)

        if data is None:
            return []

        if isinstance(data, dict):
            records = data.get("records", data.get("data", []))
            return records if isinstance(records, list) else []

        return data if isinstance(data, list) else []

    async def fetch_scheme_expenditure_data(self) -> list[dict]:
        """Fetch scheme-wise expenditure data from Ministry of Finance datasets.

        Returns data on government spending across various welfare schemes,
        useful for enriching scheme records with financial context.

        Returns
        -------
        list[dict]
            Expenditure records with fields like scheme_name, amount,
            financial_year, ministry, etc.
        """
        resource_id = _KNOWN_RESOURCES.get("scheme_expenditure")
        if not resource_id:
            return []

        logger.info("datagov.fetching_expenditure_data", resource_id=resource_id)

        records = await self.fetch_dataset(resource_id, limit=500)

        # Also try a broader search if the specific resource fails
        if not records:
            logger.info("datagov.expenditure_fallback_search")
            search_results = await self.search_datasets(
                query="scheme wise expenditure central government",
                limit=10,
            )
            for result in search_results:
                rid = result.get("resource_id") or result.get("id")
                if rid:
                    records = await self.fetch_dataset(rid, limit=500)
                    if records:
                        break

        logger.info(
            "datagov.expenditure_data_fetched", record_count=len(records)
        )
        return records

    async def fetch_beneficiary_data(self) -> list[dict]:
        """Fetch scheme beneficiary statistics from various government datasets.

        Queries multiple known datasets for beneficiary counts across
        major schemes (PM-KISAN, MGNREGA, social welfare, etc.).

        Returns
        -------
        list[dict]
            Beneficiary records with fields like scheme_name,
            beneficiary_count, state, year, etc.
        """
        all_records: list[dict] = []

        resource_ids = [
            _KNOWN_RESOURCES.get("social_welfare_beneficiaries"),
            _KNOWN_RESOURCES.get("pmkisan_beneficiaries"),
            _KNOWN_RESOURCES.get("mgnrega_data"),
        ]

        tasks = []
        for rid in resource_ids:
            if rid:
                tasks.append(self.fetch_dataset(rid, limit=200))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, list):
                    all_records.extend(result)
                elif isinstance(result, Exception):
                    logger.warning(
                        "datagov.beneficiary_fetch_error",
                        error=str(result),
                    )

        # Supplement with search-based discovery
        if not all_records:
            logger.info("datagov.beneficiary_fallback_search")
            search_results = await self.search_datasets(
                query="government scheme beneficiaries India",
                limit=5,
            )
            for result in search_results:
                rid = result.get("resource_id") or result.get("id")
                if rid:
                    try:
                        records = await self.fetch_dataset(rid, limit=200)
                        all_records.extend(records)
                    except Exception:
                        pass

        logger.info(
            "datagov.beneficiary_data_fetched",
            total_records=len(all_records),
        )
        return all_records

    async def fetch_all_supplementary_data(self) -> dict[str, list[dict]]:
        """Fetch all available supplementary data in parallel.

        Returns
        -------
        dict[str, list[dict]]
            Dictionary mapping data type names to their records.
        """
        expenditure_task = self.fetch_scheme_expenditure_data()
        beneficiary_task = self.fetch_beneficiary_data()

        expenditure_result, beneficiary_result = await asyncio.gather(
            expenditure_task,
            beneficiary_task,
            return_exceptions=True,
        )

        result: dict[str, list[dict]] = {}

        if isinstance(expenditure_result, list):
            result["expenditure"] = expenditure_result
        else:
            logger.warning(
                "datagov.expenditure_error",
                error=str(expenditure_result),
            )
            result["expenditure"] = []

        if isinstance(beneficiary_result, list):
            result["beneficiaries"] = beneficiary_result
        else:
            logger.warning(
                "datagov.beneficiary_error",
                error=str(beneficiary_result),
            )
            result["beneficiaries"] = []

        return result
