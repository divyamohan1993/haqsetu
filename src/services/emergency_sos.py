"""Emergency SOS legal distress system for HaqSetu.

When a user is in immediate danger or legal distress, this service:

1. **Identifies** the type of emergency (domestic violence, child abuse,
   accident, police harassment, custodial violence, etc.).
2. **Provides the RIGHT helpline number immediately** -- Women helpline 181,
   Child helpline 1098, Police 100/112, DLSA, NHRC, etc.
3. **Generates a distress report** that can be shared with authorities,
   lawyers, or NGOs.
4. **Provides safety planning guidance** -- step-by-step instructions
   tailored to the situation.
5. **Tracks emergency reports** for follow-up and escalation.

This is a critical service -- for many users in rural India, HaqSetu may
be the ONLY way they discover the right helpline or legal aid contact.
The data here is curated and verified against official government sources.

Helpline data sources:
    * Ministry of Women and Child Development
    * National Commission for Women (NCW)
    * National Commission for Protection of Child Rights (NCPCR)
    * National Legal Services Authority (NALSA)
    * National Human Rights Commission (NHRC)
    * State Women's Commission websites
    * District Legal Services Authority (DLSA) directories
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Emergency type classification
# ---------------------------------------------------------------------------


class EmergencyType(StrEnum):
    """Types of emergencies the system can handle."""

    __slots__ = ()

    DOMESTIC_VIOLENCE = "domestic_violence"
    CHILD_ABUSE = "child_abuse"
    SEXUAL_ASSAULT = "sexual_assault"
    POLICE_HARASSMENT = "police_harassment"
    CUSTODIAL_VIOLENCE = "custodial_violence"
    DOWRY_HARASSMENT = "dowry_harassment"
    TRAFFICKING = "trafficking"
    ACID_ATTACK = "acid_attack"
    ROAD_ACCIDENT = "road_accident"
    MEDICAL_EMERGENCY = "medical_emergency"
    FIRE = "fire"
    NATURAL_DISASTER = "natural_disaster"
    LABOUR_EXPLOITATION = "labour_exploitation"
    CASTE_VIOLENCE = "caste_violence"
    COMMUNAL_VIOLENCE = "communal_violence"
    CYBER_CRIME = "cyber_crime"
    MISSING_PERSON = "missing_person"
    ILLEGAL_DETENTION = "illegal_detention"
    LAND_GRABBING = "land_grabbing"
    ELDER_ABUSE = "elder_abuse"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Emergency keyword -> type classification map
# ---------------------------------------------------------------------------

_EMERGENCY_KEYWORDS: Final[dict[str, EmergencyType]] = {
    # Domestic violence
    "domestic violence": EmergencyType.DOMESTIC_VIOLENCE,
    "husband beating": EmergencyType.DOMESTIC_VIOLENCE,
    "wife beating": EmergencyType.DOMESTIC_VIOLENCE,
    "marpit": EmergencyType.DOMESTIC_VIOLENCE,
    "ghar me maar": EmergencyType.DOMESTIC_VIOLENCE,
    "pati maarta hai": EmergencyType.DOMESTIC_VIOLENCE,
    "sasural me maar": EmergencyType.DOMESTIC_VIOLENCE,
    "domestic abuse": EmergencyType.DOMESTIC_VIOLENCE,
    "beaten at home": EmergencyType.DOMESTIC_VIOLENCE,
    "family violence": EmergencyType.DOMESTIC_VIOLENCE,
    # Dowry
    "dowry": EmergencyType.DOWRY_HARASSMENT,
    "dahej": EmergencyType.DOWRY_HARASSMENT,
    "dowry demand": EmergencyType.DOWRY_HARASSMENT,
    "dahej ki maang": EmergencyType.DOWRY_HARASSMENT,
    # Child abuse
    "child abuse": EmergencyType.CHILD_ABUSE,
    "child labour": EmergencyType.CHILD_ABUSE,
    "child marriage": EmergencyType.CHILD_ABUSE,
    "bal vivah": EmergencyType.CHILD_ABUSE,
    "child beaten": EmergencyType.CHILD_ABUSE,
    "baal shram": EmergencyType.CHILD_ABUSE,
    "child trafficking": EmergencyType.CHILD_ABUSE,
    "child exploitation": EmergencyType.CHILD_ABUSE,
    "minor abuse": EmergencyType.CHILD_ABUSE,
    "child missing": EmergencyType.CHILD_ABUSE,
    # Sexual assault
    "rape": EmergencyType.SEXUAL_ASSAULT,
    "sexual assault": EmergencyType.SEXUAL_ASSAULT,
    "sexual harassment": EmergencyType.SEXUAL_ASSAULT,
    "molestation": EmergencyType.SEXUAL_ASSAULT,
    "eve teasing": EmergencyType.SEXUAL_ASSAULT,
    "stalking": EmergencyType.SEXUAL_ASSAULT,
    "balatkar": EmergencyType.SEXUAL_ASSAULT,
    "chhedkhani": EmergencyType.SEXUAL_ASSAULT,
    # Police harassment
    "police harassment": EmergencyType.POLICE_HARASSMENT,
    "police beating": EmergencyType.POLICE_HARASSMENT,
    "false case": EmergencyType.POLICE_HARASSMENT,
    "police torture": EmergencyType.POLICE_HARASSMENT,
    "police threat": EmergencyType.POLICE_HARASSMENT,
    "not filing fir": EmergencyType.POLICE_HARASSMENT,
    "fir not registered": EmergencyType.POLICE_HARASSMENT,
    "police refusing": EmergencyType.POLICE_HARASSMENT,
    "thana me nahi sun rahe": EmergencyType.POLICE_HARASSMENT,
    # Custodial violence
    "custodial death": EmergencyType.CUSTODIAL_VIOLENCE,
    "lock up death": EmergencyType.CUSTODIAL_VIOLENCE,
    "jail torture": EmergencyType.CUSTODIAL_VIOLENCE,
    "custodial torture": EmergencyType.CUSTODIAL_VIOLENCE,
    "police custody": EmergencyType.CUSTODIAL_VIOLENCE,
    "hirasat me maut": EmergencyType.CUSTODIAL_VIOLENCE,
    # Trafficking
    "trafficking": EmergencyType.TRAFFICKING,
    "bonded labour": EmergencyType.TRAFFICKING,
    "forced labour": EmergencyType.TRAFFICKING,
    "human trafficking": EmergencyType.TRAFFICKING,
    "manav taskar": EmergencyType.TRAFFICKING,
    # Acid attack
    "acid attack": EmergencyType.ACID_ATTACK,
    "tezab": EmergencyType.ACID_ATTACK,
    "acid thrown": EmergencyType.ACID_ATTACK,
    # Road accident
    "road accident": EmergencyType.ROAD_ACCIDENT,
    "car accident": EmergencyType.ROAD_ACCIDENT,
    "bike accident": EmergencyType.ROAD_ACCIDENT,
    "hit and run": EmergencyType.ROAD_ACCIDENT,
    "sadak durghatna": EmergencyType.ROAD_ACCIDENT,
    "accident": EmergencyType.ROAD_ACCIDENT,
    # Medical emergency
    "medical emergency": EmergencyType.MEDICAL_EMERGENCY,
    "heart attack": EmergencyType.MEDICAL_EMERGENCY,
    "hospital refused": EmergencyType.MEDICAL_EMERGENCY,
    "ambulance": EmergencyType.MEDICAL_EMERGENCY,
    "snake bite": EmergencyType.MEDICAL_EMERGENCY,
    "poison": EmergencyType.MEDICAL_EMERGENCY,
    # Fire
    "fire": EmergencyType.FIRE,
    "aag lagi": EmergencyType.FIRE,
    "building fire": EmergencyType.FIRE,
    # Natural disaster
    "flood": EmergencyType.NATURAL_DISASTER,
    "earthquake": EmergencyType.NATURAL_DISASTER,
    "cyclone": EmergencyType.NATURAL_DISASTER,
    "landslide": EmergencyType.NATURAL_DISASTER,
    "baadh": EmergencyType.NATURAL_DISASTER,
    "bhoochal": EmergencyType.NATURAL_DISASTER,
    # Labour exploitation
    "labour exploitation": EmergencyType.LABOUR_EXPLOITATION,
    "wages not paid": EmergencyType.LABOUR_EXPLOITATION,
    "mazdoori nahi mili": EmergencyType.LABOUR_EXPLOITATION,
    "minimum wage": EmergencyType.LABOUR_EXPLOITATION,
    "factory abuse": EmergencyType.LABOUR_EXPLOITATION,
    "no payment": EmergencyType.LABOUR_EXPLOITATION,
    # Caste violence
    "caste violence": EmergencyType.CASTE_VIOLENCE,
    "untouchability": EmergencyType.CASTE_VIOLENCE,
    "dalit atrocity": EmergencyType.CASTE_VIOLENCE,
    "caste discrimination": EmergencyType.CASTE_VIOLENCE,
    "jaati hinsa": EmergencyType.CASTE_VIOLENCE,
    "sc st atrocity": EmergencyType.CASTE_VIOLENCE,
    # Communal violence
    "communal violence": EmergencyType.COMMUNAL_VIOLENCE,
    "riot": EmergencyType.COMMUNAL_VIOLENCE,
    "communal riot": EmergencyType.COMMUNAL_VIOLENCE,
    "religious violence": EmergencyType.COMMUNAL_VIOLENCE,
    "danga": EmergencyType.COMMUNAL_VIOLENCE,
    # Cyber crime
    "cyber crime": EmergencyType.CYBER_CRIME,
    "online fraud": EmergencyType.CYBER_CRIME,
    "online harassment": EmergencyType.CYBER_CRIME,
    "morphed photos": EmergencyType.CYBER_CRIME,
    "sextortion": EmergencyType.CYBER_CRIME,
    "upi fraud": EmergencyType.CYBER_CRIME,
    "bank fraud": EmergencyType.CYBER_CRIME,
    # Missing person
    "missing person": EmergencyType.MISSING_PERSON,
    "person missing": EmergencyType.MISSING_PERSON,
    "laapta": EmergencyType.MISSING_PERSON,
    "gayab": EmergencyType.MISSING_PERSON,
    # Illegal detention
    "illegal detention": EmergencyType.ILLEGAL_DETENTION,
    "wrongful confinement": EmergencyType.ILLEGAL_DETENTION,
    "held against will": EmergencyType.ILLEGAL_DETENTION,
    "band karke rakha": EmergencyType.ILLEGAL_DETENTION,
    # Land grabbing
    "land grabbing": EmergencyType.LAND_GRABBING,
    "encroachment": EmergencyType.LAND_GRABBING,
    "zameen kabza": EmergencyType.LAND_GRABBING,
    "land dispute": EmergencyType.LAND_GRABBING,
    "forced eviction": EmergencyType.LAND_GRABBING,
    # Elder abuse
    "elder abuse": EmergencyType.ELDER_ABUSE,
    "old age abuse": EmergencyType.ELDER_ABUSE,
    "parent abuse": EmergencyType.ELDER_ABUSE,
    "budhape me pareshan": EmergencyType.ELDER_ABUSE,
    "senior citizen abuse": EmergencyType.ELDER_ABUSE,
}


# ---------------------------------------------------------------------------
# National helplines -- VERIFIED against official government sources
# ---------------------------------------------------------------------------

_NATIONAL_HELPLINES: Final[dict[str, dict[str, str]]] = {
    "police_emergency": {
        "name": "Police Emergency",
        "number": "112",
        "alternate": "100",
        "description": "Single emergency number for police, fire, and ambulance",
        "available": "24x7",
    },
    "women_helpline": {
        "name": "Women Helpline (NCW)",
        "number": "181",
        "alternate": "7827-170-170",
        "description": "National Commission for Women -- domestic violence, harassment, abuse",
        "available": "24x7",
    },
    "child_helpline": {
        "name": "Childline India",
        "number": "1098",
        "alternate": "011-23978046",
        "description": "Child abuse, child labour, missing children, child marriage",
        "available": "24x7",
    },
    "ambulance": {
        "name": "National Ambulance Service",
        "number": "108",
        "alternate": "102",
        "description": "Free ambulance service across India",
        "available": "24x7",
    },
    "fire": {
        "name": "Fire Brigade",
        "number": "101",
        "alternate": "112",
        "description": "Fire emergency",
        "available": "24x7",
    },
    "nhrc": {
        "name": "National Human Rights Commission",
        "number": "14433",
        "alternate": "011-23385368",
        "description": "Human rights violations, custodial deaths, police atrocities",
        "available": "9:30 AM - 5:30 PM, Mon-Fri",
    },
    "ncw": {
        "name": "National Commission for Women",
        "number": "7827-170-170",
        "alternate": "011-26944880",
        "description": "Crimes against women -- file complaints online at ncw.nic.in",
        "available": "24x7 (WhatsApp), Office hours for calls",
    },
    "nalsa": {
        "name": "National Legal Services Authority",
        "number": "15100",
        "alternate": "011-23382778",
        "description": "Free legal aid for SC/ST, women, children, disabled, poor",
        "available": "9:30 AM - 5:30 PM, Mon-Fri",
    },
    "cyber_crime": {
        "name": "National Cyber Crime Helpline",
        "number": "1930",
        "alternate": "155260",
        "description": "Online fraud, sextortion, cyber bullying -- report at cybercrime.gov.in",
        "available": "24x7",
    },
    "senior_citizen": {
        "name": "Elder Helpline",
        "number": "14567",
        "alternate": "112",
        "description": "Senior citizen helpline -- abuse, pension, legal aid",
        "available": "24x7",
    },
    "sc_st_commission": {
        "name": "National Commission for Scheduled Castes",
        "number": "011-23320649",
        "alternate": "011-23320015",
        "description": "SC/ST atrocities, caste discrimination complaints",
        "available": "9:30 AM - 5:30 PM, Mon-Fri",
    },
    "anti_trafficking": {
        "name": "Anti-Trafficking Helpline",
        "number": "1800-419-8588",
        "alternate": "011-23317004",
        "description": "Human trafficking, bonded labour, forced labour",
        "available": "24x7 (toll-free)",
    },
    "disaster_management": {
        "name": "NDMA Helpline",
        "number": "1078",
        "alternate": "011-26701700",
        "description": "National Disaster Management Authority -- floods, earthquakes, cyclones",
        "available": "24x7",
    },
    "road_accident": {
        "name": "Road Accident Emergency",
        "number": "1073",
        "alternate": "112",
        "description": "Highway accident assistance, trauma care",
        "available": "24x7",
    },
    "railway_helpline": {
        "name": "Railway Helpline",
        "number": "139",
        "alternate": "182",
        "description": "Railway emergencies and security",
        "available": "24x7",
    },
    "acid_attack": {
        "name": "Acid Attack Helpline (NCW)",
        "number": "181",
        "alternate": "7827-170-170",
        "description": "Immediate medical + legal help for acid attack survivors",
        "available": "24x7",
    },
    "mental_health": {
        "name": "iCall Psychosocial Helpline",
        "number": "9152987821",
        "alternate": "080-46110007",
        "description": "Mental health crisis, suicidal thoughts, emotional distress",
        "available": "Mon-Sat 8AM-10PM",
    },
    "labour_helpline": {
        "name": "Shram Suvidha (Labour Ministry)",
        "number": "14434",
        "alternate": "011-23710465",
        "description": "Labour disputes, unpaid wages, unsafe working conditions",
        "available": "9:30 AM - 5:30 PM, Mon-Fri",
    },
}

# Emergency type to helpline mapping -- which helplines to show for each type
_EMERGENCY_HELPLINE_MAP: Final[dict[EmergencyType, list[str]]] = {
    EmergencyType.DOMESTIC_VIOLENCE: [
        "women_helpline", "police_emergency", "nalsa", "ncw",
    ],
    EmergencyType.CHILD_ABUSE: [
        "child_helpline", "police_emergency", "nalsa", "ncw",
    ],
    EmergencyType.SEXUAL_ASSAULT: [
        "women_helpline", "police_emergency", "nalsa", "ncw",
    ],
    EmergencyType.POLICE_HARASSMENT: [
        "nhrc", "nalsa", "police_emergency",
    ],
    EmergencyType.CUSTODIAL_VIOLENCE: [
        "nhrc", "nalsa", "police_emergency",
    ],
    EmergencyType.DOWRY_HARASSMENT: [
        "women_helpline", "police_emergency", "nalsa", "ncw",
    ],
    EmergencyType.TRAFFICKING: [
        "anti_trafficking", "police_emergency", "child_helpline", "nalsa",
    ],
    EmergencyType.ACID_ATTACK: [
        "acid_attack", "ambulance", "police_emergency", "nalsa",
    ],
    EmergencyType.ROAD_ACCIDENT: [
        "road_accident", "ambulance", "police_emergency",
    ],
    EmergencyType.MEDICAL_EMERGENCY: [
        "ambulance", "police_emergency",
    ],
    EmergencyType.FIRE: [
        "fire", "police_emergency", "ambulance",
    ],
    EmergencyType.NATURAL_DISASTER: [
        "disaster_management", "police_emergency", "ambulance",
    ],
    EmergencyType.LABOUR_EXPLOITATION: [
        "labour_helpline", "nalsa", "police_emergency",
    ],
    EmergencyType.CASTE_VIOLENCE: [
        "sc_st_commission", "nhrc", "police_emergency", "nalsa",
    ],
    EmergencyType.COMMUNAL_VIOLENCE: [
        "nhrc", "police_emergency", "nalsa",
    ],
    EmergencyType.CYBER_CRIME: [
        "cyber_crime", "police_emergency",
    ],
    EmergencyType.MISSING_PERSON: [
        "police_emergency", "child_helpline",
    ],
    EmergencyType.ILLEGAL_DETENTION: [
        "nhrc", "nalsa", "police_emergency",
    ],
    EmergencyType.LAND_GRABBING: [
        "police_emergency", "nalsa",
    ],
    EmergencyType.ELDER_ABUSE: [
        "senior_citizen", "police_emergency", "nalsa",
    ],
    EmergencyType.OTHER: [
        "police_emergency", "nalsa",
    ],
}


# ---------------------------------------------------------------------------
# State-specific women's commissions and legal aid contacts
# ---------------------------------------------------------------------------

_STATE_WOMEN_COMMISSIONS: Final[dict[str, dict[str, str]]] = {
    "andhra_pradesh": {
        "name": "Andhra Pradesh State Commission for Women",
        "number": "0866-2436500",
        "address": "Vijayawada, Andhra Pradesh",
        "website": "apscw.ap.gov.in",
    },
    "assam": {
        "name": "Assam State Commission for Women",
        "number": "0361-2261090",
        "address": "Guwahati, Assam",
        "website": "scw.assam.gov.in",
    },
    "bihar": {
        "name": "Bihar State Commission for Women",
        "number": "0612-2215877",
        "address": "Patna, Bihar",
        "website": "bswc.bihar.gov.in",
    },
    "chhattisgarh": {
        "name": "Chhattisgarh State Commission for Women",
        "number": "0771-2511100",
        "address": "Raipur, Chhattisgarh",
        "website": "cscw.cg.gov.in",
    },
    "delhi": {
        "name": "Delhi Commission for Women",
        "number": "011-23378044",
        "alternate": "181",
        "address": "C Block, Vikas Bhawan, IP Estate, New Delhi",
        "website": "dcw.delhigovt.nic.in",
    },
    "goa": {
        "name": "Goa State Commission for Women",
        "number": "0832-2437448",
        "address": "Panaji, Goa",
        "website": "goa.gov.in/department/women-commission",
    },
    "gujarat": {
        "name": "Gujarat State Commission for Women",
        "number": "079-23253891",
        "address": "Gandhinagar, Gujarat",
        "website": "gscw.gujarat.gov.in",
    },
    "haryana": {
        "name": "Haryana State Commission for Women",
        "number": "0172-2560028",
        "address": "Chandigarh, Haryana",
        "website": "hscw.haryana.gov.in",
    },
    "himachal_pradesh": {
        "name": "Himachal Pradesh State Commission for Women",
        "number": "0177-2623723",
        "address": "Shimla, Himachal Pradesh",
        "website": "hpscw.hp.gov.in",
    },
    "jharkhand": {
        "name": "Jharkhand State Commission for Women",
        "number": "0651-2400614",
        "address": "Ranchi, Jharkhand",
        "website": "jscw.jharkhand.gov.in",
    },
    "karnataka": {
        "name": "Karnataka State Commission for Women",
        "number": "080-22100435",
        "address": "Bangalore, Karnataka",
        "website": "kscw.karnataka.gov.in",
    },
    "kerala": {
        "name": "Kerala State Commission for Women",
        "number": "0471-2322590",
        "address": "Thiruvananthapuram, Kerala",
        "website": "kswc.kerala.gov.in",
    },
    "madhya_pradesh": {
        "name": "Madhya Pradesh State Commission for Women",
        "number": "0755-2661813",
        "address": "Bhopal, Madhya Pradesh",
        "website": "mpscw.mp.gov.in",
    },
    "maharashtra": {
        "name": "Maharashtra State Commission for Women",
        "number": "022-26592707",
        "address": "Mumbai, Maharashtra",
        "website": "mscw.maharashtra.gov.in",
    },
    "odisha": {
        "name": "Odisha State Commission for Women",
        "number": "0674-2536625",
        "address": "Bhubaneswar, Odisha",
        "website": "oscw.odisha.gov.in",
    },
    "punjab": {
        "name": "Punjab State Commission for Women",
        "number": "0172-2742974",
        "address": "Chandigarh, Punjab",
        "website": "pscw.punjab.gov.in",
    },
    "rajasthan": {
        "name": "Rajasthan State Commission for Women",
        "number": "0141-2779001",
        "address": "Jaipur, Rajasthan",
        "website": "rscw.rajasthan.gov.in",
    },
    "tamil_nadu": {
        "name": "Tamil Nadu State Commission for Women",
        "number": "044-28279800",
        "address": "Chennai, Tamil Nadu",
        "website": "tncw.tn.gov.in",
    },
    "telangana": {
        "name": "Telangana State Commission for Women",
        "number": "040-23235955",
        "address": "Hyderabad, Telangana",
        "website": "tscw.telangana.gov.in",
    },
    "uttar_pradesh": {
        "name": "Uttar Pradesh State Commission for Women",
        "number": "0522-2306403",
        "alternate": "1090",
        "address": "Lucknow, Uttar Pradesh",
        "website": "upscw.up.gov.in",
    },
    "uttarakhand": {
        "name": "Uttarakhand State Commission for Women",
        "number": "0135-2712831",
        "address": "Dehradun, Uttarakhand",
        "website": "uscw.uk.gov.in",
    },
    "west_bengal": {
        "name": "West Bengal State Commission for Women",
        "number": "033-23344788",
        "address": "Kolkata, West Bengal",
        "website": "wbscw.wb.gov.in",
    },
    "meghalaya": {
        "name": "Meghalaya State Commission for Women",
        "number": "0364-2224307",
        "address": "Shillong, Meghalaya",
        "website": "megscw.gov.in",
    },
    "manipur": {
        "name": "Manipur State Commission for Women",
        "number": "0385-2451478",
        "address": "Imphal, Manipur",
        "website": "mscw.manipur.gov.in",
    },
    "mizoram": {
        "name": "Mizoram State Commission for Women",
        "number": "0389-2322382",
        "address": "Aizawl, Mizoram",
        "website": "mscw.mizoram.gov.in",
    },
    "nagaland": {
        "name": "Nagaland State Commission for Women",
        "number": "0370-2270141",
        "address": "Kohima, Nagaland",
        "website": "nscw.nagaland.gov.in",
    },
    "tripura": {
        "name": "Tripura State Commission for Women",
        "number": "0381-2325358",
        "address": "Agartala, Tripura",
        "website": "tscw.tripura.gov.in",
    },
    "sikkim": {
        "name": "Sikkim State Commission for Women",
        "number": "03592-202539",
        "address": "Gangtok, Sikkim",
        "website": "sscw.sikkim.gov.in",
    },
    "arunachal_pradesh": {
        "name": "Arunachal Pradesh State Commission for Women",
        "number": "0360-2212458",
        "address": "Itanagar, Arunachal Pradesh",
        "website": "apscw.arunachal.gov.in",
    },
    "jammu_kashmir": {
        "name": "J&K State Commission for Women",
        "number": "0194-2477309",
        "address": "Srinagar, Jammu & Kashmir",
        "website": "jkscw.jk.gov.in",
    },
}

# State name normalization -- handles common variations users might type
_STATE_ALIASES: Final[dict[str, str]] = {
    "ap": "andhra_pradesh",
    "andhra": "andhra_pradesh",
    "andhra pradesh": "andhra_pradesh",
    "assam": "assam",
    "bihar": "bihar",
    "cg": "chhattisgarh",
    "chhattisgarh": "chhattisgarh",
    "chattisgarh": "chhattisgarh",
    "delhi": "delhi",
    "ncr": "delhi",
    "new delhi": "delhi",
    "goa": "goa",
    "guj": "gujarat",
    "gujarat": "gujarat",
    "hr": "haryana",
    "haryana": "haryana",
    "hp": "himachal_pradesh",
    "himachal": "himachal_pradesh",
    "himachal pradesh": "himachal_pradesh",
    "jharkhand": "jharkhand",
    "karnataka": "karnataka",
    "bangalore": "karnataka",
    "bengaluru": "karnataka",
    "kerala": "kerala",
    "mp": "madhya_pradesh",
    "madhya pradesh": "madhya_pradesh",
    "mh": "maharashtra",
    "maharashtra": "maharashtra",
    "mumbai": "maharashtra",
    "odisha": "odisha",
    "orissa": "odisha",
    "punjab": "punjab",
    "pb": "punjab",
    "rj": "rajasthan",
    "rajasthan": "rajasthan",
    "tn": "tamil_nadu",
    "tamil nadu": "tamil_nadu",
    "tamilnadu": "tamil_nadu",
    "chennai": "tamil_nadu",
    "ts": "telangana",
    "telangana": "telangana",
    "hyderabad": "telangana",
    "up": "uttar_pradesh",
    "uttar pradesh": "uttar_pradesh",
    "lucknow": "uttar_pradesh",
    "uk": "uttarakhand",
    "uttarakhand": "uttarakhand",
    "wb": "west_bengal",
    "west bengal": "west_bengal",
    "bengal": "west_bengal",
    "kolkata": "west_bengal",
    "meghalaya": "meghalaya",
    "manipur": "manipur",
    "mizoram": "mizoram",
    "nagaland": "nagaland",
    "tripura": "tripura",
    "sikkim": "sikkim",
    "arunachal": "arunachal_pradesh",
    "arunachal pradesh": "arunachal_pradesh",
    "jk": "jammu_kashmir",
    "j&k": "jammu_kashmir",
    "jammu": "jammu_kashmir",
    "kashmir": "jammu_kashmir",
    "jammu kashmir": "jammu_kashmir",
    "jammu and kashmir": "jammu_kashmir",
}


# ---------------------------------------------------------------------------
# State DLSA (District Legal Services Authority) contacts
# ---------------------------------------------------------------------------

_STATE_DLSA: Final[dict[str, dict[str, str]]] = {
    "andhra_pradesh": {
        "name": "Andhra Pradesh SLSA",
        "number": "0866-2574567",
        "website": "apslsa.ap.gov.in",
    },
    "bihar": {
        "name": "Bihar SLSA",
        "number": "0612-2506801",
        "website": "bslsa.bihar.gov.in",
    },
    "delhi": {
        "name": "Delhi SLSA",
        "number": "011-23384781",
        "website": "dslsa.org",
    },
    "gujarat": {
        "name": "Gujarat SLSA",
        "number": "079-27913102",
        "website": "gujslsa.in",
    },
    "haryana": {
        "name": "Haryana SLSA",
        "number": "0172-2749540",
        "website": "hslsa.gov.in",
    },
    "karnataka": {
        "name": "Karnataka SLSA",
        "number": "080-22112825",
        "website": "kslsa.kar.nic.in",
    },
    "kerala": {
        "name": "Kerala SLSA",
        "number": "0471-2306062",
        "website": "kelsa.nic.in",
    },
    "madhya_pradesh": {
        "name": "Madhya Pradesh SLSA",
        "number": "0755-2577771",
        "website": "mpslsa.gov.in",
    },
    "maharashtra": {
        "name": "Maharashtra SLSA",
        "number": "022-22676376",
        "website": "mahalsa.gov.in",
    },
    "rajasthan": {
        "name": "Rajasthan SLSA",
        "number": "0141-2227727",
        "website": "rlsa.gov.in",
    },
    "tamil_nadu": {
        "name": "Tamil Nadu SLSA",
        "number": "044-25341836",
        "website": "tnsla.tn.gov.in",
    },
    "telangana": {
        "name": "Telangana SLSA",
        "number": "040-23446922",
        "website": "tslsa.telangana.gov.in",
    },
    "uttar_pradesh": {
        "name": "Uttar Pradesh SLSA",
        "number": "0522-2209174",
        "website": "upslsa.up.gov.in",
    },
    "west_bengal": {
        "name": "West Bengal SLSA",
        "number": "033-22433242",
        "website": "wbslsa.gov.in",
    },
}


# ---------------------------------------------------------------------------
# Safety plan templates by emergency type
# ---------------------------------------------------------------------------

_SAFETY_PLANS: Final[dict[EmergencyType, dict[str, list[str] | str]]] = {
    EmergencyType.DOMESTIC_VIOLENCE: {
        "title": "Safety Plan for Domestic Violence",
        "immediate_steps": [
            "If you are in immediate danger, call 112 (emergency) or 181 (women helpline) RIGHT NOW.",
            "Try to move to a safe room with a lock and a phone. Avoid kitchen or bathroom where weapons may be accessible.",
            "If you can leave safely, go to a trusted neighbour, family member, or the nearest police station.",
            "If there are children, take them with you if safely possible.",
        ],
        "documentation_steps": [
            "Take photos of any injuries using your phone. These are critical evidence.",
            "Save threatening messages, call recordings, or WhatsApp chats -- take screenshots.",
            "Write down dates, times, and details of abuse incidents in a safe place.",
            "Get a medical examination done at a government hospital -- ask for a Medico-Legal Case (MLC) report.",
        ],
        "legal_steps": [
            "File an FIR at the nearest police station. Under Section 498A IPC, domestic violence is a cognizable offence.",
            "If police refuse to file FIR, go to the Superintendent of Police (SP) or file a written complaint by post to the SP.",
            "Apply for a Protection Order under the Domestic Violence Act 2005 through a magistrate court.",
            "Contact DLSA (District Legal Services Authority) at 15100 for FREE legal aid -- you do not need to pay for a lawyer.",
            "You can also file an online complaint at ncw.nic.in (National Commission for Women).",
        ],
        "shelter_info": (
            "One Stop Centres (Sakhi Centres) provide shelter, legal aid, medical help, and counselling "
            "under one roof. Call 181 to find the nearest centre. There are 700+ OSCs across India."
        ),
        "key_laws": [
            "Protection of Women from Domestic Violence Act, 2005 (DV Act)",
            "Section 498A IPC -- Cruelty by husband or his relatives",
            "Section 304B IPC -- Dowry death",
            "Section 354 IPC -- Assault on woman with intent to outrage modesty",
        ],
    },
    EmergencyType.CHILD_ABUSE: {
        "title": "Safety Plan for Child Abuse / Child in Danger",
        "immediate_steps": [
            "Call Childline 1098 IMMEDIATELY -- it is a 24x7 toll-free helpline for children in distress.",
            "If the child is in immediate physical danger, call 112 (police emergency).",
            "Do NOT confront the abuser alone -- ensure the child is safe first.",
            "Take the child to a safe location (school, hospital, police station, or trusted adult).",
        ],
        "documentation_steps": [
            "Note down the child's name, age, location, and nature of abuse.",
            "If there are visible injuries, get a medical examination at a government hospital.",
            "Preserve any evidence -- messages, photos, or witness statements.",
            "Record names of witnesses if any.",
        ],
        "legal_steps": [
            "File an FIR at the police station. Child abuse is a cognizable and non-bailable offence under POCSO Act.",
            "Under POCSO Act 2012, ANY person who knows of child sexual abuse MUST report it -- failure to report is also an offence.",
            "The child's identity must be kept confidential by law.",
            "Contact the Child Welfare Committee (CWC) through Childline 1098 for immediate protection.",
            "Free legal aid is available through DLSA (15100) for all child abuse cases.",
        ],
        "shelter_info": (
            "Children can be placed in Children's Homes or Specialized Adoption Agencies through the "
            "Child Welfare Committee. Childline 1098 coordinates all rescue and rehabilitation."
        ),
        "key_laws": [
            "Protection of Children from Sexual Offences (POCSO) Act, 2012",
            "Juvenile Justice (Care and Protection of Children) Act, 2015",
            "Child Labour (Prohibition and Regulation) Act, 1986",
            "Prohibition of Child Marriage Act, 2006",
            "Right to Education Act, 2009",
        ],
    },
    EmergencyType.SEXUAL_ASSAULT: {
        "title": "Safety Plan for Sexual Assault",
        "immediate_steps": [
            "Call 112 (police emergency) or 181 (women helpline) IMMEDIATELY.",
            "Get to a safe location. If possible, go directly to a hospital or police station.",
            "Do NOT bathe, change clothes, or wash -- physical evidence is critical for the case.",
            "If possible, ask a trusted person to accompany you.",
        ],
        "documentation_steps": [
            "Go to the nearest government hospital for a medical examination within 72 hours.",
            "The hospital MUST conduct a free medical examination and provide an MLC report -- this is your legal right.",
            "Under the Criminal Law Amendment Act 2013, a woman's statement is recorded by a female officer.",
            "Ask for a copy of the FIR and medical report.",
        ],
        "legal_steps": [
            "File an FIR. Under Section 376 IPC, rape is a cognizable and non-bailable offence. Police CANNOT refuse to register it.",
            "If the police refuse, approach the Superintendent of Police or a judicial magistrate under Section 156(3) CrPC.",
            "Zero FIR: You can file an FIR at ANY police station regardless of jurisdiction.",
            "The trial must be completed within 2 months (fast-track courts for sexual offences).",
            "Free legal aid is available through DLSA (15100). One Stop Centres (181) also provide lawyers.",
            "You can file an online complaint at ncw.nic.in or the SHE-box portal (shebox.nic.in).",
        ],
        "shelter_info": (
            "One Stop Centres (Sakhi Centres) provide immediate shelter, medical aid, legal counselling, "
            "and psycho-social support. Call 181 for the nearest centre."
        ),
        "key_laws": [
            "Section 375/376 IPC -- Rape",
            "Criminal Law (Amendment) Act, 2013 (Nirbhaya Act)",
            "Sexual Harassment of Women at Workplace Act, 2013 (POSH Act)",
            "POCSO Act, 2012 (if survivor is a minor)",
            "Section 354A-D IPC -- Sexual harassment, stalking, voyeurism, disrobing",
        ],
    },
    EmergencyType.POLICE_HARASSMENT: {
        "title": "Safety Plan for Police Harassment",
        "immediate_steps": [
            "Stay calm. Do NOT resist physically -- note badge numbers and names of officers involved.",
            "Call a family member, friend, or lawyer immediately and inform them of your location.",
            "You have the RIGHT to know the reason for any arrest or detention.",
            "Under Article 22 of the Constitution, you must be produced before a magistrate within 24 hours of arrest.",
        ],
        "documentation_steps": [
            "Note down date, time, place, badge numbers, and names of all officers involved.",
            "Record video/audio if safely possible (recording police in public is legal in India).",
            "Get names and phone numbers of witnesses.",
            "If injured, get a medical examination and preserve the MLC report.",
        ],
        "legal_steps": [
            "File a complaint with the Superintendent of Police (SP) in writing.",
            "Complain to the NHRC (National Human Rights Commission) at 14433 or nhrc.nic.in.",
            "File a complaint with the State Human Rights Commission.",
            "Approach the High Court under Article 226 for habeas corpus if illegally detained.",
            "Contact DLSA (15100) for free legal aid immediately.",
            "Under DK Basu vs State of West Bengal (1997), police MUST follow arrest procedures including informing family.",
        ],
        "shelter_info": (
            "If you are being threatened by police, immediately contact NHRC (14433), NALSA (15100), "
            "or your nearest High Court Legal Services Committee."
        ),
        "key_laws": [
            "Article 21 -- Right to Life and Personal Liberty",
            "Article 22 -- Protection against Arrest and Detention",
            "Section 330/331 IPC -- Voluntarily causing hurt to extort confession",
            "DK Basu Guidelines (1997) -- Mandatory procedures during arrest",
            "Section 197 CrPC -- Sanction required for prosecuting public servants",
        ],
    },
    EmergencyType.CASTE_VIOLENCE: {
        "title": "Safety Plan for Caste-based Violence / SC/ST Atrocity",
        "immediate_steps": [
            "Call 112 (police emergency) immediately if in physical danger.",
            "Move to a safe location with family/community members.",
            "Contact the National Commission for Scheduled Castes at 011-23320649.",
            "Call NHRC at 14433 for human rights violation.",
        ],
        "documentation_steps": [
            "Document everything: photos of injuries/property damage, witness names, timestamps.",
            "Get a medical examination if injured.",
            "Record the exact caste-based slurs or discriminatory acts in writing.",
            "Preserve any evidence of untouchability practices.",
        ],
        "legal_steps": [
            "File an FIR under the SC/ST (Prevention of Atrocities) Act, 1989. This is a SPECIAL law with stronger protections.",
            "Under this Act, police MUST register the FIR -- refusal is itself a punishable offence.",
            "The accused cannot get anticipatory bail in atrocity cases.",
            "Compensation of Rs 1-8.25 lakh is payable to the victim depending on the offence, along with other relief.",
            "A special court must try the case -- trial should be completed within 2 months.",
            "Free legal aid is GUARANTEED for SC/ST victims through DLSA (15100).",
        ],
        "shelter_info": (
            "Contact the District Magistrate for immediate protection and relief. SC/ST victims are "
            "entitled to travel expenses, maintenance, and rehabilitation under the Atrocities Act rules."
        ),
        "key_laws": [
            "Scheduled Castes and Scheduled Tribes (Prevention of Atrocities) Act, 1989",
            "SC/ST (Prevention of Atrocities) Amendment Act, 2015",
            "Protection of Civil Rights Act, 1955",
            "Article 17 -- Abolition of Untouchability",
            "Article 46 -- Promotion of educational and economic interests of SC/STs",
        ],
    },
    EmergencyType.CYBER_CRIME: {
        "title": "Safety Plan for Cyber Crime",
        "immediate_steps": [
            "Call 1930 (National Cyber Crime Helpline) IMMEDIATELY -- especially for financial fraud (within the golden hour).",
            "For financial fraud: Call your bank's customer care and block your card/account immediately.",
            "Do NOT delete any messages, emails, or transaction records -- these are evidence.",
            "Take screenshots of everything related to the crime.",
        ],
        "documentation_steps": [
            "Save URLs, profile screenshots, chat logs, transaction IDs, and email headers.",
            "Note down the accused's phone number, email, social media profile, or UPI ID.",
            "Record the exact amount lost (if financial fraud) and bank account details.",
            "Get a bank statement showing the fraudulent transaction.",
        ],
        "legal_steps": [
            "File a complaint on cybercrime.gov.in -- the national cyber crime reporting portal.",
            "File an FIR at the nearest police station or the Cyber Crime cell.",
            "Under IT Act Section 66C, identity theft carries up to 3 years imprisonment.",
            "For sextortion/morphed images, file under Section 67A of the IT Act (up to 5 years imprisonment).",
            "Banks must reverse unauthorized transactions if reported within 3 working days (RBI circular).",
            "Contact DLSA (15100) for free legal aid.",
        ],
        "shelter_info": (
            "For persistent online harassment, you can approach the Cyber Crime cells in major cities. "
            "For women, complaints can also be filed at the SHE-box portal (shebox.nic.in)."
        ),
        "key_laws": [
            "Information Technology Act, 2000 (Sections 66, 66C, 66D, 67, 67A)",
            "Section 354D IPC -- Stalking (including cyber stalking)",
            "Section 509 IPC -- Word, gesture or act intended to insult modesty",
            "RBI Master Direction on Digital Payment Security Controls",
        ],
    },
    EmergencyType.LABOUR_EXPLOITATION: {
        "title": "Safety Plan for Labour Exploitation / Unpaid Wages",
        "immediate_steps": [
            "Call the Labour Helpline at 14434 (Shram Suvidha).",
            "If in bonded labour or trafficking situation, call 1800-419-8588 (Anti-Trafficking) or 112.",
            "Document your working hours, wages owed, and employer details.",
            "Contact the nearest Labour Commissioner's office.",
        ],
        "documentation_steps": [
            "Keep records of days worked, agreed wage rate, and amounts received.",
            "Photograph your workplace, ID cards, wage slips, or any employment letters.",
            "Get contact information of fellow workers who can serve as witnesses.",
            "Note the employer's full name, business name, and address.",
        ],
        "legal_steps": [
            "File a complaint with the Labour Commissioner under the Payment of Wages Act.",
            "Under the Minimum Wages Act, paying below minimum wage is a criminal offence.",
            "For bonded labour: The District Magistrate has the power to release bonded labourers under the Bonded Labour System (Abolition) Act.",
            "Compensation of Rs 1 lakh minimum is payable to released bonded labourers.",
            "File on the SHRAM portal (shframeport.gov.in) or the e-Shram portal for unorganised workers.",
            "Free legal aid is available through DLSA (15100).",
        ],
        "shelter_info": (
            "Rescued bonded labourers are entitled to immediate relief of Rs 20,000, rehabilitation "
            "assistance of Rs 2-3 lakh, and ongoing support from the district administration."
        ),
        "key_laws": [
            "Minimum Wages Act, 1948",
            "Payment of Wages Act, 1936",
            "Bonded Labour System (Abolition) Act, 1976",
            "Inter-State Migrant Workmen Act, 1979",
            "Occupational Safety, Health and Working Conditions Code, 2020",
        ],
    },
    EmergencyType.ELDER_ABUSE: {
        "title": "Safety Plan for Elder Abuse",
        "immediate_steps": [
            "Call the Elder Helpline at 14567 immediately.",
            "If in physical danger, call 112 (police emergency).",
            "Contact a trusted family member, neighbour, or community elder.",
            "If possible, keep important documents (property papers, pension, Aadhaar) in a safe place.",
        ],
        "documentation_steps": [
            "Document instances of abuse with dates, times, and descriptions.",
            "Photograph any injuries or property damage.",
            "Keep copies of property documents and financial records.",
            "Get a medical examination if physically harmed.",
        ],
        "legal_steps": [
            "File a complaint under the Maintenance and Welfare of Parents and Senior Citizens Act, 2007.",
            "The Tribunal under this Act can order children/relatives to pay maintenance up to Rs 10,000/month.",
            "If evicted from your own property, the Act allows cancellation of property transfer made under coercion.",
            "File an FIR if there is physical violence.",
            "Contact DLSA (15100) for free legal aid -- senior citizens are entitled to free legal services.",
            "The District Magistrate can take action under the Act within 90 days.",
        ],
        "shelter_info": (
            "Old age homes and senior citizen welfare associations can provide temporary shelter. "
            "Contact the District Social Welfare Officer for government-funded homes."
        ),
        "key_laws": [
            "Maintenance and Welfare of Parents and Senior Citizens Act, 2007",
            "Section 125 CrPC -- Maintenance of parents",
            "Hindu Adoptions and Maintenance Act, 1956 (for Hindus)",
            "Indian Succession Act, 1925 -- property rights",
        ],
    },
}

# Default safety plan for types without a specific plan
_DEFAULT_SAFETY_PLAN: Final[dict[str, list[str] | str]] = {
    "title": "General Emergency Safety Plan",
    "immediate_steps": [
        "If in immediate physical danger, call 112 (Police/Emergency) RIGHT NOW.",
        "Move to a safe location -- a public place, police station, or trusted person's home.",
        "Inform a family member or friend about your situation and location.",
        "If injured, call 108 (Ambulance) for medical assistance.",
    ],
    "documentation_steps": [
        "Document everything: dates, times, names, and descriptions of what happened.",
        "Take photos or videos if safely possible.",
        "Get names and contact details of witnesses.",
        "Preserve all evidence -- messages, documents, photos.",
    ],
    "legal_steps": [
        "File an FIR at the nearest police station. You can also file a Zero FIR at any station.",
        "If police refuse to file FIR, send a written complaint to the Superintendent of Police by registered post.",
        "Contact NALSA (15100) for free legal aid.",
        "For human rights violations, file a complaint with NHRC at 14433 or nhrc.nic.in.",
        "Approach the nearest court (judicial magistrate) for urgent relief.",
    ],
    "shelter_info": (
        "Contact your District Legal Services Authority (DLSA) at 15100 for "
        "free legal aid, and the District Collector's office for emergency relief."
    ),
    "key_laws": [
        "Article 21 -- Right to Life and Personal Liberty",
        "Section 154 CrPC -- Mandatory registration of FIR for cognizable offences",
        "Legal Services Authorities Act, 1987 -- Right to free legal aid",
    ],
}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class EmergencyContact(BaseModel):
    """A single emergency contact with helpline details."""

    name: str
    number: str
    alternate_number: str | None = None
    description: str
    available: str = "24x7"
    is_toll_free: bool = False
    category: str = "national"  # "national", "state", "district", "ngo"
    website: str | None = None


class NearbyHelp(BaseModel):
    """A nearby help resource with approximate distance."""

    name: str
    type: str  # "police_station", "hospital", "one_stop_centre", "dlsa", "shelter"
    address: str | None = None
    phone: str | None = None
    approximate_distance_km: float | None = None
    operating_hours: str = "24x7"
    services_available: list[str] = Field(default_factory=list)


class SafetyPlan(BaseModel):
    """A structured safety plan for a specific emergency type."""

    plan_id: str = Field(default_factory=lambda: uuid4().hex)
    emergency_type: EmergencyType
    title: str
    immediate_steps: list[str] = Field(default_factory=list)
    documentation_steps: list[str] = Field(default_factory=list)
    legal_steps: list[str] = Field(default_factory=list)
    shelter_info: str = ""
    key_laws: list[str] = Field(default_factory=list)
    helpline_numbers: list[EmergencyContact] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    language: str = "en"


class EmergencyResponse(BaseModel):
    """Complete response to an emergency SOS request."""

    report_id: str = Field(default_factory=lambda: uuid4().hex)
    emergency_type: EmergencyType
    severity: str = "high"  # "critical", "high", "medium"
    primary_helpline: EmergencyContact
    all_contacts: list[EmergencyContact] = Field(default_factory=list)
    safety_plan: SafetyPlan
    distress_report: str = ""
    shareable_summary: str = ""
    location: str = ""
    language: str = "en"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    follow_up_required: bool = True


# ---------------------------------------------------------------------------
# Emergency SOS Service
# ---------------------------------------------------------------------------


class EmergencySOSService:
    """Emergency SOS legal distress system for HaqSetu.

    This is a LIFE-SAVING service. For many users in rural India, HaqSetu
    may be the ONLY way they discover the right helpline or legal aid.

    The service:
    1. Classifies the emergency from a natural language description.
    2. Returns the most relevant helpline numbers immediately.
    3. Generates a distress report that can be shared.
    4. Provides a step-by-step safety plan.
    5. Tracks reports for follow-up.

    All helpline data is curated from official government sources and
    verified periodically.

    Example usage::

        sos = EmergencySOSService()
        response = sos.report_emergency(
            description="My husband is beating me, please help",
            location="Lucknow, Uttar Pradesh",
            language="hi",
        )
        # response.primary_helpline.number => "181"
        # response.safety_plan.immediate_steps => [...]
    """

    __slots__ = ("_active_reports",)

    def __init__(self) -> None:
        # In-memory tracking of active emergency reports for follow-up.
        # Production would use a database.
        self._active_reports: dict[str, EmergencyResponse] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def report_emergency(
        self,
        description: str,
        location: str = "",
        language: str = "en",
    ) -> EmergencyResponse:
        """Report an emergency and get immediate help.

        This is the PRIMARY entry point. Given a natural language
        description of the emergency (in English or Hinglish), it:

        1. Classifies the emergency type.
        2. Looks up the right helpline numbers.
        3. Generates a safety plan.
        4. Creates a shareable distress report.

        Parameters
        ----------
        description:
            Natural language description of the emergency.
            Can be in English, Hindi, or Hinglish.
        location:
            Location of the person (city, state, or address).
        language:
            Preferred language code for the response.

        Returns
        -------
        EmergencyResponse
            Complete response with helplines, safety plan, and distress report.
        """
        # Step 1: Classify emergency
        emergency_type = self._classify_emergency(description)

        logger.info(
            "emergency_sos.report_received",
            emergency_type=emergency_type.value,
            location=location,
            language=language,
        )

        # Step 2: Get relevant contacts
        state = self._extract_state(location)
        contacts = self.get_emergency_contacts(emergency_type.value, state)

        # Step 3: Determine primary helpline (first and most important)
        primary_helpline = contacts[0] if contacts else EmergencyContact(
            name="Police Emergency",
            number="112",
            description="Single emergency number for police, fire, and ambulance",
            available="24x7",
        )

        # Step 4: Generate safety plan
        safety_plan = self.generate_safety_plan(emergency_type.value)
        safety_plan.helpline_numbers = contacts
        safety_plan.language = language

        # Step 5: Determine severity
        severity = self._assess_severity(emergency_type, description)

        # Step 6: Generate distress report
        distress_report = self._generate_distress_report(
            emergency_type=emergency_type,
            description=description,
            location=location,
            contacts=contacts,
        )

        # Step 7: Generate shareable summary
        shareable_summary = self._generate_shareable_summary(
            emergency_type=emergency_type,
            description=description,
            location=location,
            primary_helpline=primary_helpline,
        )

        # Build response
        response = EmergencyResponse(
            emergency_type=emergency_type,
            severity=severity,
            primary_helpline=primary_helpline,
            all_contacts=contacts,
            safety_plan=safety_plan,
            distress_report=distress_report,
            shareable_summary=shareable_summary,
            location=location,
            language=language,
        )

        # Track the report for follow-up
        self._active_reports[response.report_id] = response

        logger.info(
            "emergency_sos.report_created",
            report_id=response.report_id,
            emergency_type=emergency_type.value,
            severity=severity,
            primary_helpline=primary_helpline.number,
            total_contacts=len(contacts),
        )

        return response

    def get_emergency_contacts(
        self,
        emergency_type: str,
        state: str = "",
    ) -> list[EmergencyContact]:
        """Get relevant emergency contacts for a given emergency type and state.

        Returns contacts in priority order -- the most important helpline
        is first. Includes both national helplines and state-specific
        contacts (women's commission, DLSA).

        Parameters
        ----------
        emergency_type:
            The type of emergency (value from EmergencyType enum).
        state:
            Optional state name for state-specific contacts.

        Returns
        -------
        list[EmergencyContact]
            Emergency contacts sorted by relevance.
        """
        contacts: list[EmergencyContact] = []

        # Parse emergency type
        try:
            etype = EmergencyType(emergency_type)
        except ValueError:
            etype = EmergencyType.OTHER

        # Get relevant national helpline keys for this emergency type
        helpline_keys = _EMERGENCY_HELPLINE_MAP.get(etype, ["police_emergency", "nalsa"])

        for key in helpline_keys:
            helpline = _NATIONAL_HELPLINES.get(key)
            if helpline is None:
                continue

            contact = EmergencyContact(
                name=helpline["name"],
                number=helpline["number"],
                alternate_number=helpline.get("alternate"),
                description=helpline["description"],
                available=helpline.get("available", "24x7"),
                is_toll_free=helpline["number"].startswith("1") and len(helpline["number"]) <= 5,
                category="national",
            )
            contacts.append(contact)

        # Add state-specific contacts
        normalized_state = self._normalize_state(state)

        if normalized_state:
            # Women's commission (for relevant emergency types)
            women_types = {
                EmergencyType.DOMESTIC_VIOLENCE,
                EmergencyType.SEXUAL_ASSAULT,
                EmergencyType.DOWRY_HARASSMENT,
                EmergencyType.ACID_ATTACK,
                EmergencyType.TRAFFICKING,
            }
            if etype in women_types:
                commission = _STATE_WOMEN_COMMISSIONS.get(normalized_state)
                if commission:
                    contacts.append(EmergencyContact(
                        name=commission["name"],
                        number=commission["number"],
                        alternate_number=commission.get("alternate"),
                        description=f"State Women's Commission -- {commission.get('address', '')}",
                        available="9:30 AM - 5:30 PM, Mon-Fri",
                        category="state",
                        website=commission.get("website"),
                    ))

            # DLSA (for all emergency types that need legal aid)
            legal_types = {
                EmergencyType.DOMESTIC_VIOLENCE,
                EmergencyType.CHILD_ABUSE,
                EmergencyType.SEXUAL_ASSAULT,
                EmergencyType.POLICE_HARASSMENT,
                EmergencyType.CUSTODIAL_VIOLENCE,
                EmergencyType.CASTE_VIOLENCE,
                EmergencyType.ILLEGAL_DETENTION,
                EmergencyType.LABOUR_EXPLOITATION,
                EmergencyType.LAND_GRABBING,
                EmergencyType.ELDER_ABUSE,
                EmergencyType.DOWRY_HARASSMENT,
                EmergencyType.TRAFFICKING,
            }
            if etype in legal_types:
                dlsa = _STATE_DLSA.get(normalized_state)
                if dlsa:
                    contacts.append(EmergencyContact(
                        name=dlsa["name"],
                        number=dlsa["number"],
                        description="State Legal Services Authority -- FREE legal aid for SC/ST, women, children, disabled, poor",
                        available="9:30 AM - 5:30 PM, Mon-Fri",
                        category="state",
                        website=dlsa.get("website"),
                    ))

        logger.info(
            "emergency_sos.contacts_retrieved",
            emergency_type=emergency_type,
            state=state or "not_specified",
            total_contacts=len(contacts),
        )

        return contacts

    def generate_safety_plan(self, situation: str) -> SafetyPlan:
        """Generate a step-by-step safety plan for a given situation.

        The safety plan includes:
        - Immediate steps to take RIGHT NOW
        - How to document evidence
        - Legal steps and rights
        - Shelter/support information
        - Key applicable laws

        Parameters
        ----------
        situation:
            Either an EmergencyType value or a free-text description.

        Returns
        -------
        SafetyPlan
            Comprehensive, actionable safety plan.
        """
        # Try to parse as EmergencyType first
        try:
            etype = EmergencyType(situation)
        except ValueError:
            # It is a free-text description; classify it
            etype = self._classify_emergency(situation)

        # Look up the safety plan template
        plan_data = _SAFETY_PLANS.get(etype, _DEFAULT_SAFETY_PLAN)

        plan = SafetyPlan(
            emergency_type=etype,
            title=str(plan_data.get("title", "Emergency Safety Plan")),
            immediate_steps=list(plan_data.get("immediate_steps", [])),
            documentation_steps=list(plan_data.get("documentation_steps", [])),
            legal_steps=list(plan_data.get("legal_steps", [])),
            shelter_info=str(plan_data.get("shelter_info", "")),
            key_laws=list(plan_data.get("key_laws", [])),
        )

        logger.info(
            "emergency_sos.safety_plan_generated",
            emergency_type=etype.value,
            immediate_steps_count=len(plan.immediate_steps),
            legal_steps_count=len(plan.legal_steps),
        )

        return plan

    def get_nearest_help(
        self,
        latitude: float,
        longitude: float,
        emergency_type: str,
    ) -> list[NearbyHelp]:
        """Get nearest help resources based on location and emergency type.

        In production, this would query a geospatial database of police
        stations, hospitals, One Stop Centres, DLSAs, and shelters.
        Currently returns guidance on how to find nearest resources
        using well-known systems.

        Parameters
        ----------
        latitude:
            GPS latitude of the user.
        longitude:
            GPS longitude of the user.
        emergency_type:
            The type of emergency.

        Returns
        -------
        list[NearbyHelp]
            Nearby help resources with contact information.
        """
        try:
            etype = EmergencyType(emergency_type)
        except ValueError:
            etype = EmergencyType.OTHER

        resources: list[NearbyHelp] = []

        # Always include police station
        resources.append(NearbyHelp(
            name="Nearest Police Station",
            type="police_station",
            address=f"Dial 112 and share your location (Lat: {latitude:.4f}, Lon: {longitude:.4f})",
            phone="112",
            services_available=["FIR registration", "Emergency response", "Protection"],
        ))

        # Always include hospital/ambulance
        resources.append(NearbyHelp(
            name="Nearest Government Hospital",
            type="hospital",
            address="Dial 108 for ambulance -- share your GPS location",
            phone="108",
            services_available=[
                "Emergency medical care",
                "Medico-Legal Case (MLC) report",
                "Free treatment for accident/assault victims",
            ],
        ))

        # Emergency type specific resources
        women_types = {
            EmergencyType.DOMESTIC_VIOLENCE,
            EmergencyType.SEXUAL_ASSAULT,
            EmergencyType.DOWRY_HARASSMENT,
            EmergencyType.ACID_ATTACK,
            EmergencyType.TRAFFICKING,
        }
        if etype in women_types:
            resources.append(NearbyHelp(
                name="Nearest One Stop Centre (Sakhi Centre)",
                type="one_stop_centre",
                address="Call 181 for the nearest centre. 700+ OSCs across India.",
                phone="181",
                services_available=[
                    "Emergency shelter",
                    "Medical aid",
                    "Free legal counselling",
                    "Psycho-social support",
                    "Police facilitation",
                ],
            ))
            resources.append(NearbyHelp(
                name="Nearest Mahila Thana (Women's Police Station)",
                type="police_station",
                address="Call 181 or 112 to locate the nearest Women's Police Station.",
                phone="181",
                services_available=["Women-specific FIR handling", "Counselling"],
            ))

        if etype == EmergencyType.CHILD_ABUSE:
            resources.append(NearbyHelp(
                name="Nearest Child Welfare Committee (CWC)",
                type="shelter",
                address="Call Childline 1098 to be connected to the nearest CWC.",
                phone="1098",
                services_available=[
                    "Child rescue",
                    "Temporary shelter",
                    "Rehabilitation",
                    "Legal protection order",
                ],
            ))

        # Legal aid resources
        legal_types = {
            EmergencyType.POLICE_HARASSMENT,
            EmergencyType.CUSTODIAL_VIOLENCE,
            EmergencyType.CASTE_VIOLENCE,
            EmergencyType.ILLEGAL_DETENTION,
            EmergencyType.LAND_GRABBING,
            EmergencyType.LABOUR_EXPLOITATION,
            EmergencyType.ELDER_ABUSE,
        }
        if etype in legal_types or etype in women_types:
            resources.append(NearbyHelp(
                name="District Legal Services Authority (DLSA)",
                type="dlsa",
                address="Located at the district court complex. Call 15100 for your nearest DLSA.",
                phone="15100",
                operating_hours="9:30 AM - 5:30 PM, Mon-Fri",
                services_available=[
                    "Free legal aid lawyer",
                    "Lok Adalat for quick resolution",
                    "Legal awareness",
                    "Victim compensation facilitation",
                ],
            ))

        logger.info(
            "emergency_sos.nearest_help_retrieved",
            latitude=latitude,
            longitude=longitude,
            emergency_type=emergency_type,
            resources_count=len(resources),
        )

        return resources

    # ------------------------------------------------------------------
    # Report tracking
    # ------------------------------------------------------------------

    def get_report(self, report_id: str) -> EmergencyResponse | None:
        """Retrieve an existing emergency report by its ID.

        Parameters
        ----------
        report_id:
            The unique report identifier.

        Returns
        -------
        EmergencyResponse | None
            The report if found, otherwise None.
        """
        return self._active_reports.get(report_id)

    def get_all_active_reports(self) -> list[EmergencyResponse]:
        """Get all active emergency reports that need follow-up.

        Returns
        -------
        list[EmergencyResponse]
            All reports where ``follow_up_required`` is True.
        """
        return [
            report for report in self._active_reports.values()
            if report.follow_up_required
        ]

    def mark_followed_up(self, report_id: str) -> bool:
        """Mark an emergency report as followed up.

        Parameters
        ----------
        report_id:
            The unique report identifier.

        Returns
        -------
        bool
            True if the report was found and updated, False otherwise.
        """
        report = self._active_reports.get(report_id)
        if report is not None:
            report.follow_up_required = False
            logger.info("emergency_sos.report_followed_up", report_id=report_id)
            return True
        return False

    @property
    def active_report_count(self) -> int:
        """Number of active reports pending follow-up."""
        return sum(1 for r in self._active_reports.values() if r.follow_up_required)

    # ------------------------------------------------------------------
    # Internal: Emergency classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_emergency(description: str) -> EmergencyType:
        """Classify an emergency from a natural language description.

        Uses keyword matching against a comprehensive dictionary of
        terms in English and Hinglish. Falls back to EmergencyType.OTHER
        if no keywords match.

        The keyword map includes common Hindi/Hinglish terms so that
        rural users speaking in their language get classified correctly.

        Parameters
        ----------
        description:
            Natural language description of the emergency.

        Returns
        -------
        EmergencyType
            The classified emergency type.
        """
        description_lower = description.lower().strip()

        # Check each keyword against the description
        # Use longest-match-first to avoid false positives
        # (e.g., "child trafficking" should match CHILD_ABUSE not TRAFFICKING)
        best_match: EmergencyType | None = None
        best_match_len = 0

        for keyword, etype in _EMERGENCY_KEYWORDS.items():
            if keyword in description_lower and len(keyword) > best_match_len:
                best_match = etype
                best_match_len = len(keyword)

        if best_match is not None:
            return best_match

        # Fallback heuristics for very short or ambiguous descriptions
        danger_words = {"help", "danger", "urgent", "emergency", "bachao", "madad", "sahayata"}
        if any(word in description_lower.split() for word in danger_words):
            return EmergencyType.OTHER

        return EmergencyType.OTHER

    @staticmethod
    def _assess_severity(
        emergency_type: EmergencyType, description: str
    ) -> str:
        """Assess the severity of an emergency.

        Returns "critical", "high", or "medium".

        Critical: Life-threatening situations requiring immediate response.
        High: Serious situations requiring urgent intervention.
        Medium: Situations that need help but are not immediately life-threatening.

        Parameters
        ----------
        emergency_type:
            The classified emergency type.
        description:
            The original description for additional context.

        Returns
        -------
        str
            Severity level: "critical", "high", or "medium".
        """
        critical_types = {
            EmergencyType.SEXUAL_ASSAULT,
            EmergencyType.ACID_ATTACK,
            EmergencyType.ROAD_ACCIDENT,
            EmergencyType.MEDICAL_EMERGENCY,
            EmergencyType.FIRE,
            EmergencyType.NATURAL_DISASTER,
            EmergencyType.CUSTODIAL_VIOLENCE,
            EmergencyType.TRAFFICKING,
        }

        high_types = {
            EmergencyType.DOMESTIC_VIOLENCE,
            EmergencyType.CHILD_ABUSE,
            EmergencyType.POLICE_HARASSMENT,
            EmergencyType.DOWRY_HARASSMENT,
            EmergencyType.CASTE_VIOLENCE,
            EmergencyType.COMMUNAL_VIOLENCE,
            EmergencyType.ILLEGAL_DETENTION,
        }

        if emergency_type in critical_types:
            return "critical"

        # Check for critical keywords even in non-critical types
        description_lower = description.lower()
        critical_keywords = {
            "dying", "bleeding", "unconscious", "mar diya", "jaan ka khatra",
            "gun", "knife", "weapon", "choking", "not breathing",
        }
        if any(kw in description_lower for kw in critical_keywords):
            return "critical"

        if emergency_type in high_types:
            return "high"

        return "medium"

    # ------------------------------------------------------------------
    # Internal: State extraction and normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_state(location: str) -> str:
        """Extract the state name from a location string.

        Parameters
        ----------
        location:
            Free-text location like "Lucknow, Uttar Pradesh" or "Mumbai".

        Returns
        -------
        str
            Extracted state name, or empty string if not identified.
        """
        if not location:
            return ""

        location_lower = location.lower().strip()

        # Try to match against known state aliases
        for alias, state_key in _STATE_ALIASES.items():
            if alias in location_lower:
                return state_key

        return ""

    @staticmethod
    def _normalize_state(state: str) -> str:
        """Normalize a state name to its canonical key.

        Parameters
        ----------
        state:
            State name in any recognized format.

        Returns
        -------
        str
            Canonical state key (e.g., "uttar_pradesh"), or empty string.
        """
        if not state:
            return ""

        state_lower = state.lower().strip().replace("-", "_")

        # Direct match
        if state_lower in _STATE_WOMEN_COMMISSIONS:
            return state_lower

        # Alias match
        return _STATE_ALIASES.get(state_lower, "")

    # ------------------------------------------------------------------
    # Internal: Report generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_distress_report(
        emergency_type: EmergencyType,
        description: str,
        location: str,
        contacts: list[EmergencyContact],
    ) -> str:
        """Generate a formal distress report that can be shared with authorities.

        The report includes:
        - Emergency classification
        - Description of the situation
        - Location
        - Recommended helplines
        - Timestamp

        This report can be shared with police, lawyers, NGOs, or family.

        Parameters
        ----------
        emergency_type:
            Classified emergency type.
        description:
            User's description of the emergency.
        location:
            Location of the emergency.
        contacts:
            Recommended emergency contacts.

        Returns
        -------
        str
            Formatted distress report text.
        """
        now = datetime.now(UTC)
        timestamp = now.strftime("%d %B %Y, %I:%M %p IST")

        contact_lines = []
        for c in contacts[:5]:
            line = f"  - {c.name}: {c.number}"
            if c.alternate_number:
                line += f" (Alt: {c.alternate_number})"
            contact_lines.append(line)

        report = (
            f"EMERGENCY DISTRESS REPORT\n"
            f"{'=' * 40}\n"
            f"Generated via HaqSetu Emergency SOS\n"
            f"Date/Time: {timestamp}\n"
            f"Report Type: {emergency_type.value.replace('_', ' ').title()}\n"
            f"Location: {location or 'Not specified'}\n"
            f"\n"
            f"DESCRIPTION:\n"
            f"{description}\n"
            f"\n"
            f"EMERGENCY CONTACTS:\n"
            f"{chr(10).join(contact_lines)}\n"
            f"\n"
            f"IMPORTANT:\n"
            f"  - This report can be presented to police as preliminary information.\n"
            f"  - File an FIR at the nearest police station immediately.\n"
            f"  - For free legal aid, call NALSA at 15100.\n"
            f"  - Keep a copy of this report for your records.\n"
            f"{'=' * 40}\n"
        )

        return report

    @staticmethod
    def _generate_shareable_summary(
        emergency_type: EmergencyType,
        description: str,
        location: str,
        primary_helpline: EmergencyContact,
    ) -> str:
        """Generate a short, shareable summary for SMS/WhatsApp.

        Designed to be concise enough for SMS (under 160 chars for the
        core message) while still being actionable.

        Parameters
        ----------
        emergency_type:
            Classified emergency type.
        description:
            User's description (will be truncated).
        location:
            Location of the emergency.
        primary_helpline:
            The primary recommended helpline.

        Returns
        -------
        str
            Short, shareable message.
        """
        type_label = emergency_type.value.replace("_", " ").title()
        short_desc = description[:80] + "..." if len(description) > 80 else description
        location_text = location if location else "Location not specified"

        return (
            f"SOS via HaqSetu: {type_label}\n"
            f"Situation: {short_desc}\n"
            f"Location: {location_text}\n"
            f"CALL: {primary_helpline.number} ({primary_helpline.name})\n"
            f"Free legal aid: 15100 (NALSA)"
        )
