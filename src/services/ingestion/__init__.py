"""Automated scheme ingestion pipeline for HaqSetu.

Fetches government scheme data from multiple authoritative Indian government
sources, merges, deduplicates, and keeps the local scheme database current.

Sources:
  - MyScheme.gov.in  -- primary, most comprehensive catalogue
  - data.gov.in      -- supplementary financial and beneficiary data
  - Bundled seed data -- offline fallback

Public API::

    from src.services.ingestion import (
        MySchemeClient,
        DataGovClient,
        SchemeIngestionPipeline,
        IngestionScheduler,
    )
"""

from __future__ import annotations

from src.services.ingestion.data_gov_client import DataGovClient
from src.services.ingestion.myscheme_client import MySchemeClient
from src.services.ingestion.pipeline import SchemeIngestionPipeline
from src.services.ingestion.scheduler import IngestionScheduler

__all__ = [
    "MySchemeClient",
    "DataGovClient",
    "SchemeIngestionPipeline",
    "IngestionScheduler",
]
