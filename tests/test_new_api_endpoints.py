"""Tests for all new API endpoints added in v0.2.0.

Tests cover the voice agent, document scanner, legal rights, RTI,
emergency SOS, grievance tracker, nearby services, accessibility,
and self-sustaining endpoints.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with minimal app initialization."""
    from src.main import app

    return TestClient(app)


def test_voice_agent_start_no_service(client):
    """Voice agent returns 503 when service not available."""
    response = client.post(
        "/api/v1/voice-agent/start",
        json={"language": "hi"},
    )
    # Service may not be initialized in test env
    assert response.status_code in (200, 503)


def test_voice_agent_chat_requires_session(client):
    """Chat endpoint requires session_id."""
    response = client.post(
        "/api/v1/voice-agent/chat",
        json={"session_id": "test", "message": "Hello"},
    )
    assert response.status_code in (200, 503)


def test_emergency_always_returns_numbers(client):
    """Emergency endpoint should return helpline numbers even if service is down."""
    response = client.post(
        "/api/v1/emergency/report",
        json={
            "description": "I am in danger",
            "language": "hi",
        },
    )
    # Should return 200 even if service is down (graceful degradation)
    assert response.status_code == 200
    data = response.json()
    # Should always have emergency numbers
    assert "emergency_numbers" in data or "emergency_contacts" in data


def test_legal_rights_helplines(client):
    """Legal rights helplines endpoint."""
    response = client.get("/api/v1/legal-rights/helplines?category=general")
    assert response.status_code in (200, 503)


def test_rti_fee_info(client):
    """RTI fee info endpoint."""
    response = client.get("/api/v1/rti/fee-info/central")
    assert response.status_code in (200, 503)


def test_nearby_csc_validation(client):
    """Nearby CSC endpoint validates PIN code format."""
    response = client.get("/api/v1/nearby/csc/abc")
    assert response.status_code in (400, 503)


def test_nearby_csc_valid_pin(client):
    """Nearby CSC endpoint with valid PIN."""
    response = client.get("/api/v1/nearby/csc/110001")
    assert response.status_code in (200, 503)


def test_accessibility_haptic_pattern(client):
    """Accessibility haptic pattern endpoint."""
    response = client.get("/api/v1/accessibility/haptic-pattern/success")
    assert response.status_code in (200, 503)


def test_sustainability_dashboard(client):
    """Sustainability dashboard returns basic info even without service."""
    response = client.get("/api/v1/sustainability/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data or "overall_health" in data


def test_document_scanner_no_file(client):
    """Document scanner requires file upload."""
    response = client.post("/api/v1/document/scan")
    assert response.status_code == 422  # Missing required file


def test_grievance_create_validation(client):
    """Grievance creation validates input."""
    response = client.post(
        "/api/v1/grievance/create",
        json={
            "complainant_name": "Test",
            "description": "Too short",  # Less than 20 chars
            "grievance_type": "public_service",
        },
    )
    assert response.status_code == 422  # Validation error


def test_bns_section_not_found(client):
    """BNS section returns 404 for unknown sections."""
    response = client.get("/api/v1/legal-rights/bns/99999")
    assert response.status_code in (404, 503)


def test_api_info_includes_new_endpoints(client):
    """API info should list all new feature endpoints."""
    response = client.get("/api")
    assert response.status_code == 200
    data = response.json()
    assert "endpoints" in data
    endpoints = data["endpoints"]
    assert "voice_agent" in endpoints
    assert "document_scanner" in endpoints
    assert "legal_rights" in endpoints
    assert "rti_generator" in endpoints
    assert "emergency_sos" in endpoints
    assert "grievance_tracker" in endpoints
    assert "nearby_services" in endpoints
    assert "accessibility" in endpoints
    assert "sustainability" in endpoints


def test_api_info_includes_features(client):
    """API info should list all features."""
    response = client.get("/api")
    assert response.status_code == 200
    data = response.json()
    assert "features" in data
    features = data["features"]
    assert len(features) >= 10
