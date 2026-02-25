"""Nearby Services API endpoints for HaqSetu.

Provides endpoints for finding nearby government offices,
CSCs, courts, and legal aid centers.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/nearby", tags=["nearby-services"])


@router.get("/find")
async def find_nearby_services(
    latitude: float,
    longitude: float,
    service_type: str = "csc",
    radius_km: float = 25.0,
    request: Request = None,
) -> dict:
    """Find nearby government services by location.

    Service types: csc, dlsa, tehsil, block_office, post_office,
    bank, court, police_station, hospital.
    """
    locator = getattr(request.app.state, "nearby_services", None)
    if locator is None:
        raise HTTPException(status_code=503, detail="Nearby services not available")

    results = locator.find_nearby(latitude, longitude, service_type, radius_km)
    return {
        "service_type": service_type,
        "radius_km": radius_km,
        "results": [
            {
                "name": r.name,
                "type": r.service_type,
                "address": r.address,
                "phone": r.phone,
                "distance_km": r.distance_km,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "hours": r.hours,
            }
            for r in results
        ],
        "count": len(results),
    }


@router.get("/dlsa/{state}")
async def get_dlsa_info(
    state: str, district: str = "", request: Request = None
) -> dict:
    """Get District Legal Services Authority (DLSA) information.

    DLSA provides free legal aid to eligible citizens under the
    Legal Services Authorities Act, 1987.
    """
    locator = getattr(request.app.state, "nearby_services", None)
    if locator is None:
        raise HTTPException(status_code=503, detail="Nearby services not available")

    info = locator.get_dlsa_info(state, district)
    return {
        "state": state,
        "district": district or "all",
        "dlsa_name": info.name,
        "address": info.address,
        "phone": info.phone,
        "email": info.email,
        "website": info.website,
        "services": info.services,
        "note": (
            "DLSA provides free legal aid to SC/ST, women, children, "
            "persons with disabilities, victims of trafficking, industrial "
            "workers, and persons with annual income below the prescribed limit."
        ),
    }


@router.get("/csc/{pin_code}")
async def get_csc_by_pincode(
    pin_code: str, request: Request
) -> dict:
    """Find Common Service Centres (CSC) by PIN code.

    CSCs provide digital services including scheme applications,
    certificate generation, and banking services in rural areas.
    """
    locator = getattr(request.app.state, "nearby_services", None)
    if locator is None:
        raise HTTPException(status_code=503, detail="Nearby services not available")

    if not pin_code.isdigit() or len(pin_code) != 6:
        raise HTTPException(status_code=400, detail="Invalid PIN code. Must be 6 digits.")

    results = locator.get_csc_info(pin_code)
    return {
        "pin_code": pin_code,
        "cscs": [
            {
                "name": c.name,
                "address": c.address,
                "phone": c.phone,
                "vle_name": c.vle_name,
                "services": c.services,
            }
            for c in results
        ],
        "count": len(results),
    }


@router.get("/directory/{state}")
async def get_service_directory(
    state: str,
    service_type: str = "all",
    request: Request = None,
) -> dict:
    """Get a directory of government services in a state."""
    locator = getattr(request.app.state, "nearby_services", None)
    if locator is None:
        raise HTTPException(status_code=503, detail="Nearby services not available")

    services = locator.get_service_directory(state, service_type)
    return {
        "state": state,
        "service_type": service_type,
        "services": [
            {
                "name": s.name,
                "type": s.service_type,
                "address": s.address,
                "phone": s.phone,
            }
            for s in services
        ],
        "count": len(services),
    }
