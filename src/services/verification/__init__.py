"""Scheme verification services for HaqSetu.

Verifies government scheme legitimacy by cross-referencing against
authoritative legal and government sources.

Sources (in trust order):
  - Gazette of India (egazette.gov.in) -- legal document of record
  - India Code (indiacode.nic.in) -- full text of enabling Acts
  - Parliament (sansad.in) -- bills/acts passed by Lok Sabha/Rajya Sabha
  - MyScheme.gov.in via API Setu -- official scheme catalogue
  - data.gov.in -- supplementary government data

IMPORTANT: Only official government documents serve as valid proof.
All other sources carry zero trust weight.

Public API::

    from src.services.verification import (
        GazetteClient,
        IndiaCodeClient,
        SansadClient,
        SchemeVerificationEngine,
    )
"""

from __future__ import annotations

from src.services.verification.engine import SchemeVerificationEngine
from src.services.verification.gazette_client import GazetteClient
from src.services.verification.indiacode_client import IndiaCodeClient
from src.services.verification.sansad_client import SansadClient

__all__ = [
    "GazetteClient",
    "IndiaCodeClient",
    "SansadClient",
    "SchemeVerificationEngine",
]
