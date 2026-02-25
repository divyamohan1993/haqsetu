"""Grievance Tracker service for HaqSetu.

Helps citizens draft, file, and track grievances against government
departments and public services.  India has a rich ecosystem of
grievance redressal portals -- CPGRAMS at the centre, IGMS for
insurance, and state-specific portals -- but most citizens, especially
in rural areas, are unaware of where to file or how to word their
complaint effectively.

This service:

1. **Identifies the correct portal** based on grievance type, department,
   and state -- selecting from CPGRAMS, IGMS, state CM portals, or
   department-specific systems.
2. **Uses the LLM to format the complaint** in clear, formal language that
   maximises the chance of acceptance and timely resolution.
3. **Provides step-by-step filing instructions** tailored to the selected
   portal.
4. **Maps the full escalation path** with realistic timelines so the
   citizen knows whom to approach if the grievance is not resolved.
5. **Generates a unique tracking ID** for internal reference.

Portal data sources:
    * Department of Administrative Reforms & Public Grievances (DARPG)
    * CPGRAMS: https://pgportal.gov.in
    * IGMS (IRDAI): https://igms.irda.gov.in
    * State government CM helpline / Janshikayat portals
    * RTI Online: https://rtionline.gov.in
    * National Consumer Helpline: https://consumerhelpline.gov.in
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import structlog

if TYPE_CHECKING:
    from src.services.llm import LLMService

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Grievance type classification
# ---------------------------------------------------------------------------


class GrievanceType(StrEnum):
    """Supported categories of grievances."""

    __slots__ = ()

    PUBLIC_SERVICE = "public_service"
    CORRUPTION = "corruption"
    DELAY = "delay"
    DISCRIMINATION = "discrimination"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Data objects
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GrievanceRequest:
    """Incoming grievance filing request from the user."""

    complainant_name: str
    description: str
    grievance_type: str
    department: str = ""
    state: str = ""
    district: str = ""


@dataclass(slots=True)
class GrievanceDraft:
    """Draft grievance ready for filing on the recommended portal."""

    grievance_id: str
    formatted_complaint: str
    recommended_portal: str
    portal_url: str
    filing_steps: list[str]
    expected_timeline: str
    escalation_info: str


@dataclass(slots=True)
class EscalationPath:
    """Escalation hierarchy with timelines and tips."""

    levels: list[str]
    timelines: list[str]
    tips: list[str]


@dataclass(slots=True)
class PortalInfo:
    """Information about a specific grievance portal."""

    portal_name: str
    url: str
    helpline: str
    filing_steps: list[str]
    supported_types: list[str]


# ---------------------------------------------------------------------------
# Portal database
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class _PortalEntry:
    """Internal representation of a grievance redressal portal."""

    name: str
    url: str
    helpline: str
    filing_steps: list[str]
    supported_types: list[str]
    description: str = ""


# Central / national portals
_CENTRAL_PORTALS: Final[dict[str, _PortalEntry]] = {
    "cpgrams": _PortalEntry(
        name="CPGRAMS (Centralised Public Grievance Redress and Monitoring System)",
        url="https://pgportal.gov.in",
        helpline="011-23362312",
        description=(
            "Primary portal of the Government of India for lodging grievances "
            "against any central government ministry or department."
        ),
        filing_steps=[
            "Visit https://pgportal.gov.in and click 'Lodge Public Grievance'.",
            "Register with your mobile number and email address.",
            "Select the ministry/department your grievance pertains to.",
            "Fill in the grievance description, attach supporting documents (PDF, max 4 MB).",
            "Submit and note down the unique registration number.",
            "Track status using 'View Status' with your registration number.",
        ],
        supported_types=[
            "public_service",
            "corruption",
            "delay",
            "discrimination",
            "other",
        ],
    ),
    "igms": _PortalEntry(
        name="IGMS (Integrated Grievance Management System - IRDAI)",
        url="https://igms.irda.gov.in",
        helpline="155255",
        description="Insurance complaints against any insurer registered with IRDAI.",
        filing_steps=[
            "Visit https://igms.irda.gov.in and register as a policyholder.",
            "Select complaint type: claim-related, policy-related, or service-related.",
            "Enter your policy number and insurer details.",
            "Describe the grievance and upload supporting documents.",
            "Submit and note the complaint reference number.",
            "If unresolved within 15 days, escalate to the Insurance Ombudsman.",
        ],
        supported_types=["public_service", "delay", "other"],
    ),
    "rti_online": _PortalEntry(
        name="RTI Online Portal",
        url="https://rtionline.gov.in",
        helpline="011-24629841",
        description=(
            "File RTI applications to central government bodies under the "
            "Right to Information Act, 2005."
        ),
        filing_steps=[
            "Visit https://rtionline.gov.in and click 'Submit Request'.",
            "Select the public authority (ministry/department).",
            "Enter the information you are seeking clearly and precisely.",
            "Pay the fee of Rs 10 via internet banking, credit/debit card, or UPI.",
            "Submit and save the registration number for tracking.",
            "Expect a response within 30 days; file first appeal if no response.",
        ],
        supported_types=["public_service", "delay", "corruption", "other"],
    ),
    "consumer_helpline": _PortalEntry(
        name="National Consumer Helpline",
        url="https://consumerhelpline.gov.in",
        helpline="1800-11-4000",
        description="Consumer complaints about products, services, and unfair trade practices.",
        filing_steps=[
            "Call 1800-11-4000 (toll-free) or visit https://consumerhelpline.gov.in.",
            "Register using mobile number and email.",
            "File complaint under the relevant category (banking, telecom, e-commerce, etc.).",
            "Upload invoice/receipts and supporting documents.",
            "Submit and note the docket number for tracking.",
            "If unresolved, approach the District Consumer Disputes Redressal Forum.",
        ],
        supported_types=["public_service", "delay", "discrimination", "other"],
    ),
    "anti_corruption": _PortalEntry(
        name="Central Vigilance Commission (CVC)",
        url="https://cvc.gov.in",
        helpline="011-24600200",
        description="Complaints related to corruption by central government employees.",
        filing_steps=[
            "Visit https://cvc.gov.in and navigate to 'Lodge Complaint'.",
            "Provide your full name and contact details (anonymous complaints are not entertained).",
            "Describe the corrupt practice with specific details: who, when, where.",
            "Attach documentary evidence if available.",
            "Submit and note the reference number.",
            "CVC will forward the complaint to the relevant Chief Vigilance Officer.",
        ],
        supported_types=["corruption"],
    ),
    "nhrc": _PortalEntry(
        name="National Human Rights Commission (NHRC)",
        url="https://nhrc.nic.in",
        helpline="011-23385368",
        description="Complaints about violation of human rights by public servants.",
        filing_steps=[
            "Visit https://nhrc.nic.in and click 'Complaint' section.",
            "Register and fill in complainant details.",
            "Describe the human rights violation with dates and involved officials.",
            "Attach supporting documents, medical reports, or FIR copies.",
            "Submit and track using the file number provided.",
            "NHRC may call for an inquiry or direct the authorities to take action.",
        ],
        supported_types=["discrimination", "corruption", "public_service", "other"],
    ),
}

# State-specific portals
_STATE_PORTALS: Final[dict[str, _PortalEntry]] = {
    "uttar_pradesh": _PortalEntry(
        name="UP Jansunwai Portal",
        url="https://jansunwai.up.nic.in",
        helpline="1076",
        description="Chief Minister's grievance redressal portal for Uttar Pradesh.",
        filing_steps=[
            "Visit https://jansunwai.up.nic.in and register with mobile number.",
            "Click 'शिकायत दर्ज करें' (Lodge Complaint).",
            "Select the department and district.",
            "Describe your grievance in Hindi or English.",
            "Upload supporting documents (photo/PDF).",
            "Submit and note the complaint ID for tracking.",
            "Track status via 'शिकायत की स्थिति' on the portal.",
        ],
        supported_types=[
            "public_service",
            "corruption",
            "delay",
            "discrimination",
            "other",
        ],
    ),
    "maharashtra": _PortalEntry(
        name="Maharashtra Aaple Sarkar",
        url="https://aaplesarkar.mahaonline.gov.in",
        helpline="1800-120-8040",
        description="Integrated service delivery and grievance portal for Maharashtra.",
        filing_steps=[
            "Visit https://aaplesarkar.mahaonline.gov.in and register.",
            "Navigate to 'Grievance' section.",
            "Select the department and service category.",
            "Fill in the complaint details in Marathi or English.",
            "Attach relevant documents.",
            "Submit and save the application number.",
            "Track status online or call the helpline.",
        ],
        supported_types=[
            "public_service",
            "corruption",
            "delay",
            "discrimination",
            "other",
        ],
    ),
    "madhya_pradesh": _PortalEntry(
        name="MP CM Helpline",
        url="https://cmhelpline.mp.gov.in",
        helpline="181",
        description="Chief Minister's helpline portal for Madhya Pradesh.",
        filing_steps=[
            "Call 181 (toll-free) or visit https://cmhelpline.mp.gov.in.",
            "Register with Aadhaar-linked mobile number.",
            "Select complaint category and department.",
            "Describe the issue in Hindi or English.",
            "Upload supporting photographs or documents.",
            "Submit and note the reference number.",
            "Expect first response within 7 working days.",
        ],
        supported_types=[
            "public_service",
            "corruption",
            "delay",
            "discrimination",
            "other",
        ],
    ),
    "rajasthan": _PortalEntry(
        name="Rajasthan Sampark Portal",
        url="https://sampark.rajasthan.gov.in",
        helpline="181",
        description="Rajasthan government's citizen grievance and service portal.",
        filing_steps=[
            "Visit https://sampark.rajasthan.gov.in and click 'शिकायत दर्ज करें'.",
            "Register using Jan Aadhaar or mobile number.",
            "Select the department and complaint category.",
            "Provide a clear description of the grievance.",
            "Upload supporting documents if available.",
            "Submit and note the complaint number.",
            "Track status using the complaint number on the portal.",
        ],
        supported_types=[
            "public_service",
            "corruption",
            "delay",
            "discrimination",
            "other",
        ],
    ),
    "karnataka": _PortalEntry(
        name="Karnataka Janaspandana",
        url="https://janaspandana.karnataka.gov.in",
        helpline="080-22230350",
        description="Karnataka government's public grievance redressal system.",
        filing_steps=[
            "Visit https://janaspandana.karnataka.gov.in.",
            "Register with your mobile number.",
            "Select district, taluk, and department.",
            "Describe the grievance in Kannada or English.",
            "Upload relevant documents.",
            "Submit and note the token number.",
            "Track status online or visit the nearest Janaspandana counter.",
        ],
        supported_types=[
            "public_service",
            "corruption",
            "delay",
            "discrimination",
            "other",
        ],
    ),
    "tamil_nadu": _PortalEntry(
        name="Tamil Nadu CM Cell",
        url="https://cmcell.tn.gov.in",
        helpline="1100",
        description="Chief Minister's Special Cell for grievance redressal in Tamil Nadu.",
        filing_steps=[
            "Visit https://cmcell.tn.gov.in or call 1100.",
            "Register using mobile number and Aadhaar.",
            "Select the department and petition type.",
            "Describe the grievance in Tamil or English.",
            "Upload supporting documents.",
            "Submit and save the petition number.",
            "Expect acknowledgement within 3 working days.",
        ],
        supported_types=[
            "public_service",
            "corruption",
            "delay",
            "discrimination",
            "other",
        ],
    ),
    "bihar": _PortalEntry(
        name="Bihar CM Janata Darbar",
        url="https://lokshikayat.bihar.gov.in",
        helpline="1800-345-6188",
        description="Bihar government's public grievance portal.",
        filing_steps=[
            "Visit https://lokshikayat.bihar.gov.in.",
            "Register with your mobile number.",
            "Select the department and district.",
            "Describe the grievance in Hindi or English.",
            "Upload supporting documents if available.",
            "Submit and note the grievance number.",
            "Track status online or at the district office.",
        ],
        supported_types=[
            "public_service",
            "corruption",
            "delay",
            "discrimination",
            "other",
        ],
    ),
    "west_bengal": _PortalEntry(
        name="West Bengal Duare Sarkar / Didi Ke Bolo",
        url="https://dfrportal.wb.gov.in",
        helpline="1800-345-2244",
        description="West Bengal government's integrated grievance portal.",
        filing_steps=[
            "Visit https://dfrportal.wb.gov.in or call 1800-345-2244.",
            "Register with mobile number and Aadhaar.",
            "Select the scheme or service category.",
            "Describe your grievance in Bengali or English.",
            "Upload supporting documents.",
            "Submit and note the reference number.",
            "Visit nearest Duare Sarkar camp for in-person assistance.",
        ],
        supported_types=[
            "public_service",
            "delay",
            "discrimination",
            "other",
        ],
    ),
    "kerala": _PortalEntry(
        name="Kerala CM's Public Grievance Redressal Cell",
        url="https://pgrm.kerala.gov.in",
        helpline="0471-2333812",
        description="Kerala Chief Minister's public grievance redressal portal.",
        filing_steps=[
            "Visit https://pgrm.kerala.gov.in.",
            "Register with mobile number and email.",
            "Select the department and grievance category.",
            "Describe the issue in Malayalam or English.",
            "Upload relevant documents.",
            "Submit and save the complaint number.",
            "Track status online using the complaint number.",
        ],
        supported_types=[
            "public_service",
            "corruption",
            "delay",
            "discrimination",
            "other",
        ],
    ),
    "gujarat": _PortalEntry(
        name="Gujarat Swagat Online",
        url="https://swagat.gujarat.gov.in",
        helpline="1800-233-5500",
        description="Gujarat CM's grievance redressal system (SWAGAT).",
        filing_steps=[
            "Visit https://swagat.gujarat.gov.in.",
            "Register with your mobile number and Aadhaar.",
            "Select the district and department.",
            "Describe the grievance in Gujarati or English.",
            "Upload supporting documents.",
            "Submit and note the application number.",
            "Attend the next SWAGAT session if summoned (monthly video conference by CM).",
        ],
        supported_types=[
            "public_service",
            "corruption",
            "delay",
            "discrimination",
            "other",
        ],
    ),
    "delhi": _PortalEntry(
        name="Delhi e-District / CM Window",
        url="https://edistrict.delhigovt.nic.in",
        helpline="1031",
        description="Delhi government's integrated grievance and e-District portal.",
        filing_steps=[
            "Visit https://edistrict.delhigovt.nic.in or call 1031.",
            "Register using Aadhaar or voter ID.",
            "Navigate to 'Public Grievance' and select category.",
            "Describe the grievance in Hindi or English.",
            "Upload supporting documents.",
            "Submit and note the application ID.",
            "Track status online or visit the District Magistrate office.",
        ],
        supported_types=[
            "public_service",
            "corruption",
            "delay",
            "discrimination",
            "other",
        ],
    ),
}

# Department-specific portals
_DEPARTMENT_PORTALS: Final[dict[str, _PortalEntry]] = {
    "police": _PortalEntry(
        name="National Cyber Crime Reporting Portal / State Police Portal",
        url="https://cybercrime.gov.in",
        helpline="1930",
        description="Cyber crime and general police complaint portal.",
        filing_steps=[
            "For cyber crimes: visit https://cybercrime.gov.in.",
            "For general complaints: visit your state police website or dial 100/112.",
            "Register with mobile number.",
            "Select complaint type (financial fraud, women/child, other cyber crime).",
            "Provide incident details with dates and evidence.",
            "Submit and note the complaint number.",
            "Follow up with the local police station if required.",
        ],
        supported_types=["corruption", "discrimination", "public_service", "other"],
    ),
    "railways": _PortalEntry(
        name="RailMadad (Indian Railways Grievance Portal)",
        url="https://railmadad.indianrailways.gov.in",
        helpline="139",
        description="Official Indian Railways complaint and helpline portal.",
        filing_steps=[
            "Call 139 or visit https://railmadad.indianrailways.gov.in.",
            "Select complaint category: cleanliness, catering, security, punctuality, etc.",
            "Enter PNR number or train number if applicable.",
            "Describe the grievance with details.",
            "Upload photos if relevant.",
            "Submit and note the complaint ID.",
            "Expect SMS updates on the status.",
        ],
        supported_types=["public_service", "delay", "corruption", "other"],
    ),
    "income_tax": _PortalEntry(
        name="Income Tax e-Nivaran",
        url="https://www.incometax.gov.in/iec/foportal/help/e-nivaran",
        helpline="1800-103-0025",
        description="Income Tax department's grievance redressal system.",
        filing_steps=[
            "Login to https://www.incometax.gov.in using your PAN.",
            "Navigate to 'e-Nivaran' under the 'Services' tab.",
            "Select the grievance category (refund, processing, TDS, etc.).",
            "Enter assessment year and description.",
            "Upload supporting documents.",
            "Submit and note the e-Nivaran reference number.",
            "Expect resolution within 30 days.",
        ],
        supported_types=["public_service", "delay", "other"],
    ),
    "electricity": _PortalEntry(
        name="State Electricity Regulatory Commission / DISCOM Portal",
        url="https://cercind.gov.in",
        helpline="1912",
        description="Electricity-related complaints via state DISCOM or SERC.",
        filing_steps=[
            "Call 1912 or visit your state DISCOM website.",
            "Register a complaint with your consumer number.",
            "Select complaint type (billing, outage, new connection, meter).",
            "Provide meter number and location details.",
            "Submit and note the complaint/ticket number.",
            "If unresolved within 7 days, escalate to CGRF (Consumer Grievance Redressal Forum).",
            "Further escalation: Electricity Ombudsman of your state.",
        ],
        supported_types=["public_service", "delay", "corruption", "other"],
    ),
    "banking": _PortalEntry(
        name="RBI Complaint Management System (CMS)",
        url="https://cms.rbi.org.in",
        helpline="14440",
        description="Banking complaints via RBI's integrated ombudsman scheme.",
        filing_steps=[
            "First complain to your bank and wait 30 days.",
            "If unresolved, visit https://cms.rbi.org.in.",
            "Register using mobile number and email.",
            "Select the bank and complaint type (account, loan, card, digital payment).",
            "Enter details and upload bank correspondence.",
            "Submit and note the CMS complaint number.",
            "RBI Ombudsman will mediate and pass an award within 30 days.",
        ],
        supported_types=["public_service", "delay", "discrimination", "other"],
    ),
    "telecom": _PortalEntry(
        name="TRAI / Telecom Consumer Complaint Portal",
        url="https://tafcop.sancharsaathi.gov.in",
        helpline="1800-110-420",
        description="Telecom service complaints and SIM-related issues.",
        filing_steps=[
            "First complain to your telecom operator (call centre or app).",
            "If unresolved within 7 days, visit https://tafcop.sancharsaathi.gov.in.",
            "Register with your mobile number.",
            "Select complaint type (billing, network, porting, spam, etc.).",
            "Provide details and upload supporting documents.",
            "Submit and note the reference number.",
            "Escalate to the Telecom District Consumer Forum if still unresolved.",
        ],
        supported_types=["public_service", "delay", "other"],
    ),
    "land_revenue": _PortalEntry(
        name="State Revenue / Land Records Portal",
        url="https://dolr.gov.in",
        helpline="",
        description="Land-related grievances via state revenue department portals.",
        filing_steps=[
            "Visit your state's revenue/land records portal (Bhulekh, Bhoomi, etc.).",
            "Register with Aadhaar-linked mobile number.",
            "Select complaint type (mutation, encroachment, title dispute, etc.).",
            "Enter khasra/khata number and village details.",
            "Describe the grievance and upload documents (registry, map, etc.).",
            "Submit and note the reference number.",
            "Escalate to the Sub-Divisional Magistrate (SDM) if unresolved.",
        ],
        supported_types=["public_service", "delay", "corruption", "other"],
    ),
    "education": _PortalEntry(
        name="Ministry of Education Grievance Portal (via CPGRAMS)",
        url="https://pgportal.gov.in",
        helpline="011-23382698",
        description="Education-related grievances filed via CPGRAMS.",
        filing_steps=[
            "Visit https://pgportal.gov.in.",
            "Register and select 'Ministry of Education' as the department.",
            "Select sub-category: school education, higher education, scholarships, etc.",
            "Describe the grievance with institution name and details.",
            "Upload supporting documents.",
            "Submit and note the CPGRAMS registration number.",
            "Expect acknowledgement within 3 working days.",
        ],
        supported_types=["public_service", "delay", "discrimination", "corruption", "other"],
    ),
    "health": _PortalEntry(
        name="National Health Authority Grievance Portal",
        url="https://grievance.nha.gov.in",
        helpline="14555",
        description="Grievances related to Ayushman Bharat, hospitals, and health services.",
        filing_steps=[
            "Call 14555 or visit https://grievance.nha.gov.in.",
            "Register with your Ayushman Bharat Health ID or mobile number.",
            "Select complaint category (hospital, claim denial, quality, etc.).",
            "Describe the grievance with hospital name and dates.",
            "Upload supporting documents (discharge summary, bills, etc.).",
            "Submit and note the grievance reference number.",
            "Expect resolution within 30 days.",
        ],
        supported_types=["public_service", "delay", "discrimination", "other"],
    ),
    "pension": _PortalEntry(
        name="CPAO Pensioners' Portal",
        url="https://cpao.nic.in",
        helpline="011-26716637",
        description="Pension-related grievances for central government pensioners.",
        filing_steps=[
            "Visit https://cpao.nic.in and navigate to 'Pensioners' Corner'.",
            "Register with PPO number and bank account details.",
            "Select complaint type (pension not received, revision, commutation, etc.).",
            "Provide details with last pension credit date.",
            "Submit and note the reference number.",
            "Follow up with the Pay and Accounts Office if required.",
            "Escalate to CPGRAMS if unresolved within 30 days.",
        ],
        supported_types=["public_service", "delay", "other"],
    ),
}


# ---------------------------------------------------------------------------
# State name normalisation
# ---------------------------------------------------------------------------

_STATE_ALIASES: Final[dict[str, str]] = {
    "up": "uttar_pradesh",
    "uttar pradesh": "uttar_pradesh",
    "uttarpradesh": "uttar_pradesh",
    "maharashtra": "maharashtra",
    "mh": "maharashtra",
    "mp": "madhya_pradesh",
    "madhya pradesh": "madhya_pradesh",
    "madhyapradesh": "madhya_pradesh",
    "rajasthan": "rajasthan",
    "rj": "rajasthan",
    "karnataka": "karnataka",
    "ka": "karnataka",
    "tamil nadu": "tamil_nadu",
    "tamilnadu": "tamil_nadu",
    "tn": "tamil_nadu",
    "bihar": "bihar",
    "br": "bihar",
    "west bengal": "west_bengal",
    "westbengal": "west_bengal",
    "wb": "west_bengal",
    "kerala": "kerala",
    "kl": "kerala",
    "gujarat": "gujarat",
    "gj": "gujarat",
    "delhi": "delhi",
    "dl": "delhi",
    "new delhi": "delhi",
}


# ---------------------------------------------------------------------------
# Escalation paths database
# ---------------------------------------------------------------------------

_ESCALATION_PATHS: Final[dict[str, dict[str, list[str] | list[str] | list[str]]]] = {
    "public_service": {
        "levels": [
            "Concerned Officer / Dealing Clerk",
            "Section Officer / Branch Head",
            "Department Head / Director",
            "Secretary of the Department",
            "Chief Secretary / Additional Secretary",
            "Minister in charge",
            "Chief Minister's Office / PMO (via CPGRAMS)",
            "High Court (Writ Petition under Article 226)",
        ],
        "timelines": [
            "Immediate -- submit written application",
            "7 days -- if no response from concerned officer",
            "15 days -- if section officer does not resolve",
            "30 days -- if department head does not act",
            "45 days -- if secretary's office is unresponsive",
            "60 days -- escalate to political leadership",
            "90 days -- if political channel fails, use CPGRAMS",
            "120+ days -- approach the High Court as last resort",
        ],
        "tips": [
            "Always get a written acknowledgement (diary number) when submitting a complaint.",
            "Send your complaint via registered post with AD to create a paper trail.",
            "File an RTI application asking for the status -- this often accelerates action.",
            "Mention specific rules/acts violated to strengthen your complaint.",
            "Keep copies of all correspondence and maintain a timeline.",
            "Contact your local MLA/MP -- their recommendation carries weight.",
            "Use social media (Twitter/X) to tag official department handles for visibility.",
        ],
    },
    "corruption": {
        "levels": [
            "Vigilance Officer of the Department",
            "Chief Vigilance Officer (CVO)",
            "Central Vigilance Commission (CVC) / State Lokayukta",
            "Anti-Corruption Bureau (ACB) of the state",
            "Central Bureau of Investigation (CBI) -- for central government",
            "Lokpal of India (for senior officials)",
            "High Court / Supreme Court",
        ],
        "timelines": [
            "Immediate -- report to the Vigilance Officer with evidence",
            "15 days -- if Vigilance Officer does not act, approach CVO",
            "30 days -- file complaint with CVC (central) or Lokayukta (state)",
            "45 days -- file FIR with ACB if criminal corruption is involved",
            "60 days -- write to CBI (for central govt employees)",
            "90 days -- approach Lokpal for high-level corruption",
            "120+ days -- file a PIL or writ petition in court",
        ],
        "tips": [
            "Gather documentary evidence -- screenshots, recordings (where legal), receipts.",
            "File a written complaint with specific details: amount, date, place, persons involved.",
            "Anonymous complaints to CVC are NOT entertained; you must provide your identity.",
            "Under the Whistleblowers Protection Act 2014, your identity can be protected.",
            "An RTI application asking about the officer's dealings can be very effective.",
            "Media exposure (verified journalists) can create public pressure.",
            "Preserve all original documents and keep certified copies for submission.",
        ],
    },
    "delay": {
        "levels": [
            "Concerned Department Counter / Help Desk",
            "Public Grievance Officer of the Department",
            "Appellate Authority (under Right to Public Services Act)",
            "District Magistrate / Collector",
            "Divisional Commissioner",
            "State Grievance Portal (CM Helpline)",
            "CPGRAMS (Central) / High Court",
        ],
        "timelines": [
            "Immediate -- enquire at the department and get a written status",
            "7 days -- if no response, file a formal grievance with the PGO",
            "15 days -- invoke the Right to Public Services Act (where applicable)",
            "30 days -- escalate to the District Collector/DM",
            "45 days -- approach the Divisional Commissioner",
            "60 days -- file on the state CM helpline portal",
            "90+ days -- use CPGRAMS or approach the High Court",
        ],
        "tips": [
            "Many states have a 'Right to Service' act with guaranteed timelines -- check yours.",
            "Under the Right to Service Act, the erring officer can be fined Rs 250-5000/day.",
            "File an RTI asking for the specific reason for delay -- officials must respond in 30 days.",
            "Approach the District Legal Services Authority (DLSA) for free legal assistance.",
            "Simultaneously file on CPGRAMS -- it creates pressure from the central level.",
            "Document the delay with dates and keep all acknowledgement slips.",
        ],
    },
    "discrimination": {
        "levels": [
            "Concerned Authority / Institution Head",
            "District Social Welfare Officer",
            "National Commission for Scheduled Castes / Scheduled Tribes / OBC / Minorities",
            "National Commission for Women / State Women's Commission",
            "National Human Rights Commission (NHRC) / State HRC",
            "District Legal Services Authority (DLSA)",
            "High Court (Writ Petition) / Supreme Court",
        ],
        "timelines": [
            "Immediate -- file a written complaint with the institution head",
            "7 days -- report to the District Social Welfare Officer",
            "15 days -- file complaint with the relevant National Commission",
            "30 days -- approach NHRC/State HRC for human rights violation",
            "30 days -- contact DLSA for free legal aid and representation",
            "60 days -- if commissions do not act, file a court case",
            "90+ days -- approach the High Court via writ petition",
        ],
        "tips": [
            "For caste-based discrimination, invoke the SC/ST (Prevention of Atrocities) Act, 1989.",
            "For gender discrimination, approach the National Commission for Women (NCW).",
            "Document everything -- date, time, witnesses, exact words/actions.",
            "File an FIR if the discrimination constitutes a criminal offence.",
            "DLSA provides FREE legal representation -- you do not need to hire a lawyer.",
            "NHRC can order compensation and direct authorities to take action.",
            "Contact local NGOs working on the specific type of discrimination for support.",
        ],
    },
    "other": {
        "levels": [
            "Concerned Department / Office",
            "Public Grievance Officer",
            "District Administration (DM / Collector)",
            "State Grievance Portal",
            "CPGRAMS (Central Portal)",
            "Lokpal / Lokayukta",
            "High Court / Supreme Court",
        ],
        "timelines": [
            "Immediate -- submit written complaint to the concerned office",
            "7-15 days -- escalate to the Public Grievance Officer",
            "30 days -- approach the District Magistrate / Collector",
            "45 days -- file on the state grievance portal",
            "60 days -- file on CPGRAMS",
            "90 days -- approach Lokpal/Lokayukta",
            "120+ days -- approach the judiciary as last resort",
        ],
        "tips": [
            "Start with a written complaint and always get an acknowledgement.",
            "Use RTI to get information about rules, timelines, and responsible officers.",
            "DLSA offers free legal advice -- visit the nearest district court complex.",
            "CPGRAMS grievances are tracked at the PMO level and taken seriously.",
            "Keep a photocopy of every document you submit.",
            "Consult a local legal aid clinic or NGO for guidance specific to your issue.",
        ],
    },
}


# ---------------------------------------------------------------------------
# LLM prompt templates
# ---------------------------------------------------------------------------

_COMPLAINT_FORMAT_PROMPT: Final[str] = """\
You are a legal drafting assistant helping Indian citizens write formal \
grievance complaints to government portals. Your output must be:
- In clear, formal English suitable for a government grievance portal
- Structured with proper paragraphs
- Factual and specific
- Include a subject line
- Include a request for specific relief/action

Do NOT add any fictional details. Only use information provided by the user.

Complainant name: {complainant_name}
Grievance type: {grievance_type}
Department: {department}
State: {state}
District: {district}

User's description of the problem:
{description}

Please draft a formal grievance complaint that can be submitted on {portal_name}. \
Include:
1. A concise subject line (prefixed with "Subject: ")
2. Respectful salutation
3. Introduction of the complainant
4. Clear description of the grievance with relevant details
5. Specific relief/action requested
6. Closing with the complainant's name

Output ONLY the formatted complaint text, nothing else."""

_COMPLAINT_SYSTEM_INSTRUCTION: Final[str] = (
    "You are HaqSetu's grievance drafting assistant.  You help rural Indian "
    "citizens write clear, formal grievance complaints in English that can be "
    "submitted on government portals.  Be concise, factual, and respectful.  "
    "Do not invent details.  Use simple language."
)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class GrievanceTrackerService:
    """Helps citizens draft, file, and track grievances.

    Parameters
    ----------
    llm:
        An LLM service instance with a ``generate(prompt, system_instruction)``
        async method that returns a string.
    """

    __slots__ = ("_llm",)

    def __init__(self, llm: LLMService) -> None:
        self._llm = llm

    # -- portal resolution ---------------------------------------------------

    @staticmethod
    def _normalise_state(state: str) -> str:
        """Normalise a state name to a canonical key."""
        key = state.strip().lower().replace("-", " ").replace("_", " ")
        return _STATE_ALIASES.get(key, key.replace(" ", "_"))

    @staticmethod
    def _resolve_portal(
        grievance_type: str,
        department: str,
        state: str,
    ) -> _PortalEntry:
        """Pick the most appropriate portal for the given parameters.

        Priority order:
            1. Department-specific portal (if department matches).
            2. State-specific portal (if state matches).
            3. Corruption -> CVC portal.
            4. Discrimination -> NHRC portal.
            5. Fallback -> CPGRAMS.
        """
        dept_key = department.strip().lower().replace(" ", "_")
        if dept_key and dept_key in _DEPARTMENT_PORTALS:
            return _DEPARTMENT_PORTALS[dept_key]

        state_key = GrievanceTrackerService._normalise_state(state)
        if state_key and state_key in _STATE_PORTALS:
            return _STATE_PORTALS[state_key]

        if grievance_type == GrievanceType.CORRUPTION:
            return _CENTRAL_PORTALS["anti_corruption"]

        if grievance_type == GrievanceType.DISCRIMINATION:
            return _CENTRAL_PORTALS["nhrc"]

        return _CENTRAL_PORTALS["cpgrams"]

    # -- main API ------------------------------------------------------------

    async def create_grievance(self, request: GrievanceRequest) -> GrievanceDraft:
        """Draft a formal grievance and recommend the filing portal.

        Parameters
        ----------
        request:
            A ``GrievanceRequest`` with the citizen's complaint details.

        Returns
        -------
        GrievanceDraft
            A draft with formatted complaint, recommended portal, filing
            steps, expected timeline, and escalation information.
        """
        grievance_id = f"GRV-{uuid4().hex[:12].upper()}"

        log = logger.bind(
            grievance_id=grievance_id,
            grievance_type=request.grievance_type,
            department=request.department,
            state=request.state,
        )
        log.info("grievance.create.started")

        # Resolve the best portal
        portal = self._resolve_portal(
            request.grievance_type,
            request.department,
            request.state,
        )

        # Use LLM to format the complaint
        prompt = _COMPLAINT_FORMAT_PROMPT.format(
            complainant_name=request.complainant_name,
            grievance_type=request.grievance_type,
            department=request.department or "Not specified",
            state=request.state or "Not specified",
            district=request.district or "Not specified",
            description=request.description,
            portal_name=portal.name,
        )

        try:
            formatted_complaint = await self._llm.generate(
                prompt,
                system_instruction=_COMPLAINT_SYSTEM_INSTRUCTION,
            )
        except Exception:
            log.exception("grievance.llm_format.failed")
            # Fallback: use the raw description with a basic template
            formatted_complaint = (
                f"Subject: Grievance - {request.grievance_type.replace('_', ' ').title()}\n\n"
                f"Respected Sir/Madam,\n\n"
                f"I, {request.complainant_name}, wish to bring the following "
                f"matter to your attention.\n\n"
                f"{request.description}\n\n"
                f"I kindly request you to look into this matter and take "
                f"appropriate action at the earliest.\n\n"
                f"Thanking you,\n"
                f"{request.complainant_name}"
            )

        # Determine expected timeline and escalation info
        gtype = request.grievance_type.strip().lower()
        escalation = _ESCALATION_PATHS.get(gtype, _ESCALATION_PATHS["other"])

        expected_timeline = self._compute_expected_timeline(gtype)
        escalation_info = self._build_escalation_summary(escalation)

        draft = GrievanceDraft(
            grievance_id=grievance_id,
            formatted_complaint=formatted_complaint.strip(),
            recommended_portal=portal.name,
            portal_url=portal.url,
            filing_steps=list(portal.filing_steps),
            expected_timeline=expected_timeline,
            escalation_info=escalation_info,
        )

        log.info(
            "grievance.create.completed",
            portal=portal.name,
            portal_url=portal.url,
        )
        return draft

    # -- escalation & portal info --------------------------------------------

    def get_escalation_path(
        self,
        grievance_type: str,
        authority_level: str = "",
    ) -> EscalationPath:
        """Return the escalation hierarchy for a given grievance type.

        Parameters
        ----------
        grievance_type:
            One of the supported grievance types.
        authority_level:
            Optional current authority level.  If provided, the returned
            path starts from the *next* level above.

        Returns
        -------
        EscalationPath
            The escalation levels, timelines, and tips.
        """
        gtype = grievance_type.strip().lower()
        path_data = _ESCALATION_PATHS.get(gtype, _ESCALATION_PATHS["other"])

        levels: list[str] = list(path_data["levels"])
        timelines: list[str] = list(path_data["timelines"])
        tips: list[str] = list(path_data["tips"])

        # If an authority level is specified, trim everything up to and
        # including that level so the citizen sees only what comes NEXT.
        if authority_level:
            normalised = authority_level.strip().lower()
            cut_index = -1
            for i, lvl in enumerate(levels):
                if normalised in lvl.lower():
                    cut_index = i
                    break
            if cut_index >= 0 and cut_index + 1 < len(levels):
                levels = levels[cut_index + 1 :]
                timelines = timelines[cut_index + 1 :] if cut_index + 1 < len(timelines) else timelines[-1:]

        logger.info(
            "grievance.escalation_path.retrieved",
            grievance_type=gtype,
            authority_level=authority_level,
            levels_returned=len(levels),
        )

        return EscalationPath(
            levels=levels,
            timelines=timelines,
            tips=tips,
        )

    def get_portal_info(
        self,
        grievance_type: str,
        state: str = "",
    ) -> PortalInfo:
        """Return portal information for a given grievance type and state.

        Parameters
        ----------
        grievance_type:
            One of the supported grievance types.
        state:
            Optional state name/abbreviation.

        Returns
        -------
        PortalInfo
            Detailed information about the recommended portal.
        """
        portal = self._resolve_portal(
            grievance_type=grievance_type,
            department="",
            state=state,
        )

        logger.info(
            "grievance.portal_info.retrieved",
            grievance_type=grievance_type,
            state=state,
            portal=portal.name,
        )

        return PortalInfo(
            portal_name=portal.name,
            url=portal.url,
            helpline=portal.helpline,
            filing_steps=list(portal.filing_steps),
            supported_types=list(portal.supported_types),
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _compute_expected_timeline(grievance_type: str) -> str:
        """Return a human-readable expected resolution timeline."""
        timelines: dict[str, str] = {
            "public_service": (
                "Initial acknowledgement within 3 working days. "
                "First response within 30 days. "
                "Resolution expected within 30-60 days depending on complexity. "
                "Right to Service Act (where applicable) mandates resolution "
                "within the notified time limit."
            ),
            "corruption": (
                "CVC acknowledges within 15 days. "
                "Investigation may take 3-6 months. "
                "ACB/CBI cases can take 6 months to several years. "
                "Lokpal complaints are typically heard within 60 days of receipt."
            ),
            "delay": (
                "Under Right to Service Acts, departments must respond within "
                "the notified time (typically 7-30 days). "
                "Appellate authority must decide within 30 days. "
                "CPGRAMS grievances are monitored monthly."
            ),
            "discrimination": (
                "National Commissions typically respond within 30 days. "
                "NHRC inquiries take 3-6 months. "
                "Court cases under SC/ST Act have priority and are expected "
                "to be heard within 2 months of filing."
            ),
            "other": (
                "Initial acknowledgement within 3-7 working days. "
                "First response within 30 days. "
                "Resolution depends on the nature of the grievance and the "
                "department involved; typically 30-90 days."
            ),
        }
        return timelines.get(grievance_type, timelines["other"])

    @staticmethod
    def _build_escalation_summary(escalation: dict[str, list[str]]) -> str:
        """Build a concise escalation summary string from path data."""
        levels = escalation.get("levels", [])
        if not levels:
            return "No escalation path available. Approach the District Magistrate for guidance."

        summary_parts = [
            f"Escalation path ({len(levels)} levels):",
        ]
        for i, level in enumerate(levels, 1):
            summary_parts.append(f"  {i}. {level}")

        summary_parts.append("")
        summary_parts.append(
            "Tip: Always get a written acknowledgement at each level before escalating."
        )

        return "\n".join(summary_parts)
