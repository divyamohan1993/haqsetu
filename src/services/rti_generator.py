"""RTI (Right to Information) application generator for HaqSetu.

UNIQUE FEATURE: No other platform in India helps citizens -- especially
illiterate or semi-literate rural users -- draft legally-valid RTI
applications through a voice-first conversational flow.  This service:

1. **Generates complete RTI applications** in the proper format mandated
   by Section 6 of the RTI Act, 2005.
2. **Auto-identifies the correct public authority** (ministry, department,
   or public body) based on a plain-language description of the problem.
3. **Auto-generates pointed RTI questions** from a problem description,
   ensuring the questions are specific, answerable, and maximise the
   chance of a meaningful disclosure.
4. **Provides fee schedules** for central and all state governments,
   including BPL exemptions under Section 7(5).
5. **Provides step-by-step filing instructions** for both online
   (rtionline.gov.in) and offline (postal/in-person) submission.
6. **Translates applications** into the user's preferred language while
   maintaining legal validity.

Legal references:
    * Right to Information Act, 2005 -- Sections 2(f), 3, 6, 7, 8, 19
    * RTI Rules, 2012 -- Rules 3, 4
    * CIC decisions on fee structure and format

Fee data sources:
    * DoPT (Department of Personnel & Training) notifications
    * State Information Commission circulars
    * RTI Rules of respective state governments

Architecture:
    * Uses Gemini LLM for intelligent question generation and authority
      identification.
    * Uses the Translation service for multilingual application output.
    * All data structures use ``dataclasses`` with ``__slots__`` for
      memory efficiency at scale.
    * Deterministic fee/authority lookups use dictionaries for O(1)
      performance -- LLM is only invoked for the creative/analytical
      parts (question generation, authority identification).
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from src.services.llm import LLMService
    from src.services.translation import TranslationService

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Legal disclaimer
# ---------------------------------------------------------------------------

RTI_LEGAL_DISCLAIMER: Final[str] = (
    "DISCLAIMER: This RTI application has been drafted by an AI system for "
    "informational and educational purposes only. While it follows the format "
    "prescribed under Section 6 of the RTI Act, 2005, the applicant is solely "
    "responsible for verifying the accuracy of all details before submission. "
    "This is NOT legal advice. For guidance on complex RTI matters, appeals "
    "under Section 19, or complaints, please contact your nearest District "
    "Legal Services Authority (DLSA) at helpline 1516 or visit the Central "
    "Information Commission (CIC) website at https://cic.gov.in."
)

RTI_LEGAL_DISCLAIMER_HI: Final[str] = (
    "अस्वीकरण: यह RTI आवेदन एक AI प्रणाली द्वारा केवल सूचनात्मक और शैक्षिक "
    "उद्देश्यों के लिए तैयार किया गया है। हालाँकि यह RTI अधिनियम, 2005 की धारा 6 "
    "के तहत निर्धारित प्रारूप का पालन करता है, आवेदक जमा करने से पहले सभी विवरणों "
    "की सटीकता सत्यापित करने के लिए स्वयं जिम्मेदार है। यह कानूनी सलाह नहीं है।"
)

# ---------------------------------------------------------------------------
# RTI Act 2005 -- Key section references
# ---------------------------------------------------------------------------

RTI_ACT_SECTIONS: Final[dict[str, str]] = {
    "section_2f": (
        "Section 2(f) -- Definition of 'information': any material in any "
        "form, including records, documents, memos, e-mails, opinions, "
        "advices, press releases, circulars, orders, logbooks, contracts, "
        "reports, papers, samples, models, data material held in any "
        "electronic form."
    ),
    "section_3": (
        "Section 3 -- Right to information: Subject to the provisions of "
        "this Act, all citizens shall have the right to information."
    ),
    "section_6": (
        "Section 6 -- Request for obtaining information: A person who "
        "desires to obtain any information shall make a request in writing "
        "or through electronic means in English or Hindi or in the official "
        "language of the area, to the CPIO or SPIO, specifying the "
        "particulars of the information sought."
    ),
    "section_7": (
        "Section 7 -- Disposal of request: The CPIO/SPIO shall supply the "
        "information within 30 days of the receipt of the request. If the "
        "information concerns the life or liberty of a person, it shall be "
        "provided within 48 hours."
    ),
    "section_7_5": (
        "Section 7(5) -- BPL exemption: No fee shall be charged from "
        "persons who are below the poverty line as may be determined by "
        "the appropriate government."
    ),
    "section_8": (
        "Section 8 -- Exemptions from disclosure: Certain categories of "
        "information are exempt, including information affecting sovereignty "
        "and integrity of India, security, scientific or economic interests, "
        "Cabinet papers, personal information with no public interest, etc."
    ),
    "section_19": (
        "Section 19 -- Appeal: Any person who does not receive a decision "
        "within the specified time or is aggrieved by a decision, may prefer "
        "an appeal to the officer senior in rank to the CPIO within 30 days. "
        "A second appeal lies to the Central/State Information Commission "
        "within 90 days."
    ),
    "section_20": (
        "Section 20 -- Penalties: If the CPIO has, without reasonable cause, "
        "refused to receive an application or has not furnished information "
        "within the specified time, the Information Commission shall impose "
        "a penalty of Rs. 250 per day up to a maximum of Rs. 25,000."
    ),
}

# ---------------------------------------------------------------------------
# Fee schedules -- Central and State governments
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """RTI fee schedule for a specific authority level/state."""

    amount: str
    payment_modes: list[str]
    bpl_exempt: bool
    state_specific_notes: str


_CENTRAL_FEE: Final[FeeSchedule] = FeeSchedule(
    amount="Rs. 10 (application fee) + Rs. 2 per page for additional information",
    payment_modes=[
        "Indian Postal Order (IPO)",
        "Demand Draft (DD)",
        "Banker's Cheque",
        "Court Fee Stamp",
        "Online payment via rtionline.gov.in",
        "Cash (when submitting in person)",
    ],
    bpl_exempt=True,
    state_specific_notes=(
        "Central government RTI fee is governed by RTI Rules, 2012 "
        "(Rule 4). The application fee is Rs. 10. Additional fees: "
        "Rs. 2 per page (A4/A3), actual cost for larger paper, "
        "Rs. 50 per diskette/floppy, actual cost or Rs. 2 per page "
        "for printed material. Inspection of records: first hour free, "
        "Rs. 5 for each subsequent hour."
    ),
)

# State-level fee schedules (selected major states)
_STATE_FEES: Final[dict[str, FeeSchedule]] = {
    "maharashtra": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Court Fee Stamp",
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Maharashtra RTI Rules, 2005. Fee: Rs. 10. Court fee stamp "
            "is the most commonly accepted mode. Applications can be "
            "filed at the Mantralaya or respective department offices."
        ),
    ),
    "uttar_pradesh": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Treasury Challan",
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Court Fee Stamp",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Uttar Pradesh RTI Rules, 2015. Fee: Rs. 10 via treasury "
            "challan, IPO, DD, or court fee stamp. UP State Information "
            "Commission: https://upic.up.nic.in."
        ),
    ),
    "rajasthan": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Court Fee Stamp",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Rajasthan RTI Rules, 2005. Fee: Rs. 10. Applications may "
            "be filed at the PIO of the concerned department. Rajasthan "
            "State Information Commission: https://ric.rajasthan.gov.in."
        ),
    ),
    "tamil_nadu": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Court Fee Stamp",
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Tamil Nadu RTI Rules, 2005. Fee: Rs. 10. The Tamil Nadu "
            "Information Commission accepts complaints via "
            "https://tnsic.gov.in. Applications may be submitted in "
            "Tamil or English."
        ),
    ),
    "karnataka": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Court Fee Stamp",
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Cash",
            "Karnataka One portal online payment",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Karnataka RTI Rules, 2005. Fee: Rs. 10. Applications may "
            "be filed online via Karnataka One portal or in person at "
            "the concerned PIO's office. Karnataka Information "
            "Commission: https://kic.karnataka.gov.in."
        ),
    ),
    "kerala": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Court Fee Stamp",
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Kerala RTI Rules, 2006. Fee: Rs. 10. Kerala State "
            "Information Commission: https://sic.kerala.gov.in. "
            "Applications may be filed in Malayalam or English."
        ),
    ),
    "madhya_pradesh": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Court Fee Stamp",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Madhya Pradesh RTI Rules, 2005. Fee: Rs. 10. MP State "
            "Information Commission: https://mpic.mp.gov.in. "
            "Applications may be filed in Hindi or English."
        ),
    ),
    "west_bengal": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Court Fee Stamp",
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "West Bengal RTI Rules, 2006. Fee: Rs. 10. West Bengal "
            "Information Commission: https://wbic.gov.in. Applications "
            "may be filed in Bengali or English."
        ),
    ),
    "bihar": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Indian Postal Order (IPO)",
            "Court Fee Stamp",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Bihar RTI Rules, 2005. Fee: Rs. 10. Bihar State "
            "Information Commission: https://bsic.bihar.gov.in. "
            "Applications in Hindi are preferred."
        ),
    ),
    "gujarat": FeeSchedule(
        amount="Rs. 20 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Court Fee Stamp",
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Gujarat RTI Rules, 2005. Fee: Rs. 20 (higher than central "
            "government rate). Gujarat Information Commission: "
            "https://gic.gujarat.gov.in. Applications may be filed "
            "in Gujarati or English."
        ),
    ),
    "delhi": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Court Fee Stamp",
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Delhi RTI Rules, 2005. Fee: Rs. 10. Delhi Information "
            "Commission: https://ic.delhi.gov.in. Applications may "
            "also be filed online via the Delhi government portal."
        ),
    ),
    "punjab": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Indian Postal Order (IPO)",
            "Court Fee Stamp",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Punjab RTI Rules, 2005. Fee: Rs. 10. Punjab State "
            "Information Commission: https://infocommpunjab.com. "
            "Applications may be filed in Punjabi, Hindi, or English."
        ),
    ),
    "haryana": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Court Fee Stamp",
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Haryana RTI Rules, 2005. Fee: Rs. 10. Haryana State "
            "Information Commission: https://haryanainfocm.gov.in."
        ),
    ),
    "telangana": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Court Fee Stamp",
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Telangana RTI Rules, 2005. Fee: Rs. 10. Telangana State "
            "Information Commission: https://tsic.telangana.gov.in."
        ),
    ),
    "andhra_pradesh": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Court Fee Stamp",
            "Indian Postal Order (IPO)",
            "Demand Draft (DD)",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Andhra Pradesh RTI Rules, 2005. Fee: Rs. 10. AP State "
            "Information Commission: https://apic.ap.gov.in."
        ),
    ),
    "odisha": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Indian Postal Order (IPO)",
            "Treasury Challan",
            "Court Fee Stamp",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Odisha RTI Rules, 2005. Fee: Rs. 10. Odisha Information "
            "Commission: https://orissasic.nic.in."
        ),
    ),
    "assam": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Indian Postal Order (IPO)",
            "Court Fee Stamp",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Assam RTI Rules, 2005. Fee: Rs. 10. Assam State "
            "Information Commission: https://asic.assam.gov.in."
        ),
    ),
    "jharkhand": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Indian Postal Order (IPO)",
            "Court Fee Stamp",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Jharkhand RTI Rules, 2005. Fee: Rs. 10. Jharkhand "
            "Information Commission: https://jicsic.jharkhand.gov.in."
        ),
    ),
    "chhattisgarh": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Indian Postal Order (IPO)",
            "Court Fee Stamp",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Chhattisgarh RTI Rules, 2005. Fee: Rs. 10. CG Information "
            "Commission: https://cgic.cg.gov.in."
        ),
    ),
    "uttarakhand": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Indian Postal Order (IPO)",
            "Treasury Challan",
            "Court Fee Stamp",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Uttarakhand RTI Rules, 2005. Fee: Rs. 10. Uttarakhand "
            "Information Commission: https://ukinformationcommission.nic.in."
        ),
    ),
    "himachal_pradesh": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Indian Postal Order (IPO)",
            "Court Fee Stamp",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Himachal Pradesh RTI Rules, 2006. Fee: Rs. 10. HP State "
            "Information Commission: https://hpsic.hp.gov.in."
        ),
    ),
    "goa": FeeSchedule(
        amount="Rs. 10 (application fee) + Rs. 2 per page",
        payment_modes=[
            "Court Fee Stamp",
            "Indian Postal Order (IPO)",
            "Cash",
        ],
        bpl_exempt=True,
        state_specific_notes=(
            "Goa RTI Rules, 2005. Fee: Rs. 10. Goa Information "
            "Commission: https://goasic.gov.in."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Authority mapping -- common public authorities and their departments
# ---------------------------------------------------------------------------

_AUTHORITY_KEYWORDS: Final[dict[str, str]] = {
    # --- Central government ministries/departments ---
    "pension": "Ministry of Personnel, Public Grievances and Pensions",
    "retirement": "Ministry of Personnel, Public Grievances and Pensions",
    "government job": "Ministry of Personnel, Public Grievances and Pensions",
    "sarkari naukri": "Ministry of Personnel, Public Grievances and Pensions",
    "ration": "Department of Food and Public Distribution, Ministry of Consumer Affairs",
    "ration card": "Department of Food and Public Distribution, Ministry of Consumer Affairs",
    "pds": "Department of Food and Public Distribution, Ministry of Consumer Affairs",
    "food supply": "Department of Food and Public Distribution, Ministry of Consumer Affairs",
    "road": "Ministry of Road Transport and Highways",
    "highway": "Ministry of Road Transport and Highways",
    "national highway": "Ministry of Road Transport and Highways",
    "sadak": "Ministry of Road Transport and Highways",
    "railway": "Ministry of Railways",
    "train": "Ministry of Railways",
    "rail": "Ministry of Railways",
    "income tax": "Central Board of Direct Taxes (CBDT), Ministry of Finance",
    "tax": "Central Board of Direct Taxes (CBDT), Ministry of Finance",
    "gst": "Central Board of Indirect Taxes and Customs (CBIC), Ministry of Finance",
    "customs": "Central Board of Indirect Taxes and Customs (CBIC), Ministry of Finance",
    "bank": "Reserve Bank of India (RBI) / Department of Financial Services",
    "loan": "Reserve Bank of India (RBI) / Department of Financial Services",
    "mudra": "Reserve Bank of India (RBI) / Department of Financial Services",
    "passport": "Ministry of External Affairs",
    "visa": "Ministry of External Affairs",
    "defence": "Ministry of Defence",
    "military": "Ministry of Defence",
    "army": "Ministry of Defence",
    "navy": "Ministry of Defence",
    "air force": "Ministry of Defence",
    "education": "Ministry of Education",
    "school": "Ministry of Education",
    "university": "Ministry of Education / University Grants Commission (UGC)",
    "college": "Ministry of Education / University Grants Commission (UGC)",
    "ugc": "University Grants Commission (UGC)",
    "scholarship": "Ministry of Education / Ministry of Social Justice and Empowerment",
    "health": "Ministry of Health and Family Welfare",
    "hospital": "Ministry of Health and Family Welfare",
    "ayushman": "National Health Authority, Ministry of Health and Family Welfare",
    "pmjay": "National Health Authority, Ministry of Health and Family Welfare",
    "medicine": "Ministry of Health and Family Welfare",
    "agriculture": "Ministry of Agriculture and Farmers Welfare",
    "farming": "Ministry of Agriculture and Farmers Welfare",
    "kisan": "Ministry of Agriculture and Farmers Welfare",
    "pm-kisan": "Ministry of Agriculture and Farmers Welfare",
    "crop insurance": "Ministry of Agriculture and Farmers Welfare",
    "fasal bima": "Ministry of Agriculture and Farmers Welfare",
    "mandi": "Ministry of Agriculture and Farmers Welfare",
    "land": "Revenue Department (State Government)",
    "land record": "Revenue Department (State Government)",
    "bhulekh": "Revenue Department (State Government)",
    "jameen": "Revenue Department (State Government)",
    "property": "Revenue Department (State Government)",
    "water": "Ministry of Jal Shakti",
    "drinking water": "Ministry of Jal Shakti",
    "jal jeevan": "Ministry of Jal Shakti",
    "sanitation": "Ministry of Jal Shakti",
    "swachh bharat": "Ministry of Jal Shakti",
    "toilet": "Ministry of Jal Shakti",
    "housing": "Ministry of Housing and Urban Affairs",
    "pm awas": "Ministry of Housing and Urban Affairs",
    "pradhan mantri awas": "Ministry of Housing and Urban Affairs",
    "electricity": "Ministry of Power",
    "power": "Ministry of Power",
    "bijli": "Ministry of Power",
    "saubhagya": "Ministry of Power",
    "gas": "Ministry of Petroleum and Natural Gas",
    "lpg": "Ministry of Petroleum and Natural Gas",
    "ujjwala": "Ministry of Petroleum and Natural Gas",
    "cylinder": "Ministry of Petroleum and Natural Gas",
    "telecom": "Department of Telecommunications, Ministry of Communications",
    "mobile": "Department of Telecommunications, Ministry of Communications",
    "internet": "Department of Telecommunications, Ministry of Communications",
    "post office": "Department of Posts, Ministry of Communications",
    "environment": "Ministry of Environment, Forest and Climate Change",
    "forest": "Ministry of Environment, Forest and Climate Change",
    "pollution": "Central Pollution Control Board / State Pollution Control Board",
    "labour": "Ministry of Labour and Employment",
    "wages": "Ministry of Labour and Employment",
    "epf": "Employees' Provident Fund Organisation (EPFO)",
    "pf": "Employees' Provident Fund Organisation (EPFO)",
    "provident fund": "Employees' Provident Fund Organisation (EPFO)",
    "esic": "Employees' State Insurance Corporation (ESIC)",
    "esi": "Employees' State Insurance Corporation (ESIC)",
    "tribal": "Ministry of Tribal Affairs",
    "adivasi": "Ministry of Tribal Affairs",
    "sc st": "Ministry of Social Justice and Empowerment",
    "scheduled caste": "Ministry of Social Justice and Empowerment",
    "obc": "Ministry of Social Justice and Empowerment",
    "disabled": "Department of Empowerment of Persons with Disabilities",
    "disability": "Department of Empowerment of Persons with Disabilities",
    "divyang": "Department of Empowerment of Persons with Disabilities",
    "women": "Ministry of Women and Child Development",
    "child": "Ministry of Women and Child Development",
    "anganwadi": "Ministry of Women and Child Development",
    "icds": "Ministry of Women and Child Development",
    "rural development": "Ministry of Rural Development",
    "mgnrega": "Ministry of Rural Development",
    "nrega": "Ministry of Rural Development",
    "manrega": "Ministry of Rural Development",
    "gram panchayat": "Ministry of Panchayati Raj / District Administration",
    "panchayat": "Ministry of Panchayati Raj / District Administration",
    "aadhar": "Unique Identification Authority of India (UIDAI)",
    "aadhaar": "Unique Identification Authority of India (UIDAI)",
    "uidai": "Unique Identification Authority of India (UIDAI)",
    "election": "Election Commission of India",
    "voter id": "Election Commission of India",
    "voter": "Election Commission of India",
    "police": "Home Department (State Government) / Ministry of Home Affairs",
    "fir": "Home Department (State Government) / Ministry of Home Affairs",
    "crime": "Home Department (State Government) / Ministry of Home Affairs",
    "district collector": "District Administration (State Government)",
    "dm": "District Administration (State Government)",
    "tehsildar": "Revenue Department (State Government)",
    "municipality": "Urban Local Body / Municipal Corporation",
    "nagar palika": "Urban Local Body / Municipal Corporation",
    "nagar nigam": "Urban Local Body / Municipal Corporation",
    "corporation": "Urban Local Body / Municipal Corporation",
}


# ---------------------------------------------------------------------------
# Filing instructions
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FilingInstructions:
    """Step-by-step filing instructions for an RTI application."""

    online_url: str
    steps: list[str]
    documents_needed: list[str]


_CENTRAL_FILING: Final[FilingInstructions] = FilingInstructions(
    online_url="https://rtionline.gov.in",
    steps=[
        "Visit https://rtionline.gov.in and click 'Submit Request'.",
        "Select the Ministry/Department/Public Authority from the dropdown.",
        "Fill in your name, address, email, phone number, and citizenship.",
        "Type your RTI application text in the 'Text of Application' box.",
        "If you are BPL, select the BPL checkbox and upload your BPL certificate.",
        "Pay the fee of Rs. 10 online via internet banking, credit/debit card, or UPI.",
        "Submit and note down the Registration Number for tracking.",
        "You can track the status at https://rtionline.gov.in/request/status.php.",
        "If no reply within 30 days, file a First Appeal on the same portal.",
    ],
    documents_needed=[
        "Proof of citizenship (Aadhaar/Voter ID/Passport -- for reference only, not mandatory to attach)",
        "BPL certificate (only if claiming fee exemption under Section 7(5))",
        "Any supporting documents relevant to your query (optional)",
    ],
)

_STATE_FILING: Final[FilingInstructions] = FilingInstructions(
    online_url="Check respective State Information Commission website",
    steps=[
        "Write the RTI application on plain paper in the prescribed format.",
        "Address it to the Public Information Officer (PIO) of the concerned department.",
        "Attach the fee via Indian Postal Order (IPO), Court Fee Stamp, or DD.",
        "If you are BPL, attach a copy of your BPL certificate and mark 'BPL -- Fee Exempt'.",
        "Send via Registered Post / Speed Post to the PIO's address (keep the receipt).",
        "Alternatively, submit in person at the PIO's office and obtain a receipt.",
        "Keep a photocopy of the entire application and fee proof for your records.",
        "Note the date of submission -- the PIO has 30 days to respond.",
        "If no reply within 30 days, file a First Appeal to the First Appellate Authority.",
        "If the First Appeal is also not resolved, file a Second Appeal/Complaint with the State Information Commission.",
    ],
    documents_needed=[
        "RTI application on plain paper (no stamp paper needed)",
        "Fee payment proof: IPO / Court Fee Stamp / DD / Treasury Challan",
        "BPL certificate (only if claiming fee exemption)",
        "Self-addressed envelope (if requesting information by post)",
        "Photocopy of the application for your records",
    ],
)

_OFFLINE_CENTRAL_FILING: Final[FilingInstructions] = FilingInstructions(
    online_url="https://rtionline.gov.in (online alternative available)",
    steps=[
        "Write the RTI application on plain A4 paper in the prescribed format.",
        "Address it to: The Central Public Information Officer (CPIO), "
        "[Name of Ministry/Department], [Full Address].",
        "Purchase an Indian Postal Order (IPO) of Rs. 10 from the Post Office, "
        "payable to the Accounts Officer of the concerned Ministry/Department.",
        "Attach the IPO to the application.",
        "If you are BPL, write 'BPL -- Fee Exempt under Section 7(5)' and "
        "attach a photocopy of your BPL certificate.",
        "Send via Registered Post or Speed Post. Keep the postal receipt "
        "as proof of submission.",
        "Alternatively, submit in person at the concerned department's "
        "counter and obtain a dated receipt.",
        "The CPIO must reply within 30 days (48 hours if it concerns "
        "life/liberty of a person).",
        "If no response or unsatisfactory response, file a First Appeal "
        "within 30 days to the First Appellate Authority.",
        "Second Appeal to the Central Information Commission (CIC) within "
        "90 days at: CIC Bhawan, Baba Gangnath Marg, Munirka, New Delhi - 110067.",
    ],
    documents_needed=[
        "RTI application on plain paper (no stamp paper required)",
        "Indian Postal Order (IPO) of Rs. 10 payable to the Accounts Officer",
        "BPL certificate photocopy (if claiming fee exemption)",
        "Postal receipt (if sending by post)",
        "Photocopy of the entire application for your records",
    ],
)


# ---------------------------------------------------------------------------
# Data classes for RTI request and response
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RTIRequest:
    """Incoming RTI application request from the user.

    Attributes:
        applicant_name: Full name of the applicant.
        address: Complete postal address of the applicant.
        subject: Subject/topic of the RTI application.
        questions: List of specific information items being sought.
        public_authority: Name of the public authority to which the
            application is addressed.
        authority_address: Postal address of the public authority
            (optional -- can be left empty and filled later).
        bpl_status: Whether the applicant holds a BPL (Below Poverty
            Line) certificate, which exempts them from fees.
    """

    applicant_name: str
    address: str
    subject: str
    questions: list[str]
    public_authority: str
    authority_address: str = ""
    bpl_status: bool = False


@dataclass(slots=True)
class RTIDraft:
    """Generated RTI application draft.

    Attributes:
        application_text: Complete RTI application text ready for
            submission.
        subject: Subject of the application.
        public_authority: Name of the target public authority.
        fee_amount: Fee amount as a human-readable string.
        filing_method: Recommended filing method.
        reference_sections: RTI Act sections referenced in this
            application.
        language: Language code of the generated application (ISO 639-1).
        generated_at: Timestamp when the draft was generated.
    """

    application_text: str
    subject: str
    public_authority: str
    fee_amount: str
    filing_method: str
    reference_sections: list[str]
    language: str
    generated_at: datetime


# ---------------------------------------------------------------------------
# RTI application template
# ---------------------------------------------------------------------------

_RTI_APPLICATION_TEMPLATE: Final[str] = textwrap.dedent("""\
    To,
    The Central/State Public Information Officer,
    {public_authority},
    {authority_address}

    Subject: Application under the Right to Information Act, 2005 -- {subject}

    Date: {date}

    Respected Sir/Madam,

    I, {applicant_name}, a citizen of India, hereby submit this application under
    Section 6(1) of the Right to Information Act, 2005, to seek the following
    information:

    {questions_section}

    My details are as follows:
    Name: {applicant_name}
    Address: {address}
    {bpl_line}

    I request that the above information be provided to me in the form of
    photocopies / printed material / electronic format (as applicable) at
    my above-mentioned address.

    I am enclosing herewith an application fee of {fee_amount} via
    {payment_mode} as prescribed under the RTI Act, 2005.

    I state that the information sought does not fall within the restrictions
    set out in Section 8 of the RTI Act, 2005, and to the best of my
    knowledge pertains to your office.

    If the requested information or any part thereof concerns another public
    authority, I request that the relevant portion of this application be
    transferred to that authority under Section 6(3) of the RTI Act, 2005,
    and I be informed accordingly.

    I expect a response within 30 days as mandated under Section 7(1) of the
    RTI Act, 2005. If the information concerns the life or liberty of a person,
    I request it be provided within 48 hours as per Section 7(1) proviso.

    Thanking you,

    Yours faithfully,
    {applicant_name}
    {address}

    ---
    {disclaimer}
""")


# ---------------------------------------------------------------------------
# RTI Generator Service
# ---------------------------------------------------------------------------

class RTIGeneratorService:
    """Service for generating RTI applications, identifying authorities,
    and providing filing guidance.

    This service combines deterministic lookups (fees, filing instructions,
    authority mapping) with LLM-powered intelligence (question generation,
    authority identification from free-text descriptions) to help citizens
    file effective RTI applications.

    Parameters:
        llm: LLM service instance with a ``generate(prompt, system_instruction)``
            method.
        translation: Translation service instance with a
            ``translate(text, source_lang, target_lang)`` method.
    """

    __slots__ = ("_llm", "_translation")

    def __init__(
        self,
        llm: LLMService,
        translation: TranslationService,
    ) -> None:
        self._llm = llm
        self._translation = translation
        logger.info("rti_generator_service_initialized")

    # -----------------------------------------------------------------
    # Public methods
    # -----------------------------------------------------------------

    async def generate_rti_draft(
        self,
        request: RTIRequest,
        target_language: str = "en",
    ) -> RTIDraft:
        """Generate a complete RTI application draft.

        Produces a legally-formatted RTI application from the provided
        request parameters.  Optionally translates the application into
        the target language.

        Args:
            request: The RTI request containing applicant details,
                questions, and target authority.
            target_language: ISO 639-1 language code for the output.
                Defaults to ``"en"`` (English).

        Returns:
            An ``RTIDraft`` containing the complete application text and
            metadata.
        """
        logger.info(
            "generating_rti_draft",
            applicant=request.applicant_name,
            authority=request.public_authority,
            num_questions=len(request.questions),
            bpl=request.bpl_status,
            target_language=target_language,
        )

        # Determine authority level for fee lookup
        authority_level = self._classify_authority_level(request.public_authority)

        # Get fee information
        fee_info = self.get_fee_info(authority_level, state="")
        fee_amount = "Nil (BPL exempt under Section 7(5))" if request.bpl_status else fee_info.amount

        # Format the questions
        questions_section = self._format_questions(request.questions)

        # BPL line
        bpl_line = (
            "BPL Status: I am a Below Poverty Line (BPL) certificate holder "
            "and am exempt from payment of fees under Section 7(5) of the "
            "RTI Act, 2005. A copy of my BPL certificate is enclosed."
            if request.bpl_status
            else ""
        )

        # Payment mode
        payment_mode = "N/A (BPL exempt)" if request.bpl_status else fee_info.payment_modes[0]

        # Authority address
        authority_address = request.authority_address or "[Address of the Public Authority]"

        # Generate application text
        application_text = _RTI_APPLICATION_TEMPLATE.format(
            public_authority=request.public_authority,
            authority_address=authority_address,
            subject=request.subject,
            date=datetime.now(UTC).strftime("%d/%m/%Y"),
            applicant_name=request.applicant_name,
            address=request.address,
            questions_section=questions_section,
            bpl_line=bpl_line,
            fee_amount=fee_amount,
            payment_mode=payment_mode,
            disclaimer=RTI_LEGAL_DISCLAIMER,
        )

        # Determine reference sections used
        reference_sections = [
            RTI_ACT_SECTIONS["section_3"],
            RTI_ACT_SECTIONS["section_6"],
            RTI_ACT_SECTIONS["section_7"],
        ]
        if request.bpl_status:
            reference_sections.append(RTI_ACT_SECTIONS["section_7_5"])
        reference_sections.append(RTI_ACT_SECTIONS["section_8"])
        reference_sections.append(RTI_ACT_SECTIONS["section_19"])

        # Determine filing method
        filing_method = (
            "Online via https://rtionline.gov.in"
            if authority_level == "central"
            else "By post (Registered Post/Speed Post) or in person at PIO's office"
        )

        # Optionally refine the application with LLM
        try:
            refined_text = await self._refine_with_llm(application_text, request)
            if refined_text and len(refined_text) > 100:
                application_text = refined_text
        except Exception:
            logger.warning(
                "llm_refinement_failed_using_template",
                applicant=request.applicant_name,
                exc_info=True,
            )

        # Translate if needed
        output_language = "en"
        if target_language and target_language != "en":
            try:
                application_text = await self._translation.translate(
                    application_text,
                    source_lang="en",
                    target_lang=target_language,
                )
                output_language = target_language
                logger.info(
                    "rti_draft_translated",
                    target_language=target_language,
                )
            except Exception:
                logger.warning(
                    "translation_failed_returning_english",
                    target_language=target_language,
                    exc_info=True,
                )

        draft = RTIDraft(
            application_text=application_text,
            subject=request.subject,
            public_authority=request.public_authority,
            fee_amount=fee_amount,
            filing_method=filing_method,
            reference_sections=reference_sections,
            language=output_language,
            generated_at=datetime.now(UTC),
        )

        logger.info(
            "rti_draft_generated",
            subject=draft.subject,
            authority=draft.public_authority,
            language=draft.language,
        )

        return draft

    async def auto_generate_questions(
        self,
        problem_description: str,
    ) -> list[str]:
        """Auto-generate targeted RTI questions from a problem description.

        Uses the LLM to analyze a free-text problem description and
        generate specific, pointed RTI questions that are likely to yield
        useful information under the RTI Act.

        Args:
            problem_description: A plain-language description of the
                citizen's problem or grievance.

        Returns:
            A list of specific RTI questions (typically 4-8 questions).

        Example:
            >>> questions = await rti_service.auto_generate_questions(
            ...     "My MGNREGA wages for the last 3 months have not been "
            ...     "paid despite completing the work."
            ... )
            >>> print(questions)
            [
                "Please provide the muster roll records for ...",
                "Please provide details of fund allocation ...",
                ...
            ]
        """
        logger.info(
            "auto_generating_rti_questions",
            description_length=len(problem_description),
        )

        prompt = textwrap.dedent(f"""\
            You are an expert RTI (Right to Information) advisor in India.
            A citizen has the following problem:

            "{problem_description}"

            Generate 5-7 specific, pointed RTI questions that the citizen
            can include in their RTI application under Section 6 of the
            RTI Act, 2005.

            Rules for generating questions:
            1. Each question must seek SPECIFIC information (documents,
               records, data, correspondence, decisions, file notings).
            2. Use the phrase "Please provide" or "Please furnish" to
               start each question.
            3. Include date ranges where relevant.
            4. Ask for file notings and correspondence where decision-
               making transparency is needed.
            5. Ask for specific records like muster rolls, sanction
               orders, fund utilization certificates, etc.
            6. Avoid vague questions -- be precise about what document
               or data is being sought.
            7. Frame questions so they fall under Section 2(f) definition
               of "information" (records, documents, memos, emails,
               opinions, advices, circulars, orders, logbooks, contracts,
               reports, papers, samples, models, data).

            Return ONLY the questions as a numbered list (1. 2. 3. etc.),
            with no other text, headers, or explanations.
        """)

        system_instruction = (
            "You are a legal expert specializing in the Indian Right to "
            "Information Act, 2005. You help citizens draft effective RTI "
            "questions that maximize the chance of obtaining useful "
            "information from public authorities. You are precise, "
            "specific, and legally accurate."
        )

        try:
            raw_response = await self._llm.generate(
                prompt,
                system_instruction=system_instruction,
            )
            questions = self._parse_numbered_list(raw_response)

            if not questions:
                logger.warning(
                    "llm_returned_no_questions_using_fallback",
                    response_length=len(raw_response),
                )
                questions = self._fallback_questions(problem_description)

            logger.info(
                "rti_questions_generated",
                num_questions=len(questions),
            )
            return questions

        except Exception:
            logger.error(
                "question_generation_failed_using_fallback",
                exc_info=True,
            )
            return self._fallback_questions(problem_description)

    async def identify_authority(
        self,
        problem_description: str,
    ) -> str:
        """Identify the correct public authority for an RTI application.

        First attempts a keyword-based deterministic lookup.  Falls back
        to the LLM for ambiguous or complex descriptions.

        Args:
            problem_description: A plain-language description of the
                citizen's problem.

        Returns:
            The name of the most appropriate public authority.
        """
        logger.info(
            "identifying_authority",
            description_length=len(problem_description),
        )

        # Try deterministic keyword matching first
        description_lower = problem_description.lower()
        for keyword, authority in _AUTHORITY_KEYWORDS.items():
            if keyword in description_lower:
                logger.info(
                    "authority_identified_via_keyword",
                    keyword=keyword,
                    authority=authority,
                )
                return authority

        # Fall back to LLM
        prompt = textwrap.dedent(f"""\
            You are an expert on Indian government administration.

            A citizen has the following problem:
            "{problem_description}"

            Identify the SINGLE most appropriate public authority (ministry,
            department, or public body) to which an RTI application should
            be addressed under the Right to Information Act, 2005.

            Rules:
            1. Be specific -- name the exact ministry, department, or body.
            2. For central government matters, name the ministry/department.
            3. For state government matters, name the department and mention
               it is a state-level authority.
            4. For local body matters, identify the type (Municipal
               Corporation, Gram Panchayat, District Administration, etc.).

            Return ONLY the name of the authority, with no explanation or
            additional text.
        """)

        system_instruction = (
            "You are an expert on Indian government structure, knowing "
            "all central ministries, state departments, and local bodies. "
            "Return only the authority name, nothing else."
        )

        try:
            authority = await self._llm.generate(
                prompt,
                system_instruction=system_instruction,
            )
            authority = authority.strip().strip('"').strip("'")

            if not authority or len(authority) < 5:
                authority = "Office of the District Collector / District Magistrate"
                logger.warning("llm_returned_empty_authority_using_default")

            logger.info("authority_identified_via_llm", authority=authority)
            return authority

        except Exception:
            logger.error(
                "authority_identification_failed_using_default",
                exc_info=True,
            )
            return "Office of the District Collector / District Magistrate"

    def get_fee_info(
        self,
        authority_level: str,
        state: str = "",
    ) -> FeeSchedule:
        """Get RTI fee information for a given authority level and state.

        Args:
            authority_level: One of ``"central"``, ``"state"``, or
                ``"local"``.  Central applies to Union ministries and
                bodies.  State and local use state-specific fee schedules.
            state: The state name (lowercase, underscored) for state-level
                queries.  Ignored for ``"central"``.

        Returns:
            A ``FeeSchedule`` with amount, payment modes, BPL exemption
            info, and state-specific notes.

        Example:
            >>> fee = rti_service.get_fee_info("state", "maharashtra")
            >>> print(fee.amount)
            'Rs. 10 (application fee) + Rs. 2 per page'
        """
        if authority_level == "central":
            return _CENTRAL_FEE

        # Normalize state name
        state_key = state.lower().strip().replace(" ", "_")

        if state_key in _STATE_FEES:
            return _STATE_FEES[state_key]

        # Default to central fee schedule if state not found
        logger.debug(
            "state_fee_not_found_using_central_default",
            state=state,
            authority_level=authority_level,
        )
        return FeeSchedule(
            amount="Rs. 10 (application fee) + Rs. 2 per page (typical)",
            payment_modes=[
                "Indian Postal Order (IPO)",
                "Court Fee Stamp",
                "Demand Draft (DD)",
                "Cash",
            ],
            bpl_exempt=True,
            state_specific_notes=(
                f"Fee schedule for '{state or authority_level}' not found in "
                "database. The most common fee is Rs. 10. Please verify with "
                "the respective State Information Commission or PIO's office."
            ),
        )

    def get_filing_instructions(
        self,
        authority_level: str,
    ) -> FilingInstructions:
        """Get step-by-step filing instructions for an RTI application.

        Args:
            authority_level: One of ``"central"``, ``"state"``, ``"local"``,
                or ``"central_offline"``.

        Returns:
            A ``FilingInstructions`` object with URL, steps, and required
            documents.

        Example:
            >>> instructions = rti_service.get_filing_instructions("central")
            >>> print(instructions.online_url)
            'https://rtionline.gov.in'
        """
        if authority_level == "central":
            return _CENTRAL_FILING
        if authority_level == "central_offline":
            return _OFFLINE_CENTRAL_FILING
        # State and local use the generic state/offline instructions
        return _STATE_FILING

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    def _classify_authority_level(self, public_authority: str) -> str:
        """Classify a public authority as central, state, or local.

        Uses keyword heuristics to determine the level.

        Returns:
            One of ``"central"``, ``"state"``, or ``"local"``.
        """
        authority_lower = public_authority.lower()

        central_keywords = [
            "ministry", "central", "union", "india", "national",
            "commission of india", "board of india", "authority of india",
            "uidai", "rbi", "reserve bank", "sebi", "ugc", "epfo",
            "esic", "cbi", "cic", "nhrc", "railways", "defence",
            "cbdt", "cbic",
        ]
        for keyword in central_keywords:
            if keyword in authority_lower:
                return "central"

        local_keywords = [
            "municipal", "nagar", "gram panchayat", "panchayat",
            "block", "tehsil", "corporation", "ward",
        ]
        for keyword in local_keywords:
            if keyword in authority_lower:
                return "local"

        state_keywords = [
            "state", "district", "collector", "revenue department",
            "home department", "directorate",
        ]
        for keyword in state_keywords:
            if keyword in authority_lower:
                return "state"

        # Default to central if unable to classify
        return "central"

    def _format_questions(self, questions: list[str]) -> str:
        """Format a list of questions into a numbered section."""
        lines: list[str] = []
        for idx, question in enumerate(questions, start=1):
            # Clean up the question text
            question = question.strip()
            if not question:
                continue
            # Remove existing numbering if present
            question = re.sub(r"^\d+[\.\)\-]\s*", "", question)
            lines.append(f"    {idx}. {question}")
        return "\n\n".join(lines) if lines else "    1. [Please specify the information sought]"

    @staticmethod
    def _parse_numbered_list(text: str) -> list[str]:
        """Parse a numbered list from LLM output.

        Handles formats like:
            1. Question text
            1) Question text
            1- Question text
        """
        if not text:
            return []

        lines = text.strip().split("\n")
        questions: list[str] = []
        current_question = ""

        for line in lines:
            line = line.strip()
            if not line:
                if current_question:
                    questions.append(current_question.strip())
                    current_question = ""
                continue

            # Check if this is a new numbered item
            match = re.match(r"^\d+[\.\)\-]\s*(.*)", line)
            if match:
                if current_question:
                    questions.append(current_question.strip())
                current_question = match.group(1)
            elif current_question:
                # Continuation of previous question
                current_question += " " + line

        # Don't forget the last question
        if current_question:
            questions.append(current_question.strip())

        # Filter out empty strings
        return [q for q in questions if q]

    @staticmethod
    def _fallback_questions(problem_description: str) -> list[str]:
        """Generate basic fallback questions when LLM is unavailable.

        These are generic but legally valid questions that can be
        included in any RTI application.
        """
        return [
            (
                f"Please provide all records, file notings, correspondence, "
                f"and decisions related to the following matter: "
                f"{problem_description}"
            ),
            (
                "Please provide copies of all relevant rules, guidelines, "
                "circulars, and office memoranda governing the above matter."
            ),
            (
                "Please provide the details of the officer(s) responsible "
                "for handling the above matter, including their name, "
                "designation, and contact details."
            ),
            (
                "Please provide the current status of the above matter and "
                "the expected timeline for resolution."
            ),
            (
                "Please provide the reasons for any delay or non-action "
                "on the above matter, along with copies of any relevant "
                "file notings recording such reasons."
            ),
        ]

    async def _refine_with_llm(
        self,
        application_text: str,
        request: RTIRequest,
    ) -> str:
        """Refine the RTI application text using the LLM.

        The LLM reviews the template-generated application and improves
        clarity, adds relevant legal references, and ensures the
        questions are optimally framed.

        Returns:
            Refined application text, or empty string on failure.
        """
        prompt = textwrap.dedent(f"""\
            You are an expert RTI application drafter in India. Review and
            refine the following RTI application. Make minimal changes --
            only improve clarity, fix any grammatical issues, and ensure
            the questions are specific and well-framed.

            IMPORTANT RULES:
            1. Do NOT change the applicant's name, address, or any
               personal details.
            2. Do NOT remove any questions -- only refine their wording.
            3. Keep the same overall format and structure.
            4. Ensure all Section references are accurate.
            5. Do NOT add any information not present in the original.
            6. Return ONLY the refined application text, with no
               commentary or explanation.

            Application to refine:

            {application_text}
        """)

        system_instruction = (
            "You are a legal document editor specializing in RTI applications "
            "under the Indian RTI Act, 2005. Return only the refined "
            "application text."
        )

        result = await self._llm.generate(
            prompt,
            system_instruction=system_instruction,
        )
        return result.strip() if result else ""
