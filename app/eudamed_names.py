"""Attributing an EUDAMED actor to one of our canonical manufacturers.

Pure: no database, no fetcher, no config. Two callers need it -- the
`eudamed.certregister` handler and the confirm-queue UI -- and neither has any
business teaching this module about HTTP or Postgres.

Why matching happens here rather than in a query: `actorName=` on the
certificates endpoint filters, but as a SUBSTRING (`Ivo` -> 4 records,
`Ivoclar` -> 1, measured 2026-08-26). Querying per manufacturer would both
invite that trap and silently miss any manufacturer whose registered legal name
does not contain our canonical string -- `GC EUROPE N.V.` being the obvious
risk. The whole 4.608-row register is mirrored instead and matched locally.

Ruling (Denis, 2026-08-26): a normalised exact match auto-stores; anything else
is queued for a human, because a wrong attribution becomes a wrong e-mail to a
supplier.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

EXACT_VIA = "register-exact"
FUZZY_VIA = "register-fuzzy"

#: Below this, nothing is offered at all -- not even to a human. Set so that
#: punctuation and spacing variants of one company clear it while two unrelated
#: dental companies do not. Raising it shrinks the confirm queue and loses
#: attributions; lowering it fills the queue with noise a person must reject.
FUZZY_FLOOR = 88.0

#: Legal-entity suffixes carry no identity: `Ivoclar Vivadent AG` and
#: `IVOCLAR VIVADENT` are one company. Stripped from both sides before any
#: comparison. Deliberately conservative -- an unknown suffix is left in place
#: rather than guessed at, because dropping a real word would merge two
#: companies.
_SUFFIXES = {
    "ag", "gmbh", "co", "kg", "nv", "bv", "sa", "srl", "spa", "ltd", "limited",
    "llc", "inc", "corp", "corporation", "plc", "oy", "ab", "as", "aps", "sas",
    "sarl", "sl", "kft", "doo", "sp", "zoo", "pte", "pty",
}

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalise(name: str) -> str:
    """Casefold, strip accents and punctuation, drop legal suffixes, collapse
    whitespace. The result is a comparison key, never displayed."""
    if not name:
        return ""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.casefold()
    # Remove dots before general punctuation replacement: `A.G.` becomes `ag`,
    # not `a g`, so the suffix set can recognise and strip it. Tradeoff: decimal
    # numbers and domain-like tokens lose their separators (e.g., `3.196` -> `3196`,
    # `acme.com` -> `acmecom`). No playbook alias hits this today; reverting to
    # space-replacement would break abbreviation stripping, so this is accepted.
    folded = folded.replace(".", "")
    folded = _PUNCT.sub(" ", folded)
    words = [w for w in _SPACE.split(folded) if w]
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def match_actor(actor_name, candidates):
    """Attribute one EUDAMED actor name to a canonical manufacturer.

    `candidates` maps canonical name -> every raw alias that resolves to it.
    Returns `(canonical_name | None, discovered_via, score)`.

    Sorted iteration and `>` rather than `>=` on the score make the outcome
    deterministic when two canonical names are equally close -- a confirm queue
    that reshuffles on every sweep is a confirm queue nobody clears.
    """
    key = normalise(actor_name)
    if not key:
        return None, FUZZY_VIA, 0.0

    for canonical in sorted(candidates):
        for alias in candidates[canonical]:
            if normalise(alias) == key:
                return canonical, EXACT_VIA, 100.0

    best_name, best_score = None, 0.0
    for canonical in sorted(candidates):
        for alias in candidates[canonical]:
            score = fuzz.token_sort_ratio(key, normalise(alias))
            if score > best_score:
                best_name, best_score = canonical, float(score)

    if best_score >= FUZZY_FLOOR:
        return best_name, FUZZY_VIA, best_score
    return None, FUZZY_VIA, best_score
