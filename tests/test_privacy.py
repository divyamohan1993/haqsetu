"""Tests for DPDPA privacy middleware (PII sanitisation)."""

from __future__ import annotations

import pytest

from src.middleware.privacy import (
    sanitize_aadhaar,
    sanitize_email,
    sanitize_phone,
    sanitize_pii,
)


# -----------------------------------------------------------------------
# Aadhaar sanitisation tests
# -----------------------------------------------------------------------


class TestSanitizeAadhaar:
    def test_aadhaar_with_spaces(self) -> None:
        result = sanitize_aadhaar("My Aadhaar is 1234 5678 9012")
        assert result == "My Aadhaar is XXXX-XXXX-9012", (
            f"Aadhaar with spaces should be masked: got '{result}'"
        )

    def test_aadhaar_with_dashes(self) -> None:
        result = sanitize_aadhaar("Aadhaar: 1234-5678-9012")
        assert result == "Aadhaar: XXXX-XXXX-9012", (
            f"Aadhaar with dashes should be masked: got '{result}'"
        )

    def test_aadhaar_without_separator(self) -> None:
        result = sanitize_aadhaar("Number is 123456789012")
        assert result == "Number is XXXX-XXXX-9012", (
            f"Aadhaar without separators should be masked: got '{result}'"
        )

    def test_aadhaar_preserves_last_four(self) -> None:
        result = sanitize_aadhaar("1111 2222 3456")
        assert "3456" in result, "last 4 digits should be preserved"
        assert "1111" not in result, "first 4 digits should be masked"
        assert "2222" not in result, "middle 4 digits should be masked"

    def test_multiple_aadhaar_numbers(self) -> None:
        text = "User1: 1234 5678 9012, User2: 9876 5432 1098"
        result = sanitize_aadhaar(text)
        assert "XXXX-XXXX-9012" in result, "first Aadhaar should be masked"
        assert "XXXX-XXXX-1098" in result, "second Aadhaar should be masked"

    def test_no_aadhaar_unchanged(self) -> None:
        text = "No Aadhaar number here, just regular text."
        result = sanitize_aadhaar(text)
        assert result == text, "text without Aadhaar should be unchanged"

    def test_aadhaar_mixed_separators(self) -> None:
        result = sanitize_aadhaar("1234-5678 9012")
        assert result == "XXXX-XXXX-9012", (
            f"Aadhaar with mixed separators should be masked: got '{result}'"
        )


# -----------------------------------------------------------------------
# Phone sanitisation tests
# -----------------------------------------------------------------------


class TestSanitizePhone:
    def test_indian_phone_with_plus91(self) -> None:
        result = sanitize_phone("Call me at +91 9876543210")
        assert "XXXXXX3210" in result, (
            f"Indian phone with +91 should be masked, preserving last 4: got '{result}'"
        )

    def test_indian_phone_with_plus91_dash(self) -> None:
        result = sanitize_phone("Phone: +91-9876543210")
        assert "XXXXXX3210" in result, (
            f"Indian phone with +91- should be masked: got '{result}'"
        )

    def test_bare_10_digit_indian_phone(self) -> None:
        result = sanitize_phone("Number: 9876543210")
        assert "XXXXXX3210" in result, (
            f"bare 10-digit Indian phone should be masked: got '{result}'"
        )

    def test_preserves_last_four_digits(self) -> None:
        result = sanitize_phone("+91 7890123456")
        assert "3456" in result, "last 4 digits of phone should be preserved"

    def test_no_phone_unchanged(self) -> None:
        text = "No phone number here."
        result = sanitize_phone(text)
        assert result == text, "text without phone numbers should be unchanged"

    def test_phone_starting_with_6(self) -> None:
        result = sanitize_phone("Call 6123456789")
        assert "XXXXXX6789" in result, "phone starting with 6 should be masked"

    def test_phone_starting_with_7(self) -> None:
        result = sanitize_phone("Call 7123456789")
        assert "XXXXXX6789" in result, "phone starting with 7 should be masked"

    def test_phone_starting_with_8(self) -> None:
        result = sanitize_phone("Call 8123456789")
        assert "XXXXXX6789" in result, "phone starting with 8 should be masked"

    def test_phone_starting_with_9(self) -> None:
        result = sanitize_phone("Call 9123456789")
        assert "XXXXXX6789" in result, "phone starting with 9 should be masked"


# -----------------------------------------------------------------------
# Email sanitisation tests
# -----------------------------------------------------------------------


class TestSanitizeEmail:
    def test_basic_email(self) -> None:
        result = sanitize_email("Email: user@example.com")
        assert "[EMAIL_REDACTED]" in result, "email should be replaced with [EMAIL_REDACTED]"
        assert "user@example.com" not in result, "original email should be removed"

    def test_multiple_emails(self) -> None:
        text = "Contact user1@gov.in or user2@mail.com"
        result = sanitize_email(text)
        assert result.count("[EMAIL_REDACTED]") == 2, "both emails should be redacted"

    def test_no_email_unchanged(self) -> None:
        text = "No email here."
        result = sanitize_email(text)
        assert result == text


# -----------------------------------------------------------------------
# Combined sanitize_pii tests
# -----------------------------------------------------------------------


class TestSanitizePII:
    def test_combined_aadhaar_and_phone(self) -> None:
        text = "Aadhaar: 1234 5678 9012, Phone: +91 9876543210"
        result = sanitize_pii(text)
        assert "XXXX-XXXX-9012" in result, "Aadhaar should be masked in combined PII sanitisation"
        assert "XXXXXX3210" in result, "Phone should be masked in combined PII sanitisation"

    def test_combined_all_pii(self) -> None:
        text = "Aadhaar: 1234 5678 9012, Phone: 9876543210, Email: user@gov.in"
        result = sanitize_pii(text)
        assert "XXXX-XXXX-9012" in result, "Aadhaar should be masked"
        assert "XXXXXX3210" in result, "Phone should be masked"
        assert "[EMAIL_REDACTED]" in result, "Email should be redacted"
        assert "user@gov.in" not in result, "original email should be removed"

    def test_no_pii_unchanged(self) -> None:
        text = "This is a regular message with no PII."
        result = sanitize_pii(text)
        assert result == text, "text without PII should be unchanged"

    def test_sanitize_order_aadhaar_before_phone(self) -> None:
        """Aadhaar (12 digits) is sanitised before phone to avoid partial matches."""
        text = "1234 5678 9012"
        result = sanitize_pii(text)
        # The 12-digit sequence should be treated as Aadhaar, not phone.
        assert "XXXX-XXXX-9012" in result, (
            "12-digit sequence should be treated as Aadhaar, masked as XXXX-XXXX-XXXX"
        )

    def test_mixed_text_with_pii(self) -> None:
        text = (
            "Dear citizen, your Aadhaar 1234-5678-9012 and phone +91-9876543210 "
            "have been verified. Contact support@haqsetu.gov.in for queries."
        )
        result = sanitize_pii(text)
        assert "XXXX-XXXX-9012" in result
        assert "XXXXXX3210" in result
        assert "[EMAIL_REDACTED]" in result
        assert "Dear citizen" in result, "non-PII text should be preserved"

    def test_empty_string(self) -> None:
        assert sanitize_pii("") == "", "empty string should return empty string"
