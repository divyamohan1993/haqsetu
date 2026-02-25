"""Legal Rights and BNS advisory service for HaqSetu.

Helps rural Indian citizens understand their legal rights by:

1. **Identifying applicable laws** -- Given a situation described in the
   user's own words, uses Gemini LLM to map the scenario to relevant
   provisions of the Bharat Nyaya Sanhita (BNS) 2023, Bharatiya Nagarik
   Suraksha Sanhita (BNSS) 2023, Bharatiya Sakshya Adhiniyam (BSA) 2023,
   the Constitution of India, and key special statutes (SC/ST Act, PWDV
   Act, POCSO, Consumer Protection Act, etc.).

2. **Providing a comprehensive BNS database** -- 30+ key sections of the
   BNS with old IPC mappings so citizens can understand the new criminal
   law that replaced IPC from 1 July 2024.

3. **Helpline directory** -- Verified helpline numbers across 9 categories
   (general, women, children, SC/ST, labor, consumer, cyber crime, senior
   citizen, disability) curated from official government sources.

Data sources:
    * Bharat Nyaya Sanhita, 2023 (Act No. 45 of 2023)
    * Bharatiya Nagarik Suraksha Sanhita, 2023 (Act No. 46 of 2023)
    * Bharatiya Sakshya Adhiniyam, 2023 (Act No. 47 of 2023)
    * National Legal Services Authority (NALSA)
    * Ministry of Women and Child Development
    * Ministry of Home Affairs -- helpline directories
    * National Commission for Women (NCW)
    * National Commission for Scheduled Castes (NCSC)
    * National Human Rights Commission (NHRC)

DISCLAIMER: All information is for educational purposes only and does
NOT constitute legal advice. Users should consult DLSA / a qualified
lawyer for actual legal counsel.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from src.services.llm import LLMService

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Legal disclaimer -- included in every response
# ---------------------------------------------------------------------------

_LEGAL_DISCLAIMER: Final[str] = (
    "DISCLAIMER: This information is provided for educational and "
    "awareness purposes only. It does NOT constitute legal advice. "
    "For actual legal guidance, please contact your nearest District "
    "Legal Services Authority (DLSA), call the NALSA helpline 15100, "
    "or consult a qualified advocate. Free legal aid is available to "
    "eligible persons under the Legal Services Authorities Act, 1987."
)


# =====================================================================
# Data classes
# =====================================================================


@dataclass(slots=True)
class ApplicableLaw:
    """A single law / statutory provision identified as relevant."""

    law: str
    description: str
    relevance: str
    bns_section: str | None = None
    act_name: str = ""


@dataclass(slots=True)
class ApplicableRight:
    """A fundamental or statutory right the citizen may exercise."""

    right_name: str
    source_law: str
    description: str
    how_to_exercise: str


@dataclass(slots=True)
class HelplineInfo:
    """A helpline number with metadata."""

    name: str
    number: str
    description: str
    hours: str = "24x7"
    languages: list[str] = field(default_factory=lambda: ["Hindi", "English"])


@dataclass(slots=True)
class LegalAnalysis:
    """Full analysis result returned by :meth:`LegalRightsService.identify_applicable_laws`."""

    situation_summary: str
    applicable_laws: list[ApplicableLaw]
    applicable_rights: list[ApplicableRight]
    recommended_actions: list[str]
    helplines: list[HelplineInfo]
    severity: str  # "low", "medium", "high", "critical"
    disclaimer: str = _LEGAL_DISCLAIMER


@dataclass(slots=True)
class BNSSection:
    """Information about a single section of the Bharat Nyaya Sanhita."""

    section_number: int
    title: str
    description: str
    old_ipc_section: str
    punishment: str
    bailable: bool
    cognizable: bool


# =====================================================================
# BNS Section Database
# =====================================================================
# Bharat Nyaya Sanhita, 2023 -- key sections with old IPC mappings.
# Effective 1 July 2024, replacing the Indian Penal Code, 1860.
# =====================================================================

_BNS_SECTIONS: Final[dict[int, BNSSection]] = {
    # -- Offences against the human body --
    100: BNSSection(
        section_number=100,
        title="Culpable homicide not amounting to murder",
        description=(
            "Whoever causes death by doing an act with the intention of "
            "causing death, or with the intention of causing such bodily "
            "injury as is likely to cause death, or with knowledge that "
            "the act is likely to cause death, commits culpable homicide "
            "not amounting to murder."
        ),
        old_ipc_section="IPC Section 299",
        punishment="Imprisonment up to 10 years and fine",
        bailable=False,
        cognizable=True,
    ),
    101: BNSSection(
        section_number=101,
        title="Murder",
        description=(
            "Whoever commits culpable homicide with the intention of "
            "causing death, or with the intention of causing such bodily "
            "injury as the offender knows to be likely to cause death, "
            "or with the intention of causing bodily injury sufficient "
            "in the ordinary course of nature to cause death, commits murder."
        ),
        old_ipc_section="IPC Section 300",
        punishment="Death or imprisonment for life and fine",
        bailable=False,
        cognizable=True,
    ),
    103: BNSSection(
        section_number=103,
        title="Punishment for murder",
        description=(
            "Whoever commits murder shall be punished with death or "
            "imprisonment for life, and shall also be liable to fine."
        ),
        old_ipc_section="IPC Section 302",
        punishment="Death or imprisonment for life, and fine",
        bailable=False,
        cognizable=True,
    ),
    105: BNSSection(
        section_number=105,
        title="Punishment for culpable homicide not amounting to murder",
        description=(
            "Whoever commits culpable homicide not amounting to murder "
            "shall be punished with imprisonment for life, or imprisonment "
            "up to 10 years, and shall also be liable to fine."
        ),
        old_ipc_section="IPC Section 304",
        punishment="Imprisonment for life or up to 10 years, and fine",
        bailable=False,
        cognizable=True,
    ),
    106: BNSSection(
        section_number=106,
        title="Causing death by negligence",
        description=(
            "Whoever causes death of any person by doing any rash or "
            "negligent act not amounting to culpable homicide shall be "
            "punished with imprisonment up to 5 years and fine. If the "
            "act is done by a registered medical practitioner while "
            "performing medical procedure, imprisonment up to 2 years."
        ),
        old_ipc_section="IPC Section 304A",
        punishment="Imprisonment up to 5 years and fine",
        bailable=True,
        cognizable=True,
    ),
    109: BNSSection(
        section_number=109,
        title="Attempt to murder",
        description=(
            "Whoever does any act with the intention or knowledge that "
            "the act would cause death, if the act caused death, would "
            "be guilty of murder. Punishable even if no hurt is caused."
        ),
        old_ipc_section="IPC Section 307",
        punishment="Imprisonment up to 10 years and fine; if hurt is caused, imprisonment for life or up to 10 years and fine",
        bailable=False,
        cognizable=True,
    ),
    115: BNSSection(
        section_number=115,
        title="Voluntarily causing hurt",
        description=(
            "Whoever does any act with the intention of thereby causing "
            "hurt to any person, or with the knowledge that he is likely "
            "thereby to cause hurt to any person, and does thereby cause "
            "hurt to any person, is said to voluntarily cause hurt."
        ),
        old_ipc_section="IPC Section 323",
        punishment="Imprisonment up to 1 year, or fine up to Rs 10,000, or both",
        bailable=True,
        cognizable=False,
    ),
    117: BNSSection(
        section_number=117,
        title="Voluntarily causing grievous hurt",
        description=(
            "Whoever voluntarily causes hurt, if the hurt which he "
            "intends to cause or knows himself to be likely to cause "
            "is grievous hurt, is said voluntarily to cause grievous hurt."
        ),
        old_ipc_section="IPC Section 325",
        punishment="Imprisonment up to 7 years and fine",
        bailable=False,
        cognizable=True,
    ),
    # -- Offences against women --
    63: BNSSection(
        section_number=63,
        title="Rape",
        description=(
            "A man is said to commit rape if he penetrates or performs "
            "sexual acts under circumstances falling within the seven "
            "descriptions, including against will, without consent, with "
            "consent obtained by fear or intoxication, or when the woman "
            "is unable to communicate consent."
        ),
        old_ipc_section="IPC Section 375",
        punishment="Rigorous imprisonment not less than 10 years, extendable to imprisonment for life, and fine",
        bailable=False,
        cognizable=True,
    ),
    64: BNSSection(
        section_number=64,
        title="Punishment for rape",
        description=(
            "Whoever commits rape shall be punished with rigorous "
            "imprisonment of not less than 10 years, but which may "
            "extend to imprisonment for life, and shall also be liable "
            "to fine."
        ),
        old_ipc_section="IPC Section 376",
        punishment="RI not less than 10 years, extendable to life imprisonment, and fine",
        bailable=False,
        cognizable=True,
    ),
    65: BNSSection(
        section_number=65,
        title="Punishment for rape in certain cases",
        description=(
            "Punishment for rape by police officer, public servant, "
            "armed forces member, management or staff of hospital, "
            "relative, guardian, teacher, or person in a position of "
            "trust or authority -- RI not less than 10 years, extendable "
            "to imprisonment for life (which shall mean the remainder "
            "of that person's natural life), and fine."
        ),
        old_ipc_section="IPC Section 376(2)",
        punishment="RI not less than 10 years, extendable to life imprisonment (remainder of natural life), and fine",
        bailable=False,
        cognizable=True,
    ),
    66: BNSSection(
        section_number=66,
        title="Punishment for gang rape",
        description=(
            "Where a woman is raped by one or more persons constituting "
            "a group or acting in furtherance of a common intention, each "
            "of those persons shall be deemed to have committed the "
            "offence of rape."
        ),
        old_ipc_section="IPC Section 376D",
        punishment="RI not less than 20 years, extendable to life (remainder of natural life), and fine",
        bailable=False,
        cognizable=True,
    ),
    69: BNSSection(
        section_number=69,
        title="Sexual intercourse by employing deceitful means etc.",
        description=(
            "Whoever, by deceitful means or by making promise to marry "
            "which he does not intend to fulfil, has sexual intercourse "
            "with a woman which is not rape, shall be punished."
        ),
        old_ipc_section="IPC Section 376(2)(f) -- new provision",
        punishment="Imprisonment up to 10 years and fine",
        bailable=False,
        cognizable=True,
    ),
    74: BNSSection(
        section_number=74,
        title="Assault or use of criminal force to woman with intent to outrage her modesty",
        description=(
            "Whoever assaults or uses criminal force to any woman, "
            "intending to outrage or knowing it to be likely that he "
            "will thereby outrage her modesty."
        ),
        old_ipc_section="IPC Section 354",
        punishment="Imprisonment not less than 1 year extendable to 5 years, and fine",
        bailable=False,
        cognizable=True,
    ),
    75: BNSSection(
        section_number=75,
        title="Sexual harassment",
        description=(
            "A man committing any of the following acts: (i) physical "
            "contact and advances involving unwelcome and explicit "
            "sexual overtures; (ii) a demand or request for sexual "
            "favours; (iii) showing pornography; (iv) making sexually "
            "coloured remarks; (v) any other unwelcome physical, verbal "
            "or non-verbal conduct of sexual nature."
        ),
        old_ipc_section="IPC Section 354A",
        punishment="Imprisonment up to 3 years, or fine, or both (for acts i-iii); imprisonment up to 1 year, or fine, or both (for acts iv-v)",
        bailable=True,
        cognizable=True,
    ),
    76: BNSSection(
        section_number=76,
        title="Assault or use of criminal force to woman with intent to disrobe",
        description=(
            "Any man who assaults or uses criminal force to any woman "
            "or abets such act with the intention of disrobing or "
            "compelling her to be naked."
        ),
        old_ipc_section="IPC Section 354B",
        punishment="Imprisonment not less than 3 years extendable to 7 years, and fine",
        bailable=False,
        cognizable=True,
    ),
    77: BNSSection(
        section_number=77,
        title="Voyeurism",
        description=(
            "Any man who watches, or captures the image of a woman "
            "engaging in a private act in circumstances where she would "
            "usually have the expectation of not being observed."
        ),
        old_ipc_section="IPC Section 354C",
        punishment="First offence: imprisonment 1-3 years, and fine; Second offence: imprisonment 3-7 years, and fine",
        bailable=False,
        cognizable=True,
    ),
    78: BNSSection(
        section_number=78,
        title="Stalking",
        description=(
            "Any man who follows a woman and contacts, or attempts to "
            "contact such woman to foster personal interaction despite "
            "clear indication of disinterest, or monitors the use by "
            "a woman of the internet, email or any other form of "
            "electronic communication."
        ),
        old_ipc_section="IPC Section 354D",
        punishment="First offence: imprisonment up to 3 years, and fine; Second offence: imprisonment up to 5 years, and fine",
        bailable=True,
        cognizable=True,
    ),
    79: BNSSection(
        section_number=79,
        title="Word, gesture or act intended to insult the modesty of a woman",
        description=(
            "Whoever intending to insult the modesty of any woman, "
            "utters any word, makes any sound or gesture, or exhibits "
            "any object, intending that such word or sound shall be "
            "heard, or that such gesture or object shall be seen, by "
            "such woman, or intrudes upon the privacy of such woman."
        ),
        old_ipc_section="IPC Section 509",
        punishment="Imprisonment up to 3 years and fine",
        bailable=True,
        cognizable=True,
    ),
    # -- Dowry --
    80: BNSSection(
        section_number=80,
        title="Dowry death",
        description=(
            "Where the death of a woman is caused by any burns or bodily "
            "injury or occurs otherwise than under normal circumstances "
            "within seven years of her marriage and it is shown that "
            "soon before her death she was subjected to cruelty or "
            "harassment by her husband or any relative of her husband "
            "for, or in connection with, any demand for dowry."
        ),
        old_ipc_section="IPC Section 304B",
        punishment="Imprisonment not less than 7 years extendable to imprisonment for life",
        bailable=False,
        cognizable=True,
    ),
    84: BNSSection(
        section_number=84,
        title="Cruelty by husband or his relatives",
        description=(
            "Whoever, being the husband or the relative of the husband "
            "of a woman, subjects such woman to cruelty, including any "
            "wilful conduct which is of such a nature as is likely to "
            "drive the woman to commit suicide or to cause grave injury "
            "or danger to life, limb or health, or harassment with a "
            "view to coercing her or any person related to her to meet "
            "any unlawful demand for any property or valuable security."
        ),
        old_ipc_section="IPC Section 498A",
        punishment="Imprisonment up to 3 years and fine",
        bailable=False,
        cognizable=True,
    ),
    # -- Kidnapping and abduction --
    137: BNSSection(
        section_number=137,
        title="Kidnapping",
        description=(
            "Kidnapping from India -- conveying any person beyond the "
            "limits of India without the consent of that person. "
            "Kidnapping from lawful guardianship -- taking or enticing "
            "any minor (under 16 if male, under 18 if female) or any "
            "person of unsound mind, out of the keeping of the lawful "
            "guardian."
        ),
        old_ipc_section="IPC Sections 359-361",
        punishment="Imprisonment up to 7 years and fine",
        bailable=False,
        cognizable=True,
    ),
    140: BNSSection(
        section_number=140,
        title="Kidnapping or abducting a woman to compel her marriage etc.",
        description=(
            "Whoever kidnaps or abducts any woman with intent that she "
            "may be compelled, or knowing it to be likely that she will "
            "be compelled, to marry any person against her will."
        ),
        old_ipc_section="IPC Section 366",
        punishment="Imprisonment up to 10 years and fine",
        bailable=False,
        cognizable=True,
    ),
    # -- Theft and property offences --
    303: BNSSection(
        section_number=303,
        title="Theft",
        description=(
            "Whoever, intending to take dishonestly any moveable "
            "property out of the possession of any person without that "
            "person's consent, moves that property in order to such "
            "taking, is said to commit theft."
        ),
        old_ipc_section="IPC Section 378",
        punishment="Imprisonment up to 3 years, or fine, or both",
        bailable=False,
        cognizable=True,
    ),
    305: BNSSection(
        section_number=305,
        title="Snatching",
        description=(
            "Whoever commits theft with the intent to cause hurt or "
            "wrongful restraint or fear of hurt or wrongful restraint "
            "to any person, commits snatching."
        ),
        old_ipc_section="New provision (no IPC equivalent)",
        punishment="Imprisonment up to 3 years and fine",
        bailable=False,
        cognizable=True,
    ),
    309: BNSSection(
        section_number=309,
        title="Robbery",
        description=(
            "In all robbery there is either theft or extortion. When "
            "theft is robbery -- if in order to commit theft, or in "
            "committing the theft, or in carrying away or attempting to "
            "carry away property obtained by the theft, the offender "
            "voluntarily causes or attempts to cause death or hurt or "
            "wrongful restraint, or fear of instant death or instant "
            "hurt or instant wrongful restraint."
        ),
        old_ipc_section="IPC Section 390",
        punishment="RI up to 10 years and fine; if on highway between sunset and sunrise, up to 14 years",
        bailable=False,
        cognizable=True,
    ),
    310: BNSSection(
        section_number=310,
        title="Dacoity",
        description=(
            "When five or more persons conjointly commit or attempt to "
            "commit a robbery, every person so committing, attempting "
            "or aiding is said to commit dacoity."
        ),
        old_ipc_section="IPC Section 391",
        punishment="RI not less than 7 years",
        bailable=False,
        cognizable=True,
    ),
    # -- Criminal intimidation, defamation --
    351: BNSSection(
        section_number=351,
        title="Criminal intimidation",
        description=(
            "Whoever threatens another with any injury to his person, "
            "reputation, or property, or to the person or reputation of "
            "any one in whom that person is interested, with intent to "
            "cause alarm to that person."
        ),
        old_ipc_section="IPC Section 503",
        punishment="Imprisonment up to 2 years, or fine, or both",
        bailable=True,
        cognizable=False,
    ),
    356: BNSSection(
        section_number=356,
        title="Defamation",
        description=(
            "Whoever, by words either spoken or intended to be read, or "
            "by signs or by visible representations, makes or publishes "
            "any imputation concerning any person intending to harm, or "
            "knowing or having reason to believe that such imputation "
            "will harm, the reputation of such person."
        ),
        old_ipc_section="IPC Section 499",
        punishment="Simple imprisonment up to 2 years, or fine, or both",
        bailable=True,
        cognizable=False,
    ),
    # -- Cheating and fraud --
    318: BNSSection(
        section_number=318,
        title="Cheating",
        description=(
            "Whoever, by deceiving any person, fraudulently or "
            "dishonestly induces the person so deceived to deliver any "
            "property to any person, or to consent that any person shall "
            "retain any property, or intentionally induces the person "
            "so deceived to do or omit to do anything which he would "
            "not do or omit if he were not so deceived."
        ),
        old_ipc_section="IPC Section 415",
        punishment="Imprisonment up to 3 years, or fine, or both",
        bailable=False,
        cognizable=False,
    ),
    319: BNSSection(
        section_number=319,
        title="Cheating by personation",
        description=(
            "A person is said to cheat by personation if he cheats "
            "by pretending to be some other person, or by knowingly "
            "substituting one person for another, or representing that "
            "he or any other person is a person other than he or such "
            "other person really is."
        ),
        old_ipc_section="IPC Section 416",
        punishment="Imprisonment up to 5 years, or fine, or both",
        bailable=False,
        cognizable=False,
    ),
    316: BNSSection(
        section_number=316,
        title="Criminal breach of trust",
        description=(
            "Whoever, being in any manner entrusted with property, or "
            "with any dominion over property, dishonestly misappropriates "
            "or converts to his own use that property, or dishonestly "
            "uses or disposes of that property in violation of any "
            "direction of law or legal contract."
        ),
        old_ipc_section="IPC Section 405",
        punishment="Imprisonment up to 3 years, or fine, or both",
        bailable=False,
        cognizable=False,
    ),
    # -- Trespass --
    329: BNSSection(
        section_number=329,
        title="Criminal trespass",
        description=(
            "Whoever enters into or upon property in the possession of "
            "another with intent to commit an offence or to intimidate, "
            "insult or annoy any person in possession of such property."
        ),
        old_ipc_section="IPC Section 441",
        punishment="Imprisonment up to 3 months, or fine up to Rs 5,000, or both",
        bailable=True,
        cognizable=False,
    ),
    331: BNSSection(
        section_number=331,
        title="House-trespass",
        description=(
            "Whoever commits criminal trespass by entering into or "
            "remaining in any building, tent or vessel used as a human "
            "dwelling or any building used as a place for worship, or "
            "as a place for the custody of property."
        ),
        old_ipc_section="IPC Section 442",
        punishment="Imprisonment up to 1 year, or fine up to Rs 5,000, or both",
        bailable=True,
        cognizable=True,
    ),
    # -- Public tranquility --
    189: BNSSection(
        section_number=189,
        title="Unlawful assembly",
        description=(
            "An assembly of five or more persons is designated an "
            "unlawful assembly if the common object of the persons "
            "composing that assembly is to commit an offence, to resist "
            "the execution of any law, etc."
        ),
        old_ipc_section="IPC Section 141",
        punishment="Imprisonment up to 6 months, or fine, or both",
        bailable=True,
        cognizable=True,
    ),
    191: BNSSection(
        section_number=191,
        title="Rioting",
        description=(
            "Whenever force or violence is used by an unlawful assembly, "
            "or by any member thereof, in prosecution of the common "
            "object of such assembly, every member of such assembly is "
            "guilty of the offence of rioting."
        ),
        old_ipc_section="IPC Section 146",
        punishment="Imprisonment up to 2 years, or fine, or both",
        bailable=True,
        cognizable=True,
    ),
    # -- Public servant offences --
    197: BNSSection(
        section_number=197,
        title="Every member of unlawful assembly guilty of offence committed in prosecution of common object",
        description=(
            "If an offence is committed by any member of an unlawful "
            "assembly in prosecution of the common object of that "
            "assembly, every person who at the time of the committing "
            "of that offence is a member of the same assembly is guilty "
            "of that offence."
        ),
        old_ipc_section="IPC Section 149",
        punishment="Same as that for the offence committed",
        bailable=False,
        cognizable=True,
    ),
    # -- Offences by public servants --
    217: BNSSection(
        section_number=217,
        title="Public servant disobeying law with intent to cause injury",
        description=(
            "Whoever, being a public servant, knowingly disobeys any "
            "direction of the law as to the way in which he is to "
            "conduct himself as such public servant, intending to cause "
            "or knowing it to be likely that he will cause injury to "
            "any person."
        ),
        old_ipc_section="IPC Section 166",
        punishment="Imprisonment up to 1 year, or fine, or both",
        bailable=True,
        cognizable=False,
    ),
    # -- Organised crime (new provision) --
    111: BNSSection(
        section_number=111,
        title="Organised crime",
        description=(
            "Any continuing unlawful activity including kidnapping, "
            "extortion, contract killing, land grabbing, financial "
            "scams, cyber crimes committed as a member of, or on behalf "
            "of, an organised crime syndicate. This is a new provision "
            "with no direct IPC equivalent."
        ),
        old_ipc_section="New provision (no IPC equivalent)",
        punishment="Imprisonment for life or not less than 5 years and fine not less than Rs 5 lakh; death if organised crime results in death",
        bailable=False,
        cognizable=True,
    ),
    # -- Petty organized crime (new) --
    112: BNSSection(
        section_number=112,
        title="Petty organised crime",
        description=(
            "Any continuing unlawful activity like theft, snatching, "
            "cheating, unauthorized selling of tickets, public "
            "nuisance, etc., carried out by groups or gangs."
        ),
        old_ipc_section="New provision (no IPC equivalent)",
        punishment="Imprisonment from 1 to 7 years and fine",
        bailable=False,
        cognizable=True,
    ),
    # -- Offences related to SC/ST community --
    # (Note: SC/ST atrocities are primarily covered under the
    #  SC/ST (Prevention of Atrocities) Act, 1989, but BNS also
    #  covers general offences that may be committed with caste bias.)

    # -- Criminal force --
    131: BNSSection(
        section_number=131,
        title="Use of criminal force or assault to deter public servant from duty",
        description=(
            "Whoever assaults or uses criminal force to any person "
            "being a public servant in the execution of his duty as "
            "such public servant, or with intent to prevent or deter "
            "that person from discharging his duty."
        ),
        old_ipc_section="IPC Section 353",
        punishment="Imprisonment up to 2 years, or fine, or both",
        bailable=False,
        cognizable=True,
    ),
    # -- Wrongful confinement --
    127: BNSSection(
        section_number=127,
        title="Wrongful confinement",
        description=(
            "Whoever wrongfully restrains any person in such a manner "
            "as to prevent that person from proceeding beyond certain "
            "circumscribing limits, is said wrongfully to confine that "
            "person."
        ),
        old_ipc_section="IPC Section 340",
        punishment="Imprisonment up to 1 year, or fine up to Rs 5,000, or both",
        bailable=True,
        cognizable=False,
    ),
}

# Build a lookup index by section number
_BNS_SECTION_INDEX: Final[dict[int, BNSSection]] = _BNS_SECTIONS


# =====================================================================
# BNSS (Bharatiya Nagarik Suraksha Sanhita) Key References
# =====================================================================

_BNSS_REFERENCES: Final[dict[str, str]] = {
    "Zero FIR": (
        "BNSS Section 173(1): Any police station must register an FIR "
        "irrespective of jurisdiction. The FIR is then transferred to "
        "the jurisdictional police station. (Replaces CrPC Section 154)"
    ),
    "E-FIR": (
        "BNSS Section 173: FIR can be filed electronically. Informant "
        "must sign within 3 days. (New provision)"
    ),
    "Mandatory information to victim": (
        "BNSS Section 193: Police must inform the victim about the "
        "progress of investigation within 90 days. (New provision)"
    ),
    "Bail": (
        "BNSS Section 480: Provisions relating to bail. First-time "
        "offenders who have served one-third of the maximum sentence "
        "are eligible for bail. (Replaces CrPC Sections 436-439)"
    ),
    "Mercy petition timeline": (
        "BNSS Section 472: Mercy petition must be disposed of within "
        "60 days by the Governor and within 30 days by the President. "
        "(New provision)"
    ),
    "Arrest provisions": (
        "BNSS Section 35: Provisions relating to arrest. Police must "
        "inform the arrested person of the grounds of arrest and their "
        "right to legal representation. (Replaces CrPC Section 41)"
    ),
    "Police custody": (
        "BNSS Section 187: Police custody can now extend up to 15 days "
        "at any time during initial 40 or 60 days of remand (not "
        "necessarily first 15 days). (Changed from CrPC Section 167)"
    ),
    "Summons and e-service": (
        "BNSS Section 64: Summons can be served electronically. "
        "(New provision)"
    ),
    "Victim's right to appeal": (
        "BNSS Section 397: Victim has the right to be heard before "
        "withdrawal of prosecution. (Replaces CrPC Section 321)"
    ),
    "Forensic investigation mandatory": (
        "BNSS Section 176: Mandatory forensic expert visit to crime "
        "scene for offences punishable with 7+ years imprisonment. "
        "(New provision)"
    ),
    "Statement by electronic means": (
        "BNSS Section 175: Statements of witnesses can be recorded "
        "through audio-video electronic means. (New provision)"
    ),
}


# =====================================================================
# BSA (Bharatiya Sakshya Adhiniyam) Key References
# =====================================================================

_BSA_REFERENCES: Final[dict[str, str]] = {
    "Electronic evidence": (
        "BSA Section 57: Electronic records are admissible as evidence. "
        "Requires a certificate (Section 63) for authentication. "
        "(Replaces Indian Evidence Act Section 65B)"
    ),
    "Joint trials": (
        "BSA provisions allow for evidence in joint trials and "
        "cross-examination via electronic means."
    ),
    "DNA evidence": (
        "BSA Section 46: DNA profiling recognized as admissible "
        "evidence. (New explicit provision)"
    ),
    "Oral evidence via electronic means": (
        "BSA Section 20: Oral evidence may be given through electronic "
        "means as prescribed. (Enhanced from Indian Evidence Act)"
    ),
}


# =====================================================================
# Helpline Database
# =====================================================================

_HELPLINES: Final[dict[str, list[HelplineInfo]]] = {
    "general": [
        HelplineInfo(
            name="Police (Emergency)",
            number="112",
            description="National emergency number for police, fire, and ambulance",
            hours="24x7",
            languages=["Hindi", "English", "Regional"],
        ),
        HelplineInfo(
            name="Police",
            number="100",
            description="Police emergency helpline",
            hours="24x7",
            languages=["Hindi", "English", "Regional"],
        ),
        HelplineInfo(
            name="NALSA (National Legal Services Authority)",
            number="15100",
            description="Free legal aid helpline for eligible persons under Legal Services Authorities Act, 1987",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="NHRC (National Human Rights Commission)",
            number="14433",
            description="Human rights violations complaint helpline",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="Anti-Corruption Helpline (CVC)",
            number="1964",
            description="Central Vigilance Commission helpline for reporting corruption",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
    ],
    "women": [
        HelplineInfo(
            name="Women Helpline (Universal)",
            number="181",
            description="24-hour helpline for women in distress -- domestic violence, harassment, abuse, dowry",
            hours="24x7",
            languages=["Hindi", "English", "Regional"],
        ),
        HelplineInfo(
            name="Women Helpline (Domestic Abuse)",
            number="1091",
            description="Police helpline specifically for women facing domestic abuse",
            hours="24x7",
            languages=["Hindi", "English", "Regional"],
        ),
        HelplineInfo(
            name="NCW (National Commission for Women)",
            number="7827-170-170",
            description="WhatsApp number for registering complaints with NCW",
            hours="9:00 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="One Stop Centre (Sakhi)",
            number="181",
            description="Integrated support for women affected by violence -- medical, legal, police, psycho-social counselling",
            hours="24x7",
            languages=["Hindi", "English", "Regional"],
        ),
        HelplineInfo(
            name="She-Box (Sexual Harassment Electronic Box)",
            number="shebox.nic.in",
            description="Online portal for registering workplace sexual harassment complaints",
            hours="24x7 (online)",
            languages=["Hindi", "English"],
        ),
    ],
    "children": [
        HelplineInfo(
            name="Childline",
            number="1098",
            description="24-hour emergency helpline for children in distress -- abuse, trafficking, child marriage, child labour",
            hours="24x7",
            languages=["Hindi", "English", "Regional"],
        ),
        HelplineInfo(
            name="NCPCR (National Commission for Protection of Child Rights)",
            number="1800-121-2830",
            description="Toll-free helpline for child rights violations",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="Missing Children",
            number="1094",
            description="Helpline for reporting and tracing missing children",
            hours="24x7",
            languages=["Hindi", "English", "Regional"],
        ),
    ],
    "sc_st": [
        HelplineInfo(
            name="SC/ST Helpline",
            number="14566",
            description="National helpline for atrocities against Scheduled Castes and Scheduled Tribes",
            hours="24x7",
            languages=["Hindi", "English", "Regional"],
        ),
        HelplineInfo(
            name="National Commission for Scheduled Castes",
            number="011-23389476",
            description="NCSC helpline for filing complaints about atrocities or discrimination",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="National Commission for Scheduled Tribes",
            number="011-23389476",
            description="NCST helpline for tribal rights violations and displacement",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
    ],
    "labor": [
        HelplineInfo(
            name="Labour Helpline (Shram Suvidha)",
            number="1800-11-0039",
            description="Toll-free helpline for labour rights -- wages, working conditions, industrial disputes",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="EPFO (Provident Fund)",
            number="1800-118-005",
            description="Employee Provident Fund Organisation helpline",
            hours="9:15 AM - 5:45 PM",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="ESIC (Employees' State Insurance)",
            number="1800-11-2526",
            description="ESIC helpline for employee health insurance",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="Anti-Human Trafficking",
            number="1800-419-8588",
            description="Helpline for reporting human trafficking and bonded labour",
            hours="24x7",
            languages=["Hindi", "English", "Regional"],
        ),
    ],
    "consumer": [
        HelplineInfo(
            name="National Consumer Helpline",
            number="1915",
            description="Toll-free helpline for consumer complaints -- defective products, deficient services, unfair trade practices",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="FSSAI (Food Safety)",
            number="1800-112-100",
            description="Food adulteration and food safety complaints",
            hours="9:00 AM - 6:00 PM",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="TRAI (Telecom)",
            number="1800-11-5656",
            description="Telecom regulatory complaints -- spam calls, network issues",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
    ],
    "cyber_crime": [
        HelplineInfo(
            name="Cyber Crime Helpline",
            number="1930",
            description="National helpline for reporting cyber crimes -- online fraud, identity theft, cyberstalking, UPI fraud",
            hours="24x7",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="Cyber Crime Reporting Portal",
            number="cybercrime.gov.in",
            description="Online portal for filing cyber crime FIRs -- especially for crimes against women and children",
            hours="24x7 (online)",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="RBI Sachet (Financial Fraud)",
            number="sachet.rbi.org.in",
            description="Portal for reporting unauthorized digital lending, UPI fraud, phishing",
            hours="24x7 (online)",
            languages=["Hindi", "English"],
        ),
    ],
    "senior_citizen": [
        HelplineInfo(
            name="Elder Helpline",
            number="14567",
            description="National helpline for senior citizens -- abuse, pension, health, legal issues",
            hours="8:00 AM - 8:00 PM",
            languages=["Hindi", "English", "Regional"],
        ),
        HelplineInfo(
            name="NALSA Senior Citizen Legal Aid",
            number="15100",
            description="Free legal aid for senior citizens under Legal Services Authorities Act",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
    ],
    "disability": [
        HelplineInfo(
            name="Disability Helpline",
            number="1800-11-5577",
            description="Helpline for persons with disabilities -- rights, entitlements, accessibility complaints under RPwD Act 2016",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English"],
        ),
        HelplineInfo(
            name="NIEPMD (National Institute for Empowerment of Persons with Multiple Disabilities)",
            number="044-2744-9737",
            description="Support for persons with multiple disabilities",
            hours="9:30 AM - 5:30 PM",
            languages=["Hindi", "English", "Tamil"],
        ),
        HelplineInfo(
            name="Mental Health Helpline (NIMHANS)",
            number="080-46110007",
            description="Mental health support and counselling",
            hours="24x7",
            languages=["Hindi", "English", "Kannada"],
        ),
    ],
}


# =====================================================================
# LLM Analysis Prompt
# =====================================================================

_LEGAL_ANALYSIS_SYSTEM_INSTRUCTION: Final[str] = """\
You are a legal awareness assistant for HaqSetu, a voice-first civic \
assistant for rural India. Your role is to help citizens understand \
their legal rights and applicable laws.

IMPORTANT RULES:
1. You are NOT a lawyer. You provide EDUCATIONAL information only.
2. Always recommend consulting DLSA (District Legal Services Authority) \
   or a qualified lawyer.
3. Always mention NALSA helpline 15100 for free legal aid.
4. Reference the NEW criminal laws effective 1 July 2024:
   - BNS (Bharat Nyaya Sanhita, 2023) replaces IPC
   - BNSS (Bharatiya Nagarik Suraksha Sanhita, 2023) replaces CrPC
   - BSA (Bharatiya Sakshya Adhiniyam, 2023) replaces Indian Evidence Act
5. Use simple language suitable for people with limited formal education.
6. For each law, provide both the new BNS section AND the old IPC section.
7. Consider fundamental rights, constitutional provisions, and special \
   statutes (SC/ST Act, POCSO, PWDV Act, RTI Act, etc.).
"""

_LEGAL_ANALYSIS_PROMPT_TEMPLATE: Final[str] = """\
A citizen has described their situation and needs to understand which \
laws and rights may be applicable. Analyze the situation and respond \
STRICTLY in the JSON format below. Do NOT include any text outside \
the JSON.

SITUATION: {situation}

Respond with this exact JSON structure:
{{
    "situation_summary": "A brief summary of the situation in simple language",
    "severity": "low|medium|high|critical",
    "applicable_laws": [
        {{
            "law": "Short name of the law/section",
            "description": "What this law says in simple language",
            "relevance": "Why this law applies to the situation",
            "bns_section": "BNS Section number (or null if not BNS)",
            "act_name": "Full name of the Act"
        }}
    ],
    "applicable_rights": [
        {{
            "right_name": "Name of the right",
            "source_law": "Which law grants this right",
            "description": "What this right means in simple language",
            "how_to_exercise": "Step-by-step how to use this right"
        }}
    ],
    "recommended_actions": [
        "Action 1 the citizen should take",
        "Action 2 the citizen should take"
    ],
    "helpline_categories": ["general", "women", "children", "sc_st", \
"labor", "consumer", "cyber_crime", "senior_citizen", "disability"]
}}

GUIDELINES:
- Include at least 2-5 applicable laws
- Include at least 1-3 applicable rights
- Include at least 3-5 recommended actions
- For helpline_categories, list ONLY the relevant categories from the \
  given list
- Severity: "critical" = immediate danger, "high" = serious legal \
  violation, "medium" = rights being affected, "low" = informational
- Always include the right to free legal aid and the right to file FIR
- Reference BNS sections where applicable (with old IPC equivalents)
- Mention BNSS provisions for procedural rights (Zero FIR, E-FIR, etc.)
"""


# =====================================================================
# Service
# =====================================================================


class LegalRightsService:
    """Legal rights advisory service powered by Gemini LLM.

    Analyzes citizen-described situations to identify applicable laws,
    fundamental rights, and recommended actions under the new Indian
    criminal law framework (BNS/BNSS/BSA, effective 1 July 2024).

    Parameters
    ----------
    llm:
        An instance of :class:`~src.services.llm.LLMService` used for
        situation analysis.
    """

    def __init__(self, llm: LLMService) -> None:
        self._llm = llm
        logger.info("legal_rights_service.initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def identify_applicable_laws(self, situation: str) -> LegalAnalysis:
        """Analyze a situation and identify applicable laws and rights.

        Uses the Gemini LLM to understand the citizen's described
        situation and map it to relevant BNS sections, constitutional
        rights, special statutes, and helplines.

        Parameters
        ----------
        situation:
            The citizen's description of their problem, in their own
            words (may be in Hindi, Hinglish, or English).

        Returns
        -------
        LegalAnalysis
            Comprehensive analysis with laws, rights, actions, and
            helplines.
        """
        logger.info(
            "legal_rights.identify_start",
            situation_length=len(situation),
        )

        prompt = _LEGAL_ANALYSIS_PROMPT_TEMPLATE.format(situation=situation)

        try:
            llm_result = await self._llm.generate(
                prompt=prompt,
                context=_LEGAL_ANALYSIS_SYSTEM_INSTRUCTION,
                temperature=0.2,
            )
            raw_response = llm_result.answer
        except Exception:
            logger.error("legal_rights.llm_failed", exc_info=True)
            return self._build_fallback_analysis(situation)

        # Parse the LLM JSON response
        try:
            parsed = self._parse_llm_response(raw_response)
        except Exception:
            logger.error(
                "legal_rights.parse_failed",
                raw_response_length=len(raw_response),
                exc_info=True,
            )
            return self._build_fallback_analysis(situation)

        # Build the structured analysis from parsed JSON
        analysis = self._build_analysis_from_parsed(parsed, situation)

        logger.info(
            "legal_rights.identify_complete",
            num_laws=len(analysis.applicable_laws),
            num_rights=len(analysis.applicable_rights),
            severity=analysis.severity,
        )

        return analysis

    def get_helplines(self, category: str) -> list[HelplineInfo]:
        """Get helpline numbers for a given category.

        Parameters
        ----------
        category:
            One of: general, women, children, sc_st, labor, consumer,
            cyber_crime, senior_citizen, disability.

        Returns
        -------
        list[HelplineInfo]
            List of helplines for the category. Falls back to general
            helplines if the category is not recognized.
        """
        category_lower = category.lower().strip()
        helplines = _HELPLINES.get(category_lower)

        if helplines is None:
            logger.warning(
                "legal_rights.unknown_helpline_category",
                category=category,
            )
            helplines = _HELPLINES["general"]

        return helplines

    def get_bns_section(self, section_number: int) -> BNSSection | None:
        """Look up a BNS section by number.

        Parameters
        ----------
        section_number:
            The BNS section number to look up.

        Returns
        -------
        BNSSection | None
            The section information, or ``None`` if the section is not
            in the database.
        """
        section = _BNS_SECTION_INDEX.get(section_number)

        if section is None:
            logger.info(
                "legal_rights.bns_section_not_found",
                section_number=section_number,
            )

        return section

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_llm_response(raw: str) -> dict:
        """Extract and parse JSON from the LLM response.

        The LLM may wrap the JSON in markdown code fences or include
        extra text. This method tries several strategies to extract
        valid JSON.
        """
        # Strategy 1: Try direct parse
        stripped = raw.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Extract from markdown code fence
        fence_match = re.search(
            r"```(?:json)?\s*\n?(.*?)```",
            stripped,
            re.DOTALL,
        )
        if fence_match:
            try:
                return json.loads(fence_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Strategy 3: Find first { ... last }
        first_brace = stripped.find("{")
        last_brace = stripped.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            try:
                return json.loads(stripped[first_brace : last_brace + 1])
            except json.JSONDecodeError:
                pass

        msg = "Could not parse JSON from LLM response"
        raise ValueError(msg)

    def _build_analysis_from_parsed(
        self,
        parsed: dict,
        situation: str,
    ) -> LegalAnalysis:
        """Convert parsed LLM JSON into a structured ``LegalAnalysis``."""
        # -- situation summary --
        situation_summary = parsed.get(
            "situation_summary",
            situation[:200] + "..." if len(situation) > 200 else situation,
        )

        # -- severity --
        severity = parsed.get("severity", "medium").lower()
        if severity not in ("low", "medium", "high", "critical"):
            severity = "medium"

        # -- applicable laws --
        applicable_laws: list[ApplicableLaw] = []
        for law_data in parsed.get("applicable_laws", []):
            applicable_laws.append(
                ApplicableLaw(
                    law=law_data.get("law", ""),
                    description=law_data.get("description", ""),
                    relevance=law_data.get("relevance", ""),
                    bns_section=law_data.get("bns_section"),
                    act_name=law_data.get("act_name", ""),
                )
            )

        # -- applicable rights --
        applicable_rights: list[ApplicableRight] = []
        for right_data in parsed.get("applicable_rights", []):
            applicable_rights.append(
                ApplicableRight(
                    right_name=right_data.get("right_name", ""),
                    source_law=right_data.get("source_law", ""),
                    description=right_data.get("description", ""),
                    how_to_exercise=right_data.get("how_to_exercise", ""),
                )
            )

        # Always ensure right to free legal aid is included
        has_legal_aid = any(
            "legal aid" in r.right_name.lower() or "nalsa" in r.source_law.lower()
            for r in applicable_rights
        )
        if not has_legal_aid:
            applicable_rights.append(
                ApplicableRight(
                    right_name="Right to Free Legal Aid",
                    source_law="Legal Services Authorities Act, 1987; Article 39A of the Constitution",
                    description=(
                        "Every person who cannot afford a lawyer has the right to "
                        "free legal aid. This includes women, children, SC/ST members, "
                        "persons with disabilities, industrial workmen, persons in "
                        "custody, victims of trafficking, persons with annual income "
                        "below Rs 3 lakh (varies by state), and others."
                    ),
                    how_to_exercise=(
                        "1. Call NALSA helpline 15100. "
                        "2. Visit your nearest District Legal Services Authority (DLSA) office. "
                        "3. Apply online at nalsa.gov.in. "
                        "4. Visit your nearest Taluk Legal Services Committee."
                    ),
                )
            )

        # -- recommended actions --
        recommended_actions: list[str] = parsed.get("recommended_actions", [])
        if not recommended_actions:
            recommended_actions = [
                "Contact NALSA helpline 15100 for free legal aid.",
                "Visit your nearest District Legal Services Authority (DLSA) office.",
                "File an FIR / Zero FIR at any police station (BNSS Section 173).",
            ]

        # -- helplines --
        helpline_categories: list[str] = parsed.get(
            "helpline_categories", ["general"]
        )
        helplines: list[HelplineInfo] = []
        seen_numbers: set[str] = set()
        for cat in helpline_categories:
            cat_helplines = _HELPLINES.get(cat.lower().strip(), [])
            for h in cat_helplines:
                if h.number not in seen_numbers:
                    helplines.append(h)
                    seen_numbers.add(h.number)

        # Always include general helplines
        if "general" not in [c.lower().strip() for c in helpline_categories]:
            for h in _HELPLINES.get("general", []):
                if h.number not in seen_numbers:
                    helplines.append(h)
                    seen_numbers.add(h.number)

        return LegalAnalysis(
            situation_summary=situation_summary,
            applicable_laws=applicable_laws,
            applicable_rights=applicable_rights,
            recommended_actions=recommended_actions,
            helplines=helplines,
            severity=severity,
            disclaimer=_LEGAL_DISCLAIMER,
        )

    @staticmethod
    def _build_fallback_analysis(situation: str) -> LegalAnalysis:
        """Build a safe fallback analysis when LLM fails.

        Even when the AI analysis fails, we return useful general
        information so the citizen is never left without help.
        """
        summary = (
            situation[:200] + "..." if len(situation) > 200 else situation
        )
        return LegalAnalysis(
            situation_summary=summary,
            applicable_laws=[
                ApplicableLaw(
                    law="Right to file FIR",
                    description=(
                        "Every citizen has the right to file a First Information "
                        "Report (FIR) at any police station. Under BNSS Section "
                        "173, police must register a Zero FIR even if the offence "
                        "occurred outside their jurisdiction."
                    ),
                    relevance="Applicable to all criminal complaints",
                    bns_section=None,
                    act_name="Bharatiya Nagarik Suraksha Sanhita, 2023 (BNSS)",
                ),
                ApplicableLaw(
                    law="Right to Information",
                    description=(
                        "Under the RTI Act, 2005, every citizen can seek "
                        "information from any public authority. RTI fee is Rs 10."
                    ),
                    relevance="Useful if government services are being denied or delayed",
                    bns_section=None,
                    act_name="Right to Information Act, 2005",
                ),
            ],
            applicable_rights=[
                ApplicableRight(
                    right_name="Right to Free Legal Aid",
                    source_law="Legal Services Authorities Act, 1987; Article 39A of the Constitution",
                    description=(
                        "Every person who cannot afford a lawyer has the right to "
                        "free legal aid. This includes women, children, SC/ST "
                        "members, persons with disabilities, industrial workmen, "
                        "persons in custody, victims of trafficking, and persons "
                        "with annual income below Rs 3 lakh (varies by state)."
                    ),
                    how_to_exercise=(
                        "1. Call NALSA helpline 15100. "
                        "2. Visit your nearest District Legal Services Authority (DLSA). "
                        "3. Apply online at nalsa.gov.in."
                    ),
                ),
                ApplicableRight(
                    right_name="Right to Equality (Article 14)",
                    source_law="Constitution of India, Article 14",
                    description=(
                        "The State shall not deny to any person equality before "
                        "the law or the equal protection of the laws within the "
                        "territory of India."
                    ),
                    how_to_exercise=(
                        "If you face discrimination, file a complaint with the "
                        "relevant National Commission (Women, SC/ST, Minorities) "
                        "or approach the NHRC."
                    ),
                ),
            ],
            recommended_actions=[
                "Call NALSA helpline 15100 for free legal aid and guidance.",
                "Visit your nearest District Legal Services Authority (DLSA) office for in-person help.",
                "If there is any criminal offence, file an FIR / Zero FIR at the nearest police station (BNSS Section 173).",
                "Call Police emergency number 112 if you are in immediate danger.",
                "Gather and preserve all relevant documents, messages, photos, or videos as evidence.",
            ],
            helplines=_HELPLINES["general"],
            severity="medium",
            disclaimer=_LEGAL_DISCLAIMER,
        )
