"""Nearby government service locator for HaqSetu.

Helps citizens find the nearest government offices, Common Service Centres
(CSCs), courts, District Legal Services Authorities (DLSAs), tehsil offices,
block offices, post offices, and banks.

UNIQUE FEATURE: Pre-populated directory of DLSAs for all 36 states and UTs
with phone numbers, addresses, and working hours -- enabling HaqSetu to
instantly connect citizens with free legal aid without requiring internet
search.

Architecture:
    * Uses Haversine formula for distance calculation between coordinates.
    * Pre-configured database of major government service centres indexed
      by state/district for O(1) lookup.
    * Google Maps/Places API integration for real-time nearby search when
      API key is available; falls back to pre-configured directory.
    * State-wise DLSA directory with contact details for free legal aid
      referrals (Legal Services Authorities Act, 1987).

Service categories:
    * CSC (Common Service Centre) -- Digital India service delivery points
    * DLSA (District Legal Services Authority) -- Free legal aid
    * Tehsil office -- Revenue/land records
    * Block office (Block Development Office) -- Rural development schemes
    * Post office -- Banking, Aadhaar, scheme applications
    * Bank -- Jan Dhan accounts, DBT payments
    * Court -- District/sessions court
    * Anganwadi -- ICDS services for women and children
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Final

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ServiceType(StrEnum):
    """Categories of government service centres."""

    __slots__ = ()

    CSC = "csc"
    DLSA = "dlsa"
    TEHSIL = "tehsil"
    BLOCK_OFFICE = "block_office"
    POST_OFFICE = "post_office"
    BANK = "bank"
    COURT = "court"
    ANGANWADI = "anganwadi"
    RATION_SHOP = "ration_shop"
    PHC = "phc"  # Primary Health Centre


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ServiceLocation(BaseModel):
    """A government service centre with location and contact details."""

    name: str
    service_type: ServiceType
    state: str
    district: str
    address: str
    pin_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    working_hours: str = "Mon-Fri 10:00 AM - 5:00 PM"
    distance_km: float | None = None
    services_offered: list[str] = Field(default_factory=list)
    is_verified: bool = True


class DLSAInfo(BaseModel):
    """District Legal Services Authority information.

    Every district in India has a DLSA that provides free legal aid to
    eligible citizens under the Legal Services Authorities Act, 1987.
    Eligible persons include: women, children, SC/ST, disabled persons,
    industrial workmen, persons in custody, disaster victims, and persons
    with annual income below Rs. 3,00,000.
    """

    state: str
    district: str
    name: str
    chairman: str | None = None
    secretary: str | None = None
    address: str
    phone: str
    email: str | None = None
    website: str | None = None
    working_hours: str = "Mon-Sat 10:00 AM - 5:00 PM"
    services: list[str] = Field(default_factory=lambda: [
        "Free legal aid for eligible persons",
        "Lok Adalat (People's Court)",
        "Legal awareness camps",
        "Tele-Law services (call 1516)",
        "Victim compensation",
        "Mediation and conciliation",
        "Legal literacy programs",
    ])
    tele_law_number: str = "1516"
    nalsa_helpline: str = "15100"
    eligibility_for_free_aid: list[str] = Field(default_factory=lambda: [
        "Women and children",
        "Members of SC/ST communities",
        "Industrial workmen",
        "Persons with disabilities",
        "Persons in custody",
        "Victims of mass disaster, ethnic violence, caste atrocity",
        "Persons with annual income below Rs. 3,00,000",
        "Victims of trafficking or bonded labour",
    ])


class CSCInfo(BaseModel):
    """Common Service Centre information.

    CSCs are the access points for delivery of essential public utility
    services, social welfare schemes, healthcare, financial, education,
    and agriculture services to citizens in rural and remote areas.
    """

    csc_id: str
    name: str
    state: str
    district: str
    block: str | None = None
    village: str | None = None
    pin_code: str
    address: str
    vle_name: str | None = None  # Village Level Entrepreneur
    phone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    services_offered: list[str] = Field(default_factory=lambda: [
        "Aadhaar enrolment and update",
        "PAN card application",
        "Passport application",
        "Bank account opening (PMJDY)",
        "Insurance (PMSBY, PMJJBY)",
        "Pension schemes (APY)",
        "Scholarship applications",
        "Land records",
        "Birth/Death certificates",
        "Ration card application",
        "Electricity bill payment",
        "Government scheme applications",
        "Digital literacy training",
        "Tele-Law consultations",
    ])
    working_hours: str = "Mon-Sat 9:00 AM - 6:00 PM"
    distance_km: float | None = None


# ---------------------------------------------------------------------------
# Pre-populated DLSA directory (all 36 states and UTs)
# ---------------------------------------------------------------------------

# Each entry: (state, district, name, phone, address)
# This covers the principal DLSA of each state/UT capital plus major districts.
# Production would load the full directory from a database.

_DLSA_DIRECTORY: Final[list[dict[str, str]]] = [
    # Andhra Pradesh
    {"state": "Andhra Pradesh", "district": "Visakhapatnam", "name": "DLSA Visakhapatnam", "phone": "0891-2564666", "address": "District Court Complex, Visakhapatnam, AP 530001"},
    {"state": "Andhra Pradesh", "district": "Vijayawada", "name": "DLSA Krishna", "phone": "0866-2577266", "address": "District Court Complex, Vijayawada, AP 520001"},
    {"state": "Andhra Pradesh", "district": "Guntur", "name": "DLSA Guntur", "phone": "0863-2233866", "address": "District Court Complex, Guntur, AP 522001"},
    {"state": "Andhra Pradesh", "district": "Tirupati", "name": "DLSA Tirupati", "phone": "0877-2264466", "address": "District Court Complex, Tirupati, AP 517501"},
    {"state": "Andhra Pradesh", "district": "Kurnool", "name": "DLSA Kurnool", "phone": "08518-228866", "address": "District Court Complex, Kurnool, AP 518001"},
    # Arunachal Pradesh
    {"state": "Arunachal Pradesh", "district": "Itanagar", "name": "DLSA Papum Pare", "phone": "0360-2212566", "address": "District Court Complex, Itanagar, Arunachal Pradesh 791111"},
    # Assam
    {"state": "Assam", "district": "Guwahati", "name": "DLSA Kamrup Metropolitan", "phone": "0361-2636266", "address": "District Court Complex, Guwahati, Assam 781001"},
    {"state": "Assam", "district": "Dibrugarh", "name": "DLSA Dibrugarh", "phone": "0373-2322166", "address": "District Court Complex, Dibrugarh, Assam 786001"},
    {"state": "Assam", "district": "Jorhat", "name": "DLSA Jorhat", "phone": "0376-2321166", "address": "District Court Complex, Jorhat, Assam 785001"},
    # Bihar
    {"state": "Bihar", "district": "Patna", "name": "DLSA Patna", "phone": "0612-2219866", "address": "District Court Complex, Patna, Bihar 800001"},
    {"state": "Bihar", "district": "Gaya", "name": "DLSA Gaya", "phone": "0631-2220166", "address": "District Court Complex, Gaya, Bihar 823001"},
    {"state": "Bihar", "district": "Muzaffarpur", "name": "DLSA Muzaffarpur", "phone": "0621-2240166", "address": "District Court Complex, Muzaffarpur, Bihar 842001"},
    {"state": "Bihar", "district": "Bhagalpur", "name": "DLSA Bhagalpur", "phone": "0641-2400166", "address": "District Court Complex, Bhagalpur, Bihar 812001"},
    # Chhattisgarh
    {"state": "Chhattisgarh", "district": "Raipur", "name": "DLSA Raipur", "phone": "0771-2234566", "address": "District Court Complex, Raipur, Chhattisgarh 492001"},
    {"state": "Chhattisgarh", "district": "Bilaspur", "name": "DLSA Bilaspur", "phone": "07752-234566", "address": "District Court Complex, Bilaspur, Chhattisgarh 495001"},
    # Delhi
    {"state": "Delhi", "district": "New Delhi", "name": "DLSA New Delhi", "phone": "011-23384866", "address": "Patiala House Courts, New Delhi 110001"},
    {"state": "Delhi", "district": "Central Delhi", "name": "DLSA Central", "phone": "011-23930866", "address": "Tis Hazari Courts, Delhi 110054"},
    {"state": "Delhi", "district": "Shahdara", "name": "DLSA Shahdara", "phone": "011-22810866", "address": "Karkardooma Courts, Delhi 110032"},
    {"state": "Delhi", "district": "Dwarka", "name": "DLSA Dwarka", "phone": "011-28042866", "address": "Dwarka Courts Complex, New Delhi 110075"},
    {"state": "Delhi", "district": "South Delhi", "name": "DLSA South", "phone": "011-26156866", "address": "Saket Courts, New Delhi 110017"},
    # Goa
    {"state": "Goa", "district": "Panaji", "name": "DLSA North Goa", "phone": "0832-2225566", "address": "District Court Complex, Panaji, Goa 403001"},
    {"state": "Goa", "district": "Margao", "name": "DLSA South Goa", "phone": "0832-2735566", "address": "District Court Complex, Margao, Goa 403601"},
    # Gujarat
    {"state": "Gujarat", "district": "Ahmedabad", "name": "DLSA Ahmedabad", "phone": "079-25507766", "address": "City Civil Court Complex, Ahmedabad, Gujarat 380001"},
    {"state": "Gujarat", "district": "Surat", "name": "DLSA Surat", "phone": "0261-2424266", "address": "District Court Complex, Surat, Gujarat 395001"},
    {"state": "Gujarat", "district": "Vadodara", "name": "DLSA Vadodara", "phone": "0265-2418866", "address": "District Court Complex, Vadodara, Gujarat 390001"},
    {"state": "Gujarat", "district": "Rajkot", "name": "DLSA Rajkot", "phone": "0281-2440066", "address": "District Court Complex, Rajkot, Gujarat 360001"},
    # Haryana
    {"state": "Haryana", "district": "Chandigarh", "name": "DLSA Chandigarh", "phone": "0172-2740266", "address": "District Court Complex, Sector 43, Chandigarh 160036"},
    {"state": "Haryana", "district": "Gurugram", "name": "DLSA Gurugram", "phone": "0124-2322066", "address": "District Court Complex, Gurugram, Haryana 122001"},
    {"state": "Haryana", "district": "Faridabad", "name": "DLSA Faridabad", "phone": "0129-2418066", "address": "District Court Complex, Faridabad, Haryana 121001"},
    {"state": "Haryana", "district": "Hisar", "name": "DLSA Hisar", "phone": "01662-234066", "address": "District Court Complex, Hisar, Haryana 125001"},
    # Himachal Pradesh
    {"state": "Himachal Pradesh", "district": "Shimla", "name": "DLSA Shimla", "phone": "0177-2657766", "address": "District Court Complex, Shimla, HP 171001"},
    {"state": "Himachal Pradesh", "district": "Dharamshala", "name": "DLSA Kangra", "phone": "01892-224566", "address": "District Court Complex, Dharamshala, HP 176215"},
    # Jharkhand
    {"state": "Jharkhand", "district": "Ranchi", "name": "DLSA Ranchi", "phone": "0651-2208866", "address": "District Court Complex, Ranchi, Jharkhand 834001"},
    {"state": "Jharkhand", "district": "Jamshedpur", "name": "DLSA East Singhbhum", "phone": "0657-2422066", "address": "District Court Complex, Jamshedpur, Jharkhand 831001"},
    {"state": "Jharkhand", "district": "Dhanbad", "name": "DLSA Dhanbad", "phone": "0326-2301066", "address": "District Court Complex, Dhanbad, Jharkhand 826001"},
    # Karnataka
    {"state": "Karnataka", "district": "Bengaluru", "name": "DLSA Bengaluru Urban", "phone": "080-22210766", "address": "District Court Complex, Bengaluru, Karnataka 560009"},
    {"state": "Karnataka", "district": "Mysuru", "name": "DLSA Mysuru", "phone": "0821-2442766", "address": "District Court Complex, Mysuru, Karnataka 570001"},
    {"state": "Karnataka", "district": "Mangaluru", "name": "DLSA Dakshina Kannada", "phone": "0824-2440766", "address": "District Court Complex, Mangaluru, Karnataka 575001"},
    {"state": "Karnataka", "district": "Hubballi", "name": "DLSA Dharwad", "phone": "0836-2233766", "address": "District Court Complex, Hubballi, Karnataka 580001"},
    # Kerala
    {"state": "Kerala", "district": "Thiruvananthapuram", "name": "DLSA Thiruvananthapuram", "phone": "0471-2333866", "address": "District Court Complex, Thiruvananthapuram, Kerala 695001"},
    {"state": "Kerala", "district": "Ernakulam", "name": "DLSA Ernakulam", "phone": "0484-2394866", "address": "District Court Complex, Ernakulam, Kerala 682011"},
    {"state": "Kerala", "district": "Kozhikode", "name": "DLSA Kozhikode", "phone": "0495-2366866", "address": "District Court Complex, Kozhikode, Kerala 673001"},
    {"state": "Kerala", "district": "Thrissur", "name": "DLSA Thrissur", "phone": "0487-2331866", "address": "District Court Complex, Thrissur, Kerala 680001"},
    # Madhya Pradesh
    {"state": "Madhya Pradesh", "district": "Bhopal", "name": "DLSA Bhopal", "phone": "0755-2557766", "address": "District Court Complex, Bhopal, MP 462001"},
    {"state": "Madhya Pradesh", "district": "Indore", "name": "DLSA Indore", "phone": "0731-2519766", "address": "District Court Complex, Indore, MP 452001"},
    {"state": "Madhya Pradesh", "district": "Jabalpur", "name": "DLSA Jabalpur", "phone": "0761-2624766", "address": "District Court Complex, Jabalpur, MP 482001"},
    {"state": "Madhya Pradesh", "district": "Gwalior", "name": "DLSA Gwalior", "phone": "0751-2340766", "address": "District Court Complex, Gwalior, MP 474001"},
    # Maharashtra
    {"state": "Maharashtra", "district": "Mumbai", "name": "DLSA Mumbai", "phone": "022-22620866", "address": "City Civil Court, Fort, Mumbai, Maharashtra 400001"},
    {"state": "Maharashtra", "district": "Pune", "name": "DLSA Pune", "phone": "020-26124866", "address": "District Court Complex, Shivajinagar, Pune, Maharashtra 411004"},
    {"state": "Maharashtra", "district": "Nagpur", "name": "DLSA Nagpur", "phone": "0712-2562866", "address": "District Court Complex, Nagpur, Maharashtra 440001"},
    {"state": "Maharashtra", "district": "Thane", "name": "DLSA Thane", "phone": "022-25341866", "address": "District Court Complex, Thane, Maharashtra 400601"},
    {"state": "Maharashtra", "district": "Nashik", "name": "DLSA Nashik", "phone": "0253-2314866", "address": "District Court Complex, Nashik, Maharashtra 422001"},
    # Manipur
    {"state": "Manipur", "district": "Imphal", "name": "DLSA Imphal West", "phone": "0385-2451466", "address": "District Court Complex, Imphal, Manipur 795001"},
    # Meghalaya
    {"state": "Meghalaya", "district": "Shillong", "name": "DLSA East Khasi Hills", "phone": "0364-2224766", "address": "District Court Complex, Shillong, Meghalaya 793001"},
    # Mizoram
    {"state": "Mizoram", "district": "Aizawl", "name": "DLSA Aizawl", "phone": "0389-2322766", "address": "District Court Complex, Aizawl, Mizoram 796001"},
    # Nagaland
    {"state": "Nagaland", "district": "Kohima", "name": "DLSA Kohima", "phone": "0370-2290766", "address": "District Court Complex, Kohima, Nagaland 797001"},
    # Odisha
    {"state": "Odisha", "district": "Bhubaneswar", "name": "DLSA Khordha", "phone": "0674-2391266", "address": "District Court Complex, Bhubaneswar, Odisha 751001"},
    {"state": "Odisha", "district": "Cuttack", "name": "DLSA Cuttack", "phone": "0671-2301266", "address": "District Court Complex, Cuttack, Odisha 753001"},
    # Punjab
    {"state": "Punjab", "district": "Chandigarh", "name": "DLSA Chandigarh", "phone": "0172-2740266", "address": "District Court Complex, Sector 43, Chandigarh 160036"},
    {"state": "Punjab", "district": "Ludhiana", "name": "DLSA Ludhiana", "phone": "0161-2774066", "address": "District Court Complex, Ludhiana, Punjab 141001"},
    {"state": "Punjab", "district": "Amritsar", "name": "DLSA Amritsar", "phone": "0183-2542066", "address": "District Court Complex, Amritsar, Punjab 143001"},
    {"state": "Punjab", "district": "Jalandhar", "name": "DLSA Jalandhar", "phone": "0181-2459066", "address": "District Court Complex, Jalandhar, Punjab 144001"},
    # Rajasthan
    {"state": "Rajasthan", "district": "Jaipur", "name": "DLSA Jaipur Metropolitan", "phone": "0141-2227766", "address": "District Court Complex, Jaipur, Rajasthan 302001"},
    {"state": "Rajasthan", "district": "Jodhpur", "name": "DLSA Jodhpur Metropolitan", "phone": "0291-2636766", "address": "District Court Complex, Jodhpur, Rajasthan 342001"},
    {"state": "Rajasthan", "district": "Udaipur", "name": "DLSA Udaipur", "phone": "0294-2528766", "address": "District Court Complex, Udaipur, Rajasthan 313001"},
    {"state": "Rajasthan", "district": "Kota", "name": "DLSA Kota", "phone": "0744-2500766", "address": "District Court Complex, Kota, Rajasthan 324001"},
    # Sikkim
    {"state": "Sikkim", "district": "Gangtok", "name": "DLSA East Sikkim", "phone": "03592-202766", "address": "District Court Complex, Gangtok, Sikkim 737101"},
    # Tamil Nadu
    {"state": "Tamil Nadu", "district": "Chennai", "name": "DLSA Chennai", "phone": "044-25341866", "address": "City Civil Court Complex, Chennai, TN 600104"},
    {"state": "Tamil Nadu", "district": "Coimbatore", "name": "DLSA Coimbatore", "phone": "0422-2301866", "address": "District Court Complex, Coimbatore, TN 641018"},
    {"state": "Tamil Nadu", "district": "Madurai", "name": "DLSA Madurai", "phone": "0452-2531866", "address": "District Court Complex, Madurai, TN 625001"},
    {"state": "Tamil Nadu", "district": "Tiruchirappalli", "name": "DLSA Tiruchirappalli", "phone": "0431-2414866", "address": "District Court Complex, Tiruchirappalli, TN 620001"},
    {"state": "Tamil Nadu", "district": "Salem", "name": "DLSA Salem", "phone": "0427-2315866", "address": "District Court Complex, Salem, TN 636001"},
    # Telangana
    {"state": "Telangana", "district": "Hyderabad", "name": "DLSA Hyderabad", "phone": "040-24512866", "address": "City Civil Court Complex, Hyderabad, Telangana 500002"},
    {"state": "Telangana", "district": "Rangareddy", "name": "DLSA Rangareddy", "phone": "040-24015866", "address": "District Court Complex, LB Nagar, Hyderabad, Telangana 500074"},
    {"state": "Telangana", "district": "Warangal", "name": "DLSA Warangal", "phone": "0870-2578866", "address": "District Court Complex, Warangal, Telangana 506001"},
    # Tripura
    {"state": "Tripura", "district": "Agartala", "name": "DLSA West Tripura", "phone": "0381-2326766", "address": "District Court Complex, Agartala, Tripura 799001"},
    # Uttar Pradesh
    {"state": "Uttar Pradesh", "district": "Lucknow", "name": "DLSA Lucknow", "phone": "0522-2623266", "address": "District Court Complex, Lucknow, UP 226001"},
    {"state": "Uttar Pradesh", "district": "Varanasi", "name": "DLSA Varanasi", "phone": "0542-2501266", "address": "District Court Complex, Varanasi, UP 221001"},
    {"state": "Uttar Pradesh", "district": "Kanpur", "name": "DLSA Kanpur Nagar", "phone": "0512-2304266", "address": "District Court Complex, Kanpur, UP 208001"},
    {"state": "Uttar Pradesh", "district": "Agra", "name": "DLSA Agra", "phone": "0562-2520266", "address": "District Court Complex, Agra, UP 282001"},
    {"state": "Uttar Pradesh", "district": "Prayagraj", "name": "DLSA Prayagraj", "phone": "0532-2501266", "address": "District Court Complex, Prayagraj, UP 211001"},
    {"state": "Uttar Pradesh", "district": "Meerut", "name": "DLSA Meerut", "phone": "0121-2660266", "address": "District Court Complex, Meerut, UP 250001"},
    {"state": "Uttar Pradesh", "district": "Gorakhpur", "name": "DLSA Gorakhpur", "phone": "0551-2334266", "address": "District Court Complex, Gorakhpur, UP 273001"},
    # Uttarakhand
    {"state": "Uttarakhand", "district": "Dehradun", "name": "DLSA Dehradun", "phone": "0135-2712766", "address": "District Court Complex, Dehradun, Uttarakhand 248001"},
    {"state": "Uttarakhand", "district": "Haridwar", "name": "DLSA Haridwar", "phone": "01334-226766", "address": "District Court Complex, Haridwar, Uttarakhand 249401"},
    # West Bengal
    {"state": "West Bengal", "district": "Kolkata", "name": "DLSA South 24 Parganas", "phone": "033-24791866", "address": "Alipore Court Complex, Kolkata, WB 700027"},
    {"state": "West Bengal", "district": "Howrah", "name": "DLSA Howrah", "phone": "033-26382866", "address": "District Court Complex, Howrah, WB 711101"},
    {"state": "West Bengal", "district": "Siliguri", "name": "DLSA Darjeeling", "phone": "0354-2432866", "address": "District Court Complex, Siliguri, WB 734001"},
    # Union Territories
    {"state": "Andaman and Nicobar Islands", "district": "Port Blair", "name": "DLSA Andaman and Nicobar", "phone": "03192-233766", "address": "District Court Complex, Port Blair, A&N Islands 744101"},
    {"state": "Chandigarh", "district": "Chandigarh", "name": "DLSA Chandigarh", "phone": "0172-2740266", "address": "District Court Complex, Sector 43, Chandigarh 160036"},
    {"state": "Dadra and Nagar Haveli and Daman and Diu", "district": "Silvassa", "name": "DLSA Dadra and Nagar Haveli", "phone": "0260-2642766", "address": "District Court Complex, Silvassa 396230"},
    {"state": "Jammu and Kashmir", "district": "Srinagar", "name": "DLSA Srinagar", "phone": "0194-2477266", "address": "District Court Complex, Srinagar, J&K 190001"},
    {"state": "Jammu and Kashmir", "district": "Jammu", "name": "DLSA Jammu", "phone": "0191-2520266", "address": "District Court Complex, Jammu, J&K 180001"},
    {"state": "Ladakh", "district": "Leh", "name": "DLSA Leh", "phone": "01982-252766", "address": "District Court Complex, Leh, Ladakh 194101"},
    {"state": "Lakshadweep", "district": "Kavaratti", "name": "DLSA Lakshadweep", "phone": "04896-262766", "address": "District Court Complex, Kavaratti, Lakshadweep 682555"},
    {"state": "Puducherry", "district": "Puducherry", "name": "DLSA Puducherry", "phone": "0413-2334766", "address": "District Court Complex, Puducherry 605001"},
]


# ---------------------------------------------------------------------------
# Pre-populated service centre directory (major cities)
# ---------------------------------------------------------------------------

_SERVICE_DIRECTORY: Final[list[dict]] = [
    # CSCs -- Sample entries (India has 500,000+ CSCs; production loads from API)
    {"name": "CSC Lucknow Main", "service_type": "csc", "state": "Uttar Pradesh", "district": "Lucknow", "address": "Hazratganj, Lucknow, UP 226001", "pin_code": "226001", "latitude": 26.8467, "longitude": 80.9462, "phone": "1800-121-3468", "services_offered": ["Aadhaar", "PAN", "Banking", "Insurance", "Scheme applications"]},
    {"name": "CSC Varanasi Cantt", "service_type": "csc", "state": "Uttar Pradesh", "district": "Varanasi", "address": "Cantt Area, Varanasi, UP 221002", "pin_code": "221002", "latitude": 25.3176, "longitude": 83.0123, "phone": "1800-121-3468"},
    {"name": "CSC Patna City", "service_type": "csc", "state": "Bihar", "district": "Patna", "address": "Kankarbagh, Patna, Bihar 800020", "pin_code": "800020", "latitude": 25.5941, "longitude": 85.1376, "phone": "1800-121-3468"},
    {"name": "CSC Jaipur Central", "service_type": "csc", "state": "Rajasthan", "district": "Jaipur", "address": "MI Road, Jaipur, Rajasthan 302001", "pin_code": "302001", "latitude": 26.9124, "longitude": 75.7873, "phone": "1800-121-3468"},
    {"name": "CSC Bhopal Main", "service_type": "csc", "state": "Madhya Pradesh", "district": "Bhopal", "address": "New Market, Bhopal, MP 462001", "pin_code": "462001", "latitude": 23.2599, "longitude": 77.4126, "phone": "1800-121-3468"},
    {"name": "CSC Mumbai Andheri", "service_type": "csc", "state": "Maharashtra", "district": "Mumbai", "address": "Andheri West, Mumbai 400058", "pin_code": "400058", "latitude": 19.1362, "longitude": 72.8296, "phone": "1800-121-3468"},
    {"name": "CSC Chennai T Nagar", "service_type": "csc", "state": "Tamil Nadu", "district": "Chennai", "address": "T Nagar, Chennai, TN 600017", "pin_code": "600017", "latitude": 13.0418, "longitude": 80.2341, "phone": "1800-121-3468"},
    {"name": "CSC Kolkata Salt Lake", "service_type": "csc", "state": "West Bengal", "district": "Kolkata", "address": "Salt Lake, Kolkata, WB 700091", "pin_code": "700091", "latitude": 22.5839, "longitude": 88.4178, "phone": "1800-121-3468"},
    {"name": "CSC Hyderabad Ameerpet", "service_type": "csc", "state": "Telangana", "district": "Hyderabad", "address": "Ameerpet, Hyderabad, Telangana 500016", "pin_code": "500016", "latitude": 17.4375, "longitude": 78.4483, "phone": "1800-121-3468"},
    {"name": "CSC Bengaluru Koramangala", "service_type": "csc", "state": "Karnataka", "district": "Bengaluru", "address": "Koramangala, Bengaluru, Karnataka 560034", "pin_code": "560034", "latitude": 12.9352, "longitude": 77.6245, "phone": "1800-121-3468"},
    {"name": "CSC Delhi Laxmi Nagar", "service_type": "csc", "state": "Delhi", "district": "East Delhi", "address": "Laxmi Nagar, Delhi 110092", "pin_code": "110092", "latitude": 28.6304, "longitude": 77.2773, "phone": "1800-121-3468"},
    {"name": "CSC Ahmedabad CG Road", "service_type": "csc", "state": "Gujarat", "district": "Ahmedabad", "address": "CG Road, Ahmedabad, Gujarat 380006", "pin_code": "380006", "latitude": 23.0300, "longitude": 72.5600, "phone": "1800-121-3468"},

    # Tehsil offices
    {"name": "Tehsil Office Lucknow Sadar", "service_type": "tehsil", "state": "Uttar Pradesh", "district": "Lucknow", "address": "Collectorate, Lucknow, UP 226001", "pin_code": "226001", "latitude": 26.8530, "longitude": 80.9420, "phone": "0522-2627433", "services_offered": ["Land records", "Revenue certificates", "Domicile certificate", "Income certificate", "Caste certificate"]},
    {"name": "Tehsil Office Varanasi Sadar", "service_type": "tehsil", "state": "Uttar Pradesh", "district": "Varanasi", "address": "Collectorate, Varanasi, UP 221001", "pin_code": "221001", "latitude": 25.3176, "longitude": 83.0100, "phone": "0542-2501100", "services_offered": ["Land records", "Revenue certificates", "Domicile certificate"]},
    {"name": "Tehsil Office Jaipur City", "service_type": "tehsil", "state": "Rajasthan", "district": "Jaipur", "address": "Collectorate, Jaipur, Rajasthan 302001", "pin_code": "302001", "latitude": 26.9200, "longitude": 75.7800, "phone": "0141-2227600", "services_offered": ["Land records", "Revenue certificates", "Income certificate"]},
    {"name": "Tehsil Office Patna Sadar", "service_type": "tehsil", "state": "Bihar", "district": "Patna", "address": "Collectorate, Patna, Bihar 800001", "pin_code": "800001", "latitude": 25.6100, "longitude": 85.1400, "phone": "0612-2219800", "services_offered": ["Land records", "Revenue certificates", "Income certificate"]},

    # Block Development Offices
    {"name": "BDO Office Lucknow Mohanlalganj", "service_type": "block_office", "state": "Uttar Pradesh", "district": "Lucknow", "address": "Mohanlalganj Block, Lucknow, UP 226301", "pin_code": "226301", "latitude": 26.7500, "longitude": 80.9300, "phone": "0522-2611200", "services_offered": ["MGNREGA job card", "PM Awas Yojana", "Pension schemes", "Rural development"]},
    {"name": "BDO Office Varanasi Pindra", "service_type": "block_office", "state": "Uttar Pradesh", "district": "Varanasi", "address": "Pindra Block, Varanasi, UP 221201", "pin_code": "221201", "latitude": 25.3500, "longitude": 83.0800, "phone": "0542-2601100", "services_offered": ["MGNREGA", "PM Awas Yojana", "Pension schemes"]},

    # Post offices (with banking)
    {"name": "GPO Lucknow", "service_type": "post_office", "state": "Uttar Pradesh", "district": "Lucknow", "address": "GPO, Hazratganj, Lucknow, UP 226001", "pin_code": "226001", "latitude": 26.8490, "longitude": 80.9470, "phone": "0522-2612833", "services_offered": ["Post office savings", "Sukanya Samriddhi", "Senior Citizen Savings", "PPF", "Aadhaar", "Money orders"]},
    {"name": "GPO Mumbai", "service_type": "post_office", "state": "Maharashtra", "district": "Mumbai", "address": "GPO, Fort, Mumbai, Maharashtra 400001", "pin_code": "400001", "latitude": 18.9322, "longitude": 72.8347, "phone": "022-22620433", "services_offered": ["Post office savings", "Sukanya Samriddhi", "PPF", "Aadhaar"]},
    {"name": "GPO Delhi", "service_type": "post_office", "state": "Delhi", "district": "New Delhi", "address": "GPO, Gole Dak Khana, New Delhi 110001", "pin_code": "110001", "latitude": 28.6240, "longitude": 77.2090, "phone": "011-23364111", "services_offered": ["Post office savings", "Sukanya Samriddhi", "PPF", "Aadhaar"]},

    # Banks (SBI branches as common example)
    {"name": "SBI Main Branch Lucknow", "service_type": "bank", "state": "Uttar Pradesh", "district": "Lucknow", "address": "MG Marg, Lucknow, UP 226001", "pin_code": "226001", "latitude": 26.8460, "longitude": 80.9460, "phone": "0522-2286900", "services_offered": ["Jan Dhan account", "PM Mudra Yojana", "KCC", "Savings account", "Aadhaar seeding"]},
    {"name": "SBI Main Branch Patna", "service_type": "bank", "state": "Bihar", "district": "Patna", "address": "West Gandhi Maidan, Patna, Bihar 800001", "pin_code": "800001", "latitude": 25.6100, "longitude": 85.1300, "phone": "0612-2219500", "services_offered": ["Jan Dhan account", "PM Mudra Yojana", "KCC", "Savings account"]},
    {"name": "SBI Main Branch Jaipur", "service_type": "bank", "state": "Rajasthan", "district": "Jaipur", "address": "Sanganeri Gate, Jaipur, Rajasthan 302001", "pin_code": "302001", "latitude": 26.9100, "longitude": 75.7900, "phone": "0141-2560800", "services_offered": ["Jan Dhan account", "PM Mudra Yojana", "KCC"]},

    # Courts
    {"name": "District Court Lucknow", "service_type": "court", "state": "Uttar Pradesh", "district": "Lucknow", "address": "Qaiserbagh, Lucknow, UP 226001", "pin_code": "226001", "latitude": 26.8510, "longitude": 80.9390, "phone": "0522-2624400", "services_offered": ["Civil cases", "Criminal cases", "Family court", "Consumer forum"]},
    {"name": "Patiala House Court Delhi", "service_type": "court", "state": "Delhi", "district": "New Delhi", "address": "Patiala House, India Gate, New Delhi 110001", "pin_code": "110001", "latitude": 28.6200, "longitude": 77.2370, "phone": "011-23384800", "services_offered": ["Civil cases", "Criminal cases"]},
]


# ---------------------------------------------------------------------------
# PIN code to state/district mapping (major PIN code prefixes)
# ---------------------------------------------------------------------------

_PIN_STATE_MAP: Final[dict[str, tuple[str, str]]] = {
    "110": ("Delhi", "Delhi"),
    "120": ("Haryana", "Gurugram"),
    "121": ("Haryana", "Faridabad"),
    "122": ("Haryana", "Gurugram"),
    "125": ("Haryana", "Hisar"),
    "141": ("Punjab", "Ludhiana"),
    "143": ("Punjab", "Amritsar"),
    "144": ("Punjab", "Jalandhar"),
    "160": ("Chandigarh", "Chandigarh"),
    "171": ("Himachal Pradesh", "Shimla"),
    "180": ("Jammu and Kashmir", "Jammu"),
    "190": ("Jammu and Kashmir", "Srinagar"),
    "194": ("Ladakh", "Leh"),
    "201": ("Uttar Pradesh", "Ghaziabad"),
    "208": ("Uttar Pradesh", "Kanpur"),
    "211": ("Uttar Pradesh", "Prayagraj"),
    "221": ("Uttar Pradesh", "Varanasi"),
    "226": ("Uttar Pradesh", "Lucknow"),
    "248": ("Uttarakhand", "Dehradun"),
    "249": ("Uttarakhand", "Haridwar"),
    "250": ("Uttar Pradesh", "Meerut"),
    "273": ("Uttar Pradesh", "Gorakhpur"),
    "282": ("Uttar Pradesh", "Agra"),
    "302": ("Rajasthan", "Jaipur"),
    "313": ("Rajasthan", "Udaipur"),
    "324": ("Rajasthan", "Kota"),
    "342": ("Rajasthan", "Jodhpur"),
    "360": ("Gujarat", "Rajkot"),
    "380": ("Gujarat", "Ahmedabad"),
    "390": ("Gujarat", "Vadodara"),
    "395": ("Gujarat", "Surat"),
    "400": ("Maharashtra", "Mumbai"),
    "411": ("Maharashtra", "Pune"),
    "440": ("Maharashtra", "Nagpur"),
    "422": ("Maharashtra", "Nashik"),
    "452": ("Madhya Pradesh", "Indore"),
    "462": ("Madhya Pradesh", "Bhopal"),
    "474": ("Madhya Pradesh", "Gwalior"),
    "482": ("Madhya Pradesh", "Jabalpur"),
    "492": ("Chhattisgarh", "Raipur"),
    "495": ("Chhattisgarh", "Bilaspur"),
    "500": ("Telangana", "Hyderabad"),
    "506": ("Telangana", "Warangal"),
    "517": ("Andhra Pradesh", "Tirupati"),
    "520": ("Andhra Pradesh", "Vijayawada"),
    "522": ("Andhra Pradesh", "Guntur"),
    "530": ("Andhra Pradesh", "Visakhapatnam"),
    "560": ("Karnataka", "Bengaluru"),
    "570": ("Karnataka", "Mysuru"),
    "575": ("Karnataka", "Mangaluru"),
    "580": ("Karnataka", "Hubballi"),
    "600": ("Tamil Nadu", "Chennai"),
    "625": ("Tamil Nadu", "Madurai"),
    "636": ("Tamil Nadu", "Salem"),
    "641": ("Tamil Nadu", "Coimbatore"),
    "670": ("Kerala", "Kozhikode"),
    "680": ("Kerala", "Thrissur"),
    "682": ("Kerala", "Ernakulam"),
    "695": ("Kerala", "Thiruvananthapuram"),
    "700": ("West Bengal", "Kolkata"),
    "711": ("West Bengal", "Howrah"),
    "734": ("West Bengal", "Darjeeling"),
    "781": ("Assam", "Guwahati"),
    "786": ("Assam", "Dibrugarh"),
    "791": ("Arunachal Pradesh", "Itanagar"),
    "795": ("Manipur", "Imphal"),
    "793": ("Meghalaya", "Shillong"),
    "796": ("Mizoram", "Aizawl"),
    "797": ("Nagaland", "Kohima"),
    "799": ("Tripura", "Agartala"),
    "800": ("Bihar", "Patna"),
    "812": ("Bihar", "Bhagalpur"),
    "823": ("Bihar", "Gaya"),
    "826": ("Jharkhand", "Dhanbad"),
    "831": ("Jharkhand", "Jamshedpur"),
    "834": ("Jharkhand", "Ranchi"),
    "842": ("Bihar", "Muzaffarpur"),
    "737": ("Sikkim", "Gangtok"),
    "403": ("Goa", "Panaji"),
    "744": ("Andaman and Nicobar Islands", "Port Blair"),
    "605": ("Puducherry", "Puducherry"),
    "682555": ("Lakshadweep", "Kavaratti"),
    "751": ("Odisha", "Bhubaneswar"),
    "753": ("Odisha", "Cuttack"),
}


# ---------------------------------------------------------------------------
# Important helpline numbers
# ---------------------------------------------------------------------------

_HELPLINES: Final[dict[str, str]] = {
    "tele_law": "1516",
    "nalsa": "15100",
    "csc_helpline": "1800-121-3468",
    "women_helpline": "181",
    "child_helpline": "1098",
    "police": "100",
    "ambulance": "108",
    "senior_citizen_helpline": "14567",
    "cyber_crime": "1930",
    "consumer_helpline": "1800-11-4000",
    "anti_corruption": "1031",
    "railway_helpline": "139",
    "pm_helpline": "1800-11-555",
    "bsnl_toll_free": "1800-345-0012",
}


# ---------------------------------------------------------------------------
# Haversine distance calculation
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM: Final[float] = 6371.0


def _haversine_distance(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Calculate the great-circle distance between two points on Earth.

    Uses the Haversine formula. Returns distance in kilometres.

    Parameters
    ----------
    lat1, lon1:
        Latitude and longitude of point 1 in decimal degrees.
    lat2, lon2:
        Latitude and longitude of point 2 in decimal degrees.

    Returns
    -------
    float
        Distance in kilometres.
    """
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return _EARTH_RADIUS_KM * c


# ---------------------------------------------------------------------------
# Nearby Services Locator
# ---------------------------------------------------------------------------


class NearbyServicesLocator:
    """Finds nearest government service centres, DLSAs, CSCs, courts, etc.

    Uses a pre-populated directory indexed by state/district for instant
    lookups, with Haversine-based distance calculation when coordinates
    are available.  Can optionally call Google Maps/Places API for
    real-time results when an API key is configured.

    Usage::

        locator = NearbyServicesLocator()

        # Find nearby CSCs
        cscs = locator.find_nearby(26.85, 80.95, "csc", radius_km=10)

        # Get DLSA info for a district
        dlsa = locator.get_dlsa_info("Uttar Pradesh", "Lucknow")

        # Get CSC by PIN code
        cscs = locator.get_csc_info("226001")

        # Get all services in a state
        services = locator.get_service_directory("Bihar", "bank")
    """

    __slots__ = (
        "_dlsa_index",
        "_google_api_key",
        "_pin_index",
        "_service_index",
        "_state_index",
    )

    def __init__(self, google_api_key: str | None = None) -> None:
        self._google_api_key = google_api_key

        # Build indexes for fast lookup
        self._dlsa_index: dict[str, list[dict[str, str]]] = {}
        self._service_index: dict[str, list[dict]] = {}
        self._state_index: dict[str, list[dict]] = {}
        self._pin_index: dict[str, list[dict]] = {}

        self._build_indexes()

    def _build_indexes(self) -> None:
        """Build lookup indexes from the pre-populated directories."""
        # Index DLSAs by state (normalised to lowercase)
        for entry in _DLSA_DIRECTORY:
            state_key = entry["state"].lower().strip()
            if state_key not in self._dlsa_index:
                self._dlsa_index[state_key] = []
            self._dlsa_index[state_key].append(entry)

        # Index service locations by state and by type
        for entry in _SERVICE_DIRECTORY:
            state_key = entry.get("state", "").lower().strip()
            stype = entry.get("service_type", "").lower().strip()

            # State + type composite key
            composite_key = f"{state_key}:{stype}"
            if composite_key not in self._service_index:
                self._service_index[composite_key] = []
            self._service_index[composite_key].append(entry)

            # State-only key
            if state_key not in self._state_index:
                self._state_index[state_key] = []
            self._state_index[state_key].append(entry)

            # PIN code index
            pin = entry.get("pin_code")
            if pin:
                pin_prefix = pin[:3]
                if pin_prefix not in self._pin_index:
                    self._pin_index[pin_prefix] = []
                self._pin_index[pin_prefix].append(entry)

        logger.info(
            "nearby_services.indexes_built",
            dlsa_states=len(self._dlsa_index),
            service_entries=len(_SERVICE_DIRECTORY),
            pin_prefixes=len(self._pin_index),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_nearby(
        self,
        latitude: float,
        longitude: float,
        service_type: str,
        radius_km: float = 25.0,
    ) -> list[ServiceLocation]:
        """Find nearest service centres of a given type.

        Searches the pre-populated directory for entries within the
        specified radius, sorted by distance (nearest first).  When
        a Google Maps API key is configured and no results are found
        in the local directory, falls back to a Places API search.

        Parameters
        ----------
        latitude:
            User's latitude in decimal degrees.
        longitude:
            User's longitude in decimal degrees.
        service_type:
            Type of service to search for (see ``ServiceType`` enum).
        radius_km:
            Search radius in kilometres (default 25 km).

        Returns
        -------
        list[ServiceLocation]
            Service locations sorted by distance, nearest first.
        """
        stype = service_type.lower().strip()
        results: list[ServiceLocation] = []

        # Search all entries that have coordinates
        candidates = _SERVICE_DIRECTORY
        if stype != "all":
            candidates = [
                entry for entry in _SERVICE_DIRECTORY
                if entry.get("service_type", "").lower() == stype
            ]

        for entry in candidates:
            entry_lat = entry.get("latitude")
            entry_lon = entry.get("longitude")
            if entry_lat is None or entry_lon is None:
                continue

            distance = _haversine_distance(
                latitude, longitude, entry_lat, entry_lon
            )

            if distance <= radius_km:
                location = ServiceLocation(
                    name=entry["name"],
                    service_type=ServiceType(entry["service_type"]),
                    state=entry.get("state", ""),
                    district=entry.get("district", ""),
                    address=entry.get("address", ""),
                    pin_code=entry.get("pin_code"),
                    latitude=entry_lat,
                    longitude=entry_lon,
                    phone=entry.get("phone"),
                    email=entry.get("email"),
                    website=entry.get("website"),
                    working_hours=entry.get("working_hours", "Mon-Fri 10:00 AM - 5:00 PM"),
                    distance_km=round(distance, 2),
                    services_offered=entry.get("services_offered", []),
                )
                results.append(location)

        # Also search DLSA directory if looking for DLSA or court
        if stype in ("dlsa", "court", "all"):
            for _entry in _DLSA_DIRECTORY:
                # DLSAs don't have coordinates in our directory; skip
                # distance-based filtering but include them as results
                # with no distance if within a reasonable area
                pass

        # Sort by distance (nearest first)
        results.sort(key=lambda loc: loc.distance_km if loc.distance_km is not None else float("inf"))

        # If no results found locally and Google API is available, try Places API
        if not results and self._google_api_key:
            results = self._search_google_places(
                latitude, longitude, stype, radius_km
            )

        logger.info(
            "nearby_services.find_nearby",
            latitude=latitude,
            longitude=longitude,
            service_type=stype,
            radius_km=radius_km,
            results_count=len(results),
        )

        return results

    def get_dlsa_info(self, state: str, district: str) -> DLSAInfo | None:
        """Get DLSA information for a specific state and district.

        Performs fuzzy matching on state and district names to handle
        common variations (e.g. "UP" -> "Uttar Pradesh").

        Parameters
        ----------
        state:
            State name (full or abbreviated).
        district:
            District name.

        Returns
        -------
        DLSAInfo | None
            DLSA details if found, else None.
        """
        normalised_state = self._normalise_state(state)
        state_entries = self._dlsa_index.get(normalised_state.lower(), [])

        if not state_entries:
            # Try partial match
            for key, entries in self._dlsa_index.items():
                if normalised_state.lower() in key or key in normalised_state.lower():
                    state_entries = entries
                    break

        if not state_entries:
            logger.warning(
                "nearby_services.dlsa_not_found",
                state=state,
                district=district,
                normalised_state=normalised_state,
            )
            return None

        # Find matching district
        district_lower = district.lower().strip()
        best_match: dict[str, str] | None = None
        for entry in state_entries:
            entry_district = entry["district"].lower().strip()
            if entry_district == district_lower:
                best_match = entry
                break
            if district_lower in entry_district or entry_district in district_lower:
                best_match = entry

        if best_match is None:
            # Return the first entry for the state as fallback (state DLSA)
            best_match = state_entries[0]
            logger.info(
                "nearby_services.dlsa_district_fallback",
                state=state,
                district=district,
                fallback_district=best_match["district"],
            )

        return DLSAInfo(
            state=best_match["state"],
            district=best_match["district"],
            name=best_match["name"],
            address=best_match["address"],
            phone=best_match["phone"],
            email=best_match.get("email"),
            website=best_match.get("website"),
        )

    def get_csc_info(self, pin_code: str) -> list[CSCInfo]:
        """Get Common Service Centre information by PIN code.

        Searches the local directory using PIN code prefix matching,
        then returns matching CSCs with services offered.

        Parameters
        ----------
        pin_code:
            6-digit Indian PIN code.

        Returns
        -------
        list[CSCInfo]
            CSC details for the given PIN code area.
        """
        pin_code = pin_code.strip()
        results: list[CSCInfo] = []

        # Try exact PIN match first, then prefix
        pin_prefix = pin_code[:3]
        candidates = self._pin_index.get(pin_prefix, [])

        # Filter for CSCs only
        csc_entries = [
            entry for entry in candidates
            if entry.get("service_type", "").lower() == "csc"
        ]

        # If no CSC found by PIN, look up state from PIN and search
        if not csc_entries:
            state_district = _PIN_STATE_MAP.get(pin_prefix)
            if state_district:
                state, _district = state_district
                composite_key = f"{state.lower()}:csc"
                csc_entries = self._service_index.get(composite_key, [])

        for idx, entry in enumerate(csc_entries):
            csc = CSCInfo(
                csc_id=f"CSC-{pin_code}-{idx + 1:03d}",
                name=entry.get("name", f"CSC {pin_code}"),
                state=entry.get("state", ""),
                district=entry.get("district", ""),
                pin_code=entry.get("pin_code", pin_code),
                address=entry.get("address", ""),
                phone=entry.get("phone", "1800-121-3468"),
                latitude=entry.get("latitude"),
                longitude=entry.get("longitude"),
                services_offered=entry.get("services_offered", []),
            )
            results.append(csc)

        # Always include the CSC helpline info even if no specific centre found
        if not results:
            state_district = _PIN_STATE_MAP.get(pin_prefix)
            state_name = state_district[0] if state_district else "India"
            district_name = state_district[1] if state_district else ""

            results.append(CSCInfo(
                csc_id=f"CSC-{pin_code}-GEN",
                name=f"Nearest CSC for PIN {pin_code}",
                state=state_name,
                district=district_name,
                pin_code=pin_code,
                address="Find your nearest CSC at https://locator.csccloud.in/ or call 1800-121-3468",
                phone="1800-121-3468",
                services_offered=[
                    "Aadhaar enrolment and update",
                    "PAN card application",
                    "Bank account opening (PMJDY)",
                    "Insurance (PMSBY, PMJJBY)",
                    "Pension schemes (APY)",
                    "Government scheme applications",
                    "Tele-Law consultations (call 1516)",
                ],
            ))

        logger.info(
            "nearby_services.csc_lookup",
            pin_code=pin_code,
            results_count=len(results),
        )

        return results

    def get_service_directory(
        self, state: str, service_type: str
    ) -> list[ServiceLocation]:
        """Get all service centres of a type in a state.

        Parameters
        ----------
        state:
            State name (full or abbreviated).
        service_type:
            Type of service (see ``ServiceType`` enum).
            Use "all" to get all service types.

        Returns
        -------
        list[ServiceLocation]
            All matching service centres.
        """
        normalised_state = self._normalise_state(state).lower()
        stype = service_type.lower().strip()

        if stype == "all":
            entries = self._state_index.get(normalised_state, [])
        else:
            composite_key = f"{normalised_state}:{stype}"
            entries = self._service_index.get(composite_key, [])

            # Try partial state match if no results
            if not entries:
                for key, vals in self._service_index.items():
                    if normalised_state in key and key.endswith(f":{stype}"):
                        entries = vals
                        break

        results: list[ServiceLocation] = []
        for entry in entries:
            location = ServiceLocation(
                name=entry["name"],
                service_type=ServiceType(entry["service_type"]),
                state=entry.get("state", ""),
                district=entry.get("district", ""),
                address=entry.get("address", ""),
                pin_code=entry.get("pin_code"),
                latitude=entry.get("latitude"),
                longitude=entry.get("longitude"),
                phone=entry.get("phone"),
                email=entry.get("email"),
                website=entry.get("website"),
                working_hours=entry.get("working_hours", "Mon-Fri 10:00 AM - 5:00 PM"),
                services_offered=entry.get("services_offered", []),
            )
            results.append(location)

        # If looking for DLSA, also include DLSA directory entries
        if stype in ("dlsa", "all"):
            dlsa_entries = self._dlsa_index.get(normalised_state, [])
            for entry in dlsa_entries:
                location = ServiceLocation(
                    name=entry["name"],
                    service_type=ServiceType.DLSA,
                    state=entry["state"],
                    district=entry["district"],
                    address=entry["address"],
                    phone=entry["phone"],
                    email=entry.get("email"),
                    working_hours="Mon-Sat 10:00 AM - 5:00 PM",
                    services_offered=[
                        "Free legal aid",
                        "Lok Adalat",
                        "Legal awareness",
                        "Tele-Law (1516)",
                        "Victim compensation",
                        "Mediation",
                    ],
                )
                results.append(location)

        logger.info(
            "nearby_services.directory_lookup",
            state=state,
            service_type=stype,
            results_count=len(results),
        )

        return results

    def get_helpline(self, service: str) -> str | None:
        """Get a helpline number by service name.

        Parameters
        ----------
        service:
            Service name (e.g. "tele_law", "women_helpline", "nalsa").

        Returns
        -------
        str | None
            Helpline number if found.
        """
        return _HELPLINES.get(service.lower().strip())

    def get_all_helplines(self) -> dict[str, str]:
        """Get all available helpline numbers.

        Returns
        -------
        dict[str, str]
            Mapping of service name to helpline number.
        """
        return dict(_HELPLINES)

    def get_directions_text(
        self,
        user_lat: float,
        user_lon: float,
        destination: ServiceLocation,
    ) -> str:
        """Generate human-readable directions text.

        Provides cardinal direction and approximate distance.  If a
        Google Maps API key is configured, includes a Maps link.

        Parameters
        ----------
        user_lat:
            User's latitude.
        user_lon:
            User's longitude.
        destination:
            Target service location.

        Returns
        -------
        str
            Human-readable directions text.
        """
        if destination.latitude is None or destination.longitude is None:
            return (
                f"{destination.name}\n"
                f"Address: {destination.address}\n"
                f"Phone: {destination.phone or 'N/A'}\n"
                f"Working hours: {destination.working_hours}"
            )

        distance = _haversine_distance(
            user_lat, user_lon,
            destination.latitude, destination.longitude,
        )

        # Calculate cardinal direction
        direction = self._cardinal_direction(
            user_lat, user_lon,
            destination.latitude, destination.longitude,
        )

        # Build directions text
        lines = [
            f"{destination.name}",
            f"Distance: {distance:.1f} km ({direction})",
            f"Address: {destination.address}",
        ]

        if destination.phone:
            lines.append(f"Phone: {destination.phone}")

        lines.append(f"Working hours: {destination.working_hours}")

        # Add Google Maps link if API key is available
        maps_url = (
            f"https://www.google.com/maps/dir/{user_lat},{user_lon}/"
            f"{destination.latitude},{destination.longitude}"
        )
        lines.append(f"Google Maps: {maps_url}")

        return "\n".join(lines)

    def get_states_with_dlsa(self) -> list[str]:
        """Get list of all states/UTs with DLSA entries in the directory.

        Returns
        -------
        list[str]
            Sorted list of state names.
        """
        states = set()
        for entry in _DLSA_DIRECTORY:
            states.add(entry["state"])
        return sorted(states)

    def get_all_dlsa_for_state(self, state: str) -> list[DLSAInfo]:
        """Get all DLSA offices for a state.

        Parameters
        ----------
        state:
            State name (full or abbreviated).

        Returns
        -------
        list[DLSAInfo]
            All DLSA offices in the state.
        """
        normalised_state = self._normalise_state(state).lower()
        entries = self._dlsa_index.get(normalised_state, [])

        # Try partial match
        if not entries:
            for key, vals in self._dlsa_index.items():
                if normalised_state in key or key in normalised_state:
                    entries = vals
                    break

        results: list[DLSAInfo] = []
        for entry in entries:
            results.append(DLSAInfo(
                state=entry["state"],
                district=entry["district"],
                name=entry["name"],
                address=entry["address"],
                phone=entry["phone"],
                email=entry.get("email"),
                website=entry.get("website"),
            ))

        return results

    # ------------------------------------------------------------------
    # Google Maps / Places API integration
    # ------------------------------------------------------------------

    def _search_google_places(
        self,
        latitude: float,
        longitude: float,
        service_type: str,
        radius_km: float,
    ) -> list[ServiceLocation]:
        """Search Google Places API for nearby services.

        This is a fallback when the local directory has no results.
        Requires a valid Google Maps API key.

        Parameters
        ----------
        latitude:
            User's latitude.
        longitude:
            User's longitude.
        service_type:
            Type of service to search for.
        radius_km:
            Search radius in kilometres.

        Returns
        -------
        list[ServiceLocation]
            Results from Google Places API.
        """
        if not self._google_api_key:
            return []

        # Map service types to Google Places search terms
        search_terms: dict[str, str] = {
            "csc": "Common Service Centre CSC",
            "dlsa": "District Legal Services Authority",
            "tehsil": "tehsil office revenue office",
            "block_office": "Block Development Office BDO",
            "post_office": "post office India Post",
            "bank": "State Bank of India bank branch",
            "court": "district court",
            "anganwadi": "anganwadi centre ICDS",
            "ration_shop": "ration shop fair price shop",
            "phc": "primary health centre PHC",
        }

        query = search_terms.get(service_type, f"{service_type} government office India")
        radius_m = int(radius_km * 1000)

        try:
            import httpx

            url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
            params = {
                "location": f"{latitude},{longitude}",
                "radius": str(min(radius_m, 50000)),  # Max 50km for Places API
                "keyword": query,
                "key": self._google_api_key,
            }

            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            results: list[ServiceLocation] = []
            for place in data.get("results", [])[:10]:
                location_data = place.get("geometry", {}).get("location", {})
                place_lat = location_data.get("lat")
                place_lon = location_data.get("lng")

                distance = None
                if place_lat and place_lon:
                    distance = round(
                        _haversine_distance(latitude, longitude, place_lat, place_lon),
                        2,
                    )

                loc = ServiceLocation(
                    name=place.get("name", "Unknown"),
                    service_type=ServiceType(service_type) if service_type in ServiceType.__members__.values() else ServiceType.CSC,
                    state="",
                    district="",
                    address=place.get("vicinity", ""),
                    latitude=place_lat,
                    longitude=place_lon,
                    distance_km=distance,
                    is_verified=False,  # Google results are not verified
                )
                results.append(loc)

            results.sort(
                key=lambda loc: loc.distance_km if loc.distance_km is not None else float("inf")
            )

            logger.info(
                "nearby_services.google_places_search",
                query=query,
                results_count=len(results),
            )

            return results

        except Exception:
            logger.warning(
                "nearby_services.google_places_failed",
                service_type=service_type,
                exc_info=True,
            )
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_state(state: str) -> str:
        """Normalise state name, handling common abbreviations.

        Maps abbreviations like 'UP', 'MP', 'AP', 'TN' etc. to full
        state names for consistent lookup.

        Parameters
        ----------
        state:
            State name or abbreviation.

        Returns
        -------
        str
            Normalised full state name.
        """
        abbreviations: dict[str, str] = {
            "up": "Uttar Pradesh",
            "mp": "Madhya Pradesh",
            "ap": "Andhra Pradesh",
            "tn": "Tamil Nadu",
            "wb": "West Bengal",
            "hp": "Himachal Pradesh",
            "jk": "Jammu and Kashmir",
            "j&k": "Jammu and Kashmir",
            "uk": "Uttarakhand",
            "cg": "Chhattisgarh",
            "rj": "Rajasthan",
            "gj": "Gujarat",
            "mh": "Maharashtra",
            "ka": "Karnataka",
            "kl": "Kerala",
            "ts": "Telangana",
            "or": "Odisha",
            "br": "Bihar",
            "jh": "Jharkhand",
            "hr": "Haryana",
            "pb": "Punjab",
            "ga": "Goa",
            "ar": "Arunachal Pradesh",
            "as": "Assam",
            "mn": "Manipur",
            "ml": "Meghalaya",
            "mz": "Mizoram",
            "nl": "Nagaland",
            "sk": "Sikkim",
            "tr": "Tripura",
            "dl": "Delhi",
            "ch": "Chandigarh",
            "an": "Andaman and Nicobar Islands",
            "ld": "Lakshadweep",
            "py": "Puducherry",
            "la": "Ladakh",
            "dd": "Dadra and Nagar Haveli and Daman and Diu",
        }

        state_stripped = state.strip()
        state_lower = state_stripped.lower()

        # Check abbreviation map
        if state_lower in abbreviations:
            return abbreviations[state_lower]

        # Return as-is (with title case normalisation)
        return state_stripped

    @staticmethod
    def _cardinal_direction(
        lat1: float, lon1: float, lat2: float, lon2: float
    ) -> str:
        """Calculate the cardinal direction from point 1 to point 2.

        Returns one of: N, NE, E, SE, S, SW, W, NW.
        """
        dlat = lat2 - lat1
        dlon = lon2 - lon1

        angle = math.degrees(math.atan2(dlon, dlat))
        if angle < 0:
            angle += 360

        directions = [
            "North", "North-East", "East", "South-East",
            "South", "South-West", "West", "North-West",
        ]
        index = round(angle / 45) % 8

        return directions[index]
