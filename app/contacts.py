"""Classify e-mail addresses found in compliance documents.

A PURE FUNCTION LIBRARY: addresses and authored domains in, a verdict per
address out. No I/O, no DB, no network. The CLI (`dentalia mine-contacts`)
reads `document_text`, calls `classify`, and writes only what comes back
`manufacturer`; everything else is reported for a person.

**Why triage at all.** A compliance document names far more parties than its
manufacturer. Measured over the 937 documents whose text we hold (2026-09-03):
34 addresses across 15 manufacturers, of which roughly a third belong to
notified bodies and regulators -- BSI, TUV, DNV, DQS, RISE, the FDA -- and
several name a DIFFERENT company than the document is filed under. Writing all
of them into `manufacturer.contact_emails` would point renewal requests at the
certifier instead of the maker, which is worse than having no address: it is a
wrong address that looks right.

**Why the domain match is loose.** An authored playbook domain is the one the
document library lives on, not the one the company sends mail from. VOCO
authored `voco.dental` and writes from `voco.de` and `voco.com`; HENRY SCHEIN
authored `henryschein.de` and writes from `henryschein.com`; EDENTA authored
`edenta.com` and also uses `edenta.ch`. Comparing full domains would reject all
three. So the comparison is on the **registrable label** -- the name without its
public suffix -- which is what a human actually reads as "the company's domain".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Certifiers, notified bodies, accreditation bodies and regulators. These
#: appear IN a compliance document by necessity -- a declaration cites the body
#: that issued the certificate -- and are never the manufacturer to write to.
#: Matched on the registrable label so `de.tuv.com` and `tuv-nord.com` both
#: land here. Curated rather than inferred: the "a domain seen under three or
#: more manufacturers is a third party" heuristic is sound in principle but
#: needs a bigger corpus than we have (measured 2026-09-03: BSI appeared under
#: two manufacturers, DNV under two, TUV and the FDA under one each).
THIRD_PARTY_LABELS = frozenset({
    # notified bodies / certifiers
    "bsigroup", "bsi", "dnv", "dnvgl", "tuv", "tuev", "tuv-nord", "tuev-nord",
    "tuvsud", "tuv-sud", "dqs-med", "dqs", "ri", "sgs", "intertek", "ul",
    "kiwa", "mdc-ce", "eurofins", "nsai", "ecm-cert", "imq", "icim", "cermet",
    "presafe", "medcert", "lne-gmed", "gmed",
    # regulators
    "fda", "ema", "ec", "europa", "swissmedic", "mhra", "bfarm",
    # regulatory consultancies and authorised representatives
    "medicept", "emergobyul", "emergogroup", "obelis", "casus", "medicalvision",
})

#: Mailbox names that survive staff turnover. A renewal request sent to
#: `info@` still arrives in three years; one sent to a named person may not.
#: Used to RANK, never to filter -- a personal address at the right company is
#: still better than nothing.
ROLE_LOCALPARTS = frozenset({
    "info", "contact", "office", "service", "support", "sales", "mail",
    "regulatory", "ra", "qa", "quality", "qm", "compliance", "vigilance",
    "kontakt", "zentrale", "customerservice", "customer-service",
    "dealersupport", "dealer-support", "cs", "help", "enquiries",
})

#: A local part that names an individual rather than a function. Denis's
#: ruling 2026-09-03: personal addresses stay OUT of playbooks. A playbook is a
#: durable authored artifact in git -- a named employee goes stale, and their
#: address is personal data sitting in a repository.
#:
#: Detected by SHAPE rather than by allow-listing role words, and the
#: difference matters: `cbdeurope@henryschein.com` and `marketing@voco.com` are
#: not on any role list but are plainly departmental, and an allowlist would
#: have left HENRY SCHEIN with no address at all. Measured against the corpus
#: 2026-09-03, this catches four named-employee addresses (one
#: `firstname.lastname`, one `firstname_initial` and two `initial.surname`) and
#: keeps the other nine.
_PERSONAL_PATTERNS = (
    re.compile(r"^[a-z]\.[a-z]{2,}$"),        # a.novak
    re.compile(r"^[a-z]_[a-z]{2,}$"),         # p_kovac
    re.compile(r"^[a-z]{2,}_[a-z]$"),         # peter_k
    re.compile(r"^[a-z]{2,}\.[a-z]{2,}$"),    # jane.doe
    re.compile(r"^[a-z]{2,}_[a-z]{2,}$"),     # jane_doe
)

#: Words that make a two-part local part a FUNCTION, not a person, so
#: `customer.service@` and `medical.devices@` are never read as a name.
_FUNCTION_WORDS = frozenset({
    "customer", "service", "support", "medical", "devices", "device", "info",
    "contact", "sales", "quality", "regulatory", "affairs", "certificate",
    "verification", "product", "products", "technical", "tech", "help",
    "desk", "care", "team", "dental", "europe", "eu", "international",
    "order", "orders", "invoice", "accounts", "admin", "office", "post",
})


def looks_personal(local_part: str) -> bool:
    """Whether a local part names an individual rather than a function."""
    lp = local_part.lower()
    if lp in ROLE_LOCALPARTS:
        return False
    for pat in _PERSONAL_PATTERNS:
        if pat.match(lp):
            parts = re.split(r"[._]", lp)
            if any(p in _FUNCTION_WORDS for p in parts):
                return False
            return True
    return False


#: Public suffixes that are two labels deep, so the registrable label is the
#: THIRD from the right (`example.co.uk` -> `example`). Not the full PSL: this
#: is the short list that actually occurs in dental-industry addresses, and a
#: miss degrades to `unknown` (a human looks) rather than to a wrong verdict.
_TWO_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "com.au", "co.jp", "co.nz", "com.br",
    "com.tr", "com.cn", "co.kr", "com.mx", "co.za", "com.sg", "te.ua",
    # A regulator sits under its department: `fda.hhs.gov` is the FDA, and
    # taking `hhs` as the label would miss the denylist entirely.
    "hhs.gov", "gov.uk", "go.jp",
})

_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

#: Verdicts. Only `manufacturer` is ever written.
MANUFACTURER = "manufacturer"
THIRD_PARTY = "third-party"
OTHER_MANUFACTURER = "other-manufacturer"
UNKNOWN = "unknown"

VERDICTS = (MANUFACTURER, THIRD_PARTY, OTHER_MANUFACTURER, UNKNOWN)


@dataclass(frozen=True)
class Verdict:
    address: str
    verdict: str
    reason: str
    #: For OTHER_MANUFACTURER, whose domain it actually matched. This is the
    #: interesting case: a document filed under DENTSPLY carrying a Zhermack
    #: address is either misfiled or a brand-parent relation nobody recorded.
    matched: str | None = None
    #: True for a mailbox that outlives a person. Ranking only.
    role: bool = False
    #: True for an address naming an individual. Such an address is a valid
    #: CONTACT but is never written to a playbook (Denis, 2026-09-03).
    personal: bool = False


def find_addresses(text: str) -> list[str]:
    """Every distinct address in a block of document text, lowercased."""
    if not text:
        return []
    seen, out = set(), []
    for m in _ADDRESS_RE.finditer(text):
        a = m.group(0).lower().rstrip(".")
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def registrable_label(domain: str) -> str:
    """The company-shaped part of a domain.

    `voco.de` -> `voco`; `voco.dental` -> `voco`; `de.tuv.com` -> `tuv`;
    `cad.medentika.com` -> `medentika`; `office@galit.te.ua` -> `galit`.
    """
    parts = [p for p in domain.lower().strip(".").split(".") if p]
    if len(parts) < 2:
        return parts[0] if parts else ""
    if ".".join(parts[-2:]) in _TWO_LABEL_SUFFIXES and len(parts) >= 3:
        return parts[-3]
    return parts[-2]


def _labels(domains) -> set[str]:
    """Registrable labels for a manufacturer's authored domains.

    A playbook domain may carry a path (`gc.dental/europe`,
    `straumann.com/medentika`); the path is not part of the host.
    """
    out = set()
    for d in domains or []:
        host = str(d).split("/", 1)[0].strip()
        if host:
            lab = registrable_label(host)
            if lab:
                out.add(lab)
    return out


def classify(address: str, own_domains, other_domains_by_manufacturer=None) -> Verdict:
    """One address, one verdict.

    `own_domains`      -- the manufacturer's authored playbook domains.
    `other_domains_by_manufacturer` -- {manufacturer: domains} for everyone
    else, so an address belonging to a DIFFERENT company we know about is
    named rather than dismissed as unknown.

    Order matters: third-party first, because a notified body is a third party
    whatever else it looks like, and a manufacturer whose own domain collided
    with a certifier label would be a data problem worth seeing, not a contact
    worth writing.
    """
    local, _, domain = address.partition("@")
    label = registrable_label(domain)
    is_role = local in ROLE_LOCALPARTS
    is_personal = looks_personal(local)

    if label in THIRD_PARTY_LABELS:
        return Verdict(address, THIRD_PARTY,
                       f"{label} is a notified body, regulator or consultancy",
                       role=is_role, personal=is_personal)

    if label and label in _labels(own_domains):
        return Verdict(address, MANUFACTURER,
                       f"domain matches this manufacturer's own ({label})",
                       role=is_role, personal=is_personal)

    for other, doms in (other_domains_by_manufacturer or {}).items():
        if label and label in _labels(doms):
            return Verdict(address, OTHER_MANUFACTURER,
                           f"domain belongs to {other}, not to this document's "
                           f"manufacturer — the document may be misfiled, or "
                           f"this may be an unrecorded brand-parent relation",
                           matched=other, role=is_role, personal=is_personal)

    return Verdict(address, UNKNOWN,
                   "no authored domain to check against — needs a human",
                   role=is_role, personal=is_personal)


def playbook_writable(verdicts) -> list[Verdict]:
    """The subset an authored playbook may carry.

    `manufacturer` and not personal. Everything else -- a certifier, another
    company\'s domain, an unrecognised one, or a named individual -- is
    reported for a human and never written.
    """
    return rank([v for v in verdicts
                 if v.verdict == MANUFACTURER and not v.personal])


def rank(verdicts) -> list[Verdict]:
    """Role mailboxes before personal ones, then alphabetical.

    Only meaningful within `manufacturer`; ordering is what decides which
    address a renewal request is addressed to first.
    """
    return sorted(verdicts, key=lambda v: (not v.role, v.address))


def validate_for_playbook(entries, *, where: str = "contacts") -> list[str]:
    """The single rule for what a playbook's `contacts` may contain.

    Called by BOTH doors -- `app/playbooks.py` when a file is loaded and
    `web/registry.py` when the editor saves -- because a rule enforced at one
    of them is a rule the other can violate. Lowercases, strips, deduplicates
    and preserves order; raises `ValueError` on anything a playbook must not
    carry.
    """
    if isinstance(entries, str) or not isinstance(entries, (list, tuple)):
        raise ValueError(f"{where} must be a list of addresses")
    out: list[str] = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"{where} entries must be non-empty strings")
        addr = entry.strip().lower()
        if find_addresses(addr) != [addr]:
            raise ValueError(f"{where}: {entry!r} is not an e-mail address")
        if looks_personal(addr.partition("@")[0]):
            raise ValueError(
                f"{where}: {addr!r} names an individual. Playbooks carry role "
                f"mailboxes only -- a named employee goes stale and their "
                f"address is personal data in a repository. Use a functional "
                f"address instead.")
        if addr not in out:
            out.append(addr)
    return out
