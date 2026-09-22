"""The completeness rule: what a distributor must hold, per device class.

This is the knowledge that used to live in one person's head and in the shape
of a folder tree. It is written down here because the folder could not answer
"is anything missing?" and a person had to.

The matrix is NOT configuration. It is not in `app/config.py` and must never
move there: everything in that module is overridable by an env var or a TOML
overlay, and a legal obligation that can be switched off by `export` is not an
obligation. Changing these rules is a code change with a test diff.

Grounding, researched 2026-08-24 and recorded so the next reader does not have
to re-derive it:

- **Art. 14(2)** — before making a device available, a *distributor* verifies
  (a) CE marking affixed and the EU declaration of conformity drawn up,
  (b) the device is accompanied by the Art. 10(11) information (label AND
  instructions for use), (c) importer compliance, (d) a UDI has been assigned.
  The notified-body certificate is **not** on that list. Dentalia is a
  distributor, so DoC and IFU are the duties, and the certificate is evidence
  we are glad to hold rather than something we are answerable for.
- **ECJ C-10/24, judgment 4 June 2026** — narrows (a) to a plausibility check:
  where the documentation indicates notified-body involvement, the distributor
  verifies the four-digit NB number is present. Not that it is correct.
- **Art. 52(7)** — class I is self-declared, except (a) sterile, (b) measuring
  function, (c) reusable surgical instruments, where a notified body is
  involved for that aspect only. For (c) the scope explicitly includes "the
  related instructions for use", which is why Ir can never take the IFU
  exception below.
- **Annex I 23.1(d)** — instructions for use may be omitted for class I and
  class IIa devices that can be used safely without them, justified by risk
  analysis in the technical documentation. Narrow, documented, never a
  default — so a missing IFU on those classes is amber (check it), not red.

UDI is deliberately not scored (Denis, 2026-08-24). It is an Art. 14(2)(d)
duty, but the item side holds zero UDIs today, so scoring it would add a third
wall of red that teaches the reader to ignore the card.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.compliance import (
    CONDITIONAL,
    EVIDENCE,
    EVIDENCE_ROWS,
    EXPIRY_HORIZON_DAYS,
    NOT_APPLICABLE,
    REQUIRED,
    ROW_DOC,
    ROW_EC,
    ROW_IFU,
    ROW_ISO,
    ROW_NB,
    SCORED_ROWS,
    UNKNOWN,
    cell_state,
    requirements_for_class,
    worst_severity,
)

TODAY = date(2026, 8, 24)


def held(
    *,
    doc_status: str = "production",
    link_status: str = "production",
    match_basis: str = "ref-item",
    expires: date | None = None,
    expiry_basis: str | None = "stated",
) -> dict:
    """One production-shaped holding, overridable field by field."""
    return {
        "doc_status": doc_status,
        "link_status": link_status,
        "match_basis": match_basis,
        "expires": expires,
        "expiry_basis": expiry_basis,
    }


# --------------------------------------------------------------------------- #
# the matrix
# --------------------------------------------------------------------------- #
def test_scored_and_evidence_rows_are_disjoint():
    assert set(SCORED_ROWS) & set(EVIDENCE_ROWS) == set()
    assert SCORED_ROWS == (ROW_DOC, ROW_IFU, ROW_NB)
    assert EVIDENCE_ROWS == (ROW_EC, ROW_ISO)


@pytest.mark.parametrize(
    "product_class,doc,ifu,nb",
    [
        # Plain class I: self-declared. No notified body at all, so no number
        # to look for; the IFU exception is available to it.
        ("I", REQUIRED, CONDITIONAL, NOT_APPLICABLE),
        # Art. 52(7)(a)/(b)/(c): a notified body IS involved, so its number
        # must appear. For Ir the NB's remit covers the reprocessing IFU
        # itself, which is why Ir's IFU is REQUIRED and not CONDITIONAL.
        ("Is", REQUIRED, CONDITIONAL, REQUIRED),
        ("Im", REQUIRED, CONDITIONAL, REQUIRED),
        ("Ir", REQUIRED, REQUIRED, REQUIRED),
        # Annex I 23.1(d) reaches IIa but stops there.
        ("IIa", REQUIRED, CONDITIONAL, REQUIRED),
        ("IIb", REQUIRED, REQUIRED, REQUIRED),
        ("III", REQUIRED, REQUIRED, REQUIRED),
    ],
)
def test_requirements_by_class(product_class, doc, ifu, nb):
    req = requirements_for_class(product_class)
    assert req[ROW_DOC] == doc
    assert req[ROW_IFU] == ifu
    assert req[ROW_NB] == nb


def test_certificate_and_iso_are_evidence_for_every_class():
    for product_class in ("I", "Is", "Im", "Ir", "IIa", "IIb", "III", None):
        req = requirements_for_class(product_class)
        assert req[ROW_EC] == EVIDENCE, product_class
        assert req[ROW_ISO] == EVIDENCE, product_class


def test_unknown_class_still_requires_a_declaration():
    """A device with no class in BC is still a device: the DoC duty does not
    depend on the class, so it stays REQUIRED. What the class decides — the
    IFU exception and whether a notified body was involved at all — cannot be
    answered, and answering it green would be a guess."""
    req = requirements_for_class(None)
    assert req[ROW_DOC] == REQUIRED
    assert req[ROW_IFU] == UNKNOWN
    assert req[ROW_NB] == UNKNOWN


def test_unrecognised_class_string_is_treated_as_unknown_not_as_compliant():
    """A class BC has never sent before must not fall through to green."""
    req = requirements_for_class("IIc")
    assert req[ROW_IFU] == UNKNOWN
    assert req[ROW_NB] == UNKNOWN


# --------------------------------------------------------------------------- #
# cell states — what we hold
# --------------------------------------------------------------------------- #
def test_article_level_production_holding_is_green():
    cell = cell_state(REQUIRED, held(expires=date(2030, 1, 1)), today=TODAY)
    assert cell["state"] == "held"
    assert cell["severity"] == "ok"


def test_holding_with_no_expiry_is_green():
    """A declaration of conformity carries no expiry by regulation. Absence of
    a date is not a defect here — `document_effective_expiry` has already had
    its say by the time this function sees the row."""
    cell = cell_state(REQUIRED, held(expires=None), today=TODAY)
    assert cell["state"] == "held"
    assert cell["severity"] == "ok"


def test_holding_inside_the_horizon_is_amber():
    cell = cell_state(REQUIRED, held(expires=TODAY + timedelta(days=30)), today=TODAY)
    assert cell["state"] == "expiring"
    assert cell["severity"] == "warn"


def test_horizon_boundary_is_inclusive():
    on = cell_state(REQUIRED, held(expires=TODAY + timedelta(days=30)), today=TODAY)
    past = cell_state(REQUIRED, held(expires=TODAY + timedelta(days=31)), today=TODAY)
    assert on["state"] == "expiring"
    assert past["state"] == "held"


def test_expiry_horizon_follows_dentalias_own_renewal_window():
    """Drift guard. 30 days is Dentalia's ruling of 2026-08-18 ("bi rekla mesec
    dni prej"), and `Renewal.horizon_days` is where the scheduler reads it. Two
    places telling a reader different numbers about the same document is worse
    than either number being wrong."""
    from app.config import Renewal

    assert EXPIRY_HORIZON_DAYS == max(Renewal().horizon_days)


def test_expired_holding_is_red():
    cell = cell_state(REQUIRED, held(expires=TODAY - timedelta(days=1)), today=TODAY)
    assert cell["state"] == "expired"
    assert cell["severity"] == "bad"


def test_expiring_today_is_not_yet_expired():
    cell = cell_state(REQUIRED, held(expires=TODAY), today=TODAY)
    assert cell["state"] == "expiring"


def test_a_passed_review_horizon_is_not_an_expiry(self_check=None):
    """A declaration of conformity has no expiry under MDR — Annex IV requires
    only an issue date. The date `document_effective_expiry` gives such a
    document is `basis='staleness'`: Dentalia's own five-year review horizon
    (client ruling 2026-08-18), and migration 027 says in as many words that it
    is "a review horizon, not a legal expiry".

    139 of the registry's declarations lapse on that basis (2026-08-24) against
    157 that lapse on a date they actually claim. The minority case still gets
    its own state: telling a client that an article is non-compliant when what
    is true is that its declaration is due a look is the one error this card
    cannot afford to make."""
    cell = cell_state(
        REQUIRED,
        held(expires=TODAY - timedelta(days=1), expiry_basis="staleness"),
        today=TODAY,
    )
    assert cell["state"] == "review-due"
    assert cell["severity"] == "warn"


@pytest.mark.parametrize("basis", ["stated", "inherited"])
def test_a_real_expiry_date_is_still_an_expiry(basis):
    """Where the document (or the certificate it cites) actually claims a date,
    a passed date is a passed date."""
    cell = cell_state(
        REQUIRED,
        held(expires=TODAY - timedelta(days=1), expiry_basis=basis),
        today=TODAY,
    )
    assert cell["state"] == "expired"
    assert cell["severity"] == "bad"


# --------------------------------------------------------------------------- #
# cell states — the mfr-scope trap
# --------------------------------------------------------------------------- #
def test_manufacturer_scope_holding_is_never_green():
    """The whole reason this card exists. On 2026-08-24 a single ISO 13485
    certificate bound 2.567 Carl Martin articles by `mfr-scope`, and the
    coverage number read 99,7%. A quality-system claim about the supplier is
    not evidence about the article, and it must not be able to paint green."""
    cell = cell_state(
        REQUIRED, held(match_basis="mfr-scope", expires=date(2030, 1, 1)), today=TODAY
    )
    assert cell["state"] == "mfr-scope-only"
    assert cell["severity"] == "warn"


@pytest.mark.parametrize("basis", ["name-family", "fetch-context", "ref-catalogue"])
def test_structurally_staged_bases_read_as_review_not_as_held(basis):
    """Invariant 3 caps these at `staged` forever. Their link status says so;
    this asserts the card believes the status rather than the basis name."""
    cell = cell_state(REQUIRED, held(link_status="staged", match_basis=basis), today=TODAY)
    assert cell["state"] == "review"
    assert cell["severity"] == "warn"


def test_expired_manufacturer_scope_holding_reports_the_expiry():
    """Expiry outranks the scope caveat: an expired certificate is a harder
    fact than a weak link, and a warn would hide it."""
    cell = cell_state(
        REQUIRED,
        held(match_basis="mfr-scope", expires=TODAY - timedelta(days=1)),
        today=TODAY,
    )
    assert cell["state"] == "expired"
    assert cell["severity"] == "bad"


# --------------------------------------------------------------------------- #
# cell states — awaiting review
# --------------------------------------------------------------------------- #
def test_staged_document_is_awaiting_review():
    cell = cell_state(REQUIRED, held(doc_status="staged"), today=TODAY)
    assert cell["state"] == "review"
    assert cell["severity"] == "warn"


def test_staged_link_on_a_production_document_is_awaiting_review():
    cell = cell_state(REQUIRED, held(link_status="staged"), today=TODAY)
    assert cell["state"] == "review"


def test_superseded_document_does_not_count_as_held():
    """Supersession flips the document, never the link (item_detail.html says
    so and the registry agrees), so a superseded document can still be reached
    through a production link. It is not current evidence."""
    cell = cell_state(REQUIRED, held(doc_status="superseded"), today=TODAY)
    assert cell["state"] == "superseded"
    assert cell["severity"] == "bad"


# --------------------------------------------------------------------------- #
# cell states — nothing held
# --------------------------------------------------------------------------- #
def test_missing_required_row_is_red():
    cell = cell_state(REQUIRED, None, today=TODAY)
    assert cell["state"] == "missing"
    assert cell["severity"] == "bad"


def test_missing_conditional_row_is_amber_not_red():
    """Annex I 23.1(d): a class I or IIa device may legitimately ship without
    instructions for use. The exception is narrow and needs a justification in
    the technical documentation, so this is "go and check", not "you are in
    breach" — 1.663 articles would otherwise be accused wrongly."""
    cell = cell_state(CONDITIONAL, None, today=TODAY)
    assert cell["state"] == "missing-exception-possible"
    assert cell["severity"] == "warn"


def test_missing_evidence_row_is_not_scored_at_all():
    cell = cell_state(EVIDENCE, None, today=TODAY)
    assert cell["state"] == "missing"
    assert cell["severity"] == "none"


def test_not_applicable_row_is_grey_even_with_nothing_held():
    cell = cell_state(NOT_APPLICABLE, None, today=TODAY)
    assert cell["state"] == "not-required"
    assert cell["severity"] == "none"


def test_unknown_requirement_with_nothing_held_asks_for_the_class():
    cell = cell_state(UNKNOWN, None, today=TODAY)
    assert cell["state"] == "class-unknown"
    assert cell["severity"] == "warn"


def test_unknown_requirement_is_moot_once_the_document_is_held():
    """If we hold it, whether it was required stops mattering."""
    cell = cell_state(UNKNOWN, held(), today=TODAY)
    assert cell["state"] == "held"
    assert cell["severity"] == "ok"


def test_evidence_row_that_is_held_and_expired_stays_unscored():
    """An expired ISO 13485 certificate is worth knowing and is not a breach
    of anything Art. 14 asks of a distributor. It reports the expiry without
    escalating to `bad`."""
    cell = cell_state(EVIDENCE, held(expires=TODAY - timedelta(days=1)), today=TODAY)
    assert cell["state"] == "expired"
    assert cell["severity"] == "warn"


# --------------------------------------------------------------------------- #
# rollup
# --------------------------------------------------------------------------- #
def test_worst_severity_picks_the_worst():
    assert worst_severity(["ok", "warn", "bad"]) == "bad"
    assert worst_severity(["ok", "warn"]) == "warn"
    assert worst_severity(["ok", "none"]) == "ok"
    assert worst_severity(["none"]) == "none"


def test_worst_severity_of_nothing_is_none():
    assert worst_severity([]) == "none"


# --------------------------------------------------------------------------- #
# the query layer — against the real registry (CLAUDE.md: no mocking Postgres)
# --------------------------------------------------------------------------- #
from tests.fixtures.seed_ui import seed_document, seed_item, seed_link  # noqa: E402
from web.registry import item_completeness, manufacturer_completeness  # noqa: E402


def _alias(conn, raw: str, canonical: str) -> None:
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING",
        (raw, canonical),
    )


def _rows(card: dict) -> dict:
    return {r["row"]: r for r in card["rows"]}


def test_item_card_is_none_for_a_non_medical_device(conn):
    """The card answers an MDR question. A non-device has no answer, and a
    green card on a toothbrush handle would teach the reader that green is
    meaningless."""
    seed_item(conn, "NOTMD-1", manufacturer_raw="ACME", md_flag=False)
    assert item_completeness(conn, "NOTMD-1", today=TODAY) is None


def test_item_card_is_none_for_an_unknown_item(conn):
    assert item_completeness(conn, "NO-SUCH-ITEM", today=TODAY) is None


def test_article_level_declaration_turns_the_doc_row_green(conn):
    seed_item(conn, "IIA-1", manufacturer_raw="ACME", product_class="IIa")
    doc = seed_document(
        conn, content_hash="h-doc-green", archive_url="file:///a.pdf",
        doc_type="DoC", status="production", validity_to="2030-01-01",
        cert_number="0123",
    )
    seed_link(conn, "IIA-1", doc, match_basis="ref-item", status="production")

    rows = _rows(item_completeness(conn, "IIA-1", today=TODAY))
    assert rows[ROW_DOC]["state"] == "held"
    assert rows[ROW_DOC]["severity"] == "ok"
    assert rows[ROW_DOC]["doc_id"] == doc


def test_notified_body_row_reads_the_certificate_number_off_the_declaration(conn):
    """ECJ C-10/24: the distributor's duty is that the four-digit number is
    present, not that the certificate itself is held. So this row is satisfied
    by the DoC printing one."""
    seed_item(conn, "IIA-NB", manufacturer_raw="ACME", product_class="IIa")
    doc = seed_document(
        conn, content_hash="h-nb-yes", archive_url="file:///nb.pdf",
        doc_type="DoC", status="production", cert_number="0197",
    )
    seed_link(conn, "IIA-NB", doc, match_basis="ref-item", status="production")

    rows = _rows(item_completeness(conn, "IIA-NB", today=TODAY))
    assert rows[ROW_NB]["severity"] == "ok"


def test_declaration_without_a_certificate_number_fails_the_nb_row(conn):
    seed_item(conn, "IIA-NONB", manufacturer_raw="ACME", product_class="IIa")
    doc = seed_document(
        conn, content_hash="h-nb-no", archive_url="file:///nonb.pdf",
        doc_type="DoC", status="production", cert_number=None,
    )
    seed_link(conn, "IIA-NONB", doc, match_basis="ref-item", status="production")

    rows = _rows(item_completeness(conn, "IIA-NONB", today=TODAY))
    assert rows[ROW_NB]["state"] == "missing"
    assert rows[ROW_NB]["severity"] == "bad"


def test_plain_class_i_is_not_marked_down_for_a_missing_nb_number(conn):
    seed_item(conn, "I-1", manufacturer_raw="ACME", product_class="I")
    doc = seed_document(
        conn, content_hash="h-i-doc", archive_url="file:///i.pdf",
        doc_type="DoC", status="production",
    )
    seed_link(conn, "I-1", doc, match_basis="ref-item", status="production")

    rows = _rows(item_completeness(conn, "I-1", today=TODAY))
    assert rows[ROW_NB]["state"] == "not-required"
    assert rows[ROW_NB]["severity"] == "none"


def test_missing_ifu_is_amber_on_class_iia_and_red_on_class_ir(conn):
    """Annex I 23.1(d) reaches IIa and stops there; Art. 52(7)(c) puts the
    reprocessing instructions inside the notified body's remit for Ir."""
    seed_item(conn, "IIA-IFU", manufacturer_raw="ACME", product_class="IIa")
    seed_item(conn, "IR-IFU", manufacturer_raw="ACME", product_class="Ir")

    iia = _rows(item_completeness(conn, "IIA-IFU", today=TODAY))
    ir = _rows(item_completeness(conn, "IR-IFU", today=TODAY))
    assert iia[ROW_IFU]["severity"] == "warn"
    assert ir[ROW_IFU]["severity"] == "bad"


def test_manufacturer_scope_iso_certificate_leaves_every_scored_row_unmet(conn):
    """The Carl Martin shape, in miniature: one ISO 13485 certificate bound to
    the article by `mfr-scope`. Coverage says covered; the card must say the
    declaration, the instructions and the NB number are all still missing."""
    seed_item(conn, "IR-ISO", manufacturer_raw="ACME", product_class="Ir")
    iso = seed_document(
        conn, content_hash="h-iso", archive_url="file:///iso.pdf",
        doc_type="ISO", status="production", coverage_scope="manufacturer",
        validity_to="2030-06-06",
    )
    seed_link(conn, "IR-ISO", iso, match_basis="mfr-scope", status="production")

    rows = _rows(item_completeness(conn, "IR-ISO", today=TODAY))
    assert rows[ROW_DOC]["state"] == "missing"
    assert rows[ROW_IFU]["state"] == "missing"
    assert rows[ROW_NB]["state"] == "missing"
    # the ISO itself is reported, and reported as what it is
    assert rows[ROW_ISO]["state"] == "mfr-scope-only"
    assert rows[ROW_ISO]["severity"] == "warn"


def test_a_retracted_link_is_not_a_holding(conn):
    """A retracted link is history, not coverage — `/items` and `/documents`
    have excluded it since doc 235 (19 retracted links, zero live, all 19
    listed). The card has to agree with them, or the same document reads as
    held here and absent two clicks away."""
    seed_item(conn, "RETRACT-1", manufacturer_raw="ACME", product_class="IIa")
    doc = seed_document(
        conn, content_hash="h-retracted", archive_url="file:///r.pdf",
        doc_type="DoC", status="production", cert_number="0666",
    )
    seed_link(conn, "RETRACT-1", doc, match_basis="ref-item", status="retracted")

    rows = _rows(item_completeness(conn, "RETRACT-1", today=TODAY))
    assert rows[ROW_DOC]["state"] == "missing"
    assert rows[ROW_DOC]["doc_id"] is None


def test_item_card_severity_is_the_worst_scored_row(conn):
    seed_item(conn, "IIB-1", manufacturer_raw="ACME", product_class="IIb")
    assert item_completeness(conn, "IIB-1", today=TODAY)["severity"] == "bad"


def test_evidence_rows_never_drive_the_card_severity(conn):
    """A complete article with no EC certificate on file is still complete."""
    seed_item(conn, "I-CLEAN", manufacturer_raw="ACME", product_class="I")
    doc = seed_document(
        conn, content_hash="h-clean-doc", archive_url="file:///c1.pdf",
        doc_type="DoC", status="production", validity_to="2030-01-01",
    )
    ifu = seed_document(
        conn, content_hash="h-clean-ifu", archive_url="file:///c2.pdf",
        doc_type="IFU", status="production", validity_to=None,
    )
    seed_link(conn, "I-CLEAN", doc, match_basis="ref-item", status="production")
    seed_link(conn, "I-CLEAN", ifu, match_basis="ref-item", status="production")

    card = item_completeness(conn, "I-CLEAN", today=TODAY)
    assert _rows(card)[ROW_EC]["state"] == "missing"
    assert card["severity"] == "ok"


def test_article_level_holding_beats_a_manufacturer_scope_one_for_the_same_row(conn):
    """Two declarations reach the same article — one by `mfr-scope`, one by
    `ref-item`. The card must show the stronger, or adding a weak link would
    downgrade a row that was already evidenced."""
    seed_item(conn, "IIA-BOTH", manufacturer_raw="ACME", product_class="IIa")
    weak = seed_document(
        conn, content_hash="h-weak", archive_url="file:///w.pdf",
        doc_type="DoC", status="production", cert_number="0111",
    )
    strong = seed_document(
        conn, content_hash="h-strong", archive_url="file:///s.pdf",
        doc_type="DoC", status="production", cert_number="0222",
    )
    seed_link(conn, "IIA-BOTH", weak, match_basis="mfr-scope", status="production")
    seed_link(conn, "IIA-BOTH", strong, match_basis="ref-item", status="production")

    rows = _rows(item_completeness(conn, "IIA-BOTH", today=TODAY))
    assert rows[ROW_DOC]["state"] == "held"
    assert rows[ROW_DOC]["doc_id"] == strong


def test_staged_link_does_not_beat_a_production_one(conn):
    seed_item(conn, "IIA-STAGE", manufacturer_raw="ACME", product_class="IIa")
    staged = seed_document(
        conn, content_hash="h-staged", archive_url="file:///st.pdf",
        doc_type="DoC", status="staged",
    )
    live = seed_document(
        conn, content_hash="h-live", archive_url="file:///lv.pdf",
        doc_type="DoC", status="production", cert_number="0333",
    )
    seed_link(conn, "IIA-STAGE", staged, match_basis="ref-item", status="staged")
    seed_link(conn, "IIA-STAGE", live, match_basis="ref-item", status="production")

    rows = _rows(item_completeness(conn, "IIA-STAGE", today=TODAY))
    assert rows[ROW_DOC]["doc_id"] == live
    assert rows[ROW_DOC]["severity"] == "ok"


# --------------------------------------------------------------------------- #
# manufacturer rollup
# --------------------------------------------------------------------------- #
def test_manufacturer_card_counts_items_per_row(conn):
    _alias(conn, "ACME", "ACME CORP")
    seed_item(conn, "M-1", manufacturer_raw="ACME", product_class="IIa")
    seed_item(conn, "M-2", manufacturer_raw="ACME", product_class="IIa")
    doc = seed_document(
        conn, content_hash="h-m1", archive_url="file:///m1.pdf",
        doc_type="DoC", status="production", validity_to="2030-01-01",
        cert_number="0444",
    )
    seed_link(conn, "M-1", doc, match_basis="ref-item", status="production")

    card = manufacturer_completeness(conn, "ACME CORP", today=TODAY)
    assert card["md_items"] == 2
    doc_row = _rows(card)[ROW_DOC]
    assert doc_row["held"] == 1
    assert doc_row["none"] == 1
    assert doc_row["severity"] == "bad"


def test_manufacturer_card_counts_articles_with_no_declaration(conn):
    """The headline the client needs: "supplier is certified, N articles are
    not evidenced". `no_declaration_items` is that N."""
    _alias(conn, "ACME", "ACME CORP")
    seed_item(conn, "U-1", manufacturer_raw="ACME", product_class="Ir")
    seed_item(conn, "U-2", manufacturer_raw="ACME", product_class="Ir")
    seed_item(conn, "U-3", manufacturer_raw="ACME", product_class="Ir")
    doc = seed_document(
        conn, content_hash="h-u1", archive_url="file:///u1.pdf",
        doc_type="DoC", status="production", cert_number="0555",
    )
    seed_link(conn, "U-1", doc, match_basis="ref-item", status="production")

    card = manufacturer_completeness(conn, "ACME CORP", today=TODAY)
    assert card["no_declaration_items"] == 2
    assert card["stale_declaration_items"] == 0


def test_manufacturer_card_separates_missing_declarations_from_stale_ones(conn):
    """"No declaration" and "a declaration nobody has looked at since 2019" are
    different problems with different answers, and lumping them together
    produced a headline that told IVOCLAR 1.050 of its 1.069 articles had no
    declaration when 16 was the true figure."""
    _alias(conn, "ACME", "ACME CORP")
    seed_item(conn, "SPLIT-NONE", manufacturer_raw="ACME", product_class="IIa")
    seed_item(conn, "SPLIT-OLD", manufacturer_raw="ACME", product_class="IIa")
    old = seed_document(
        conn, content_hash="h-split-old", archive_url="file:///so.pdf",
        doc_type="DoC", status="production", validity_to="2020-01-01",
        cert_number="0777",
    )
    seed_link(conn, "SPLIT-OLD", old, match_basis="ref-item", status="production")

    card = manufacturer_completeness(conn, "ACME CORP", today=TODAY)
    assert card["no_declaration_items"] == 1
    assert card["stale_declaration_items"] == 1


def test_manufacturer_card_surfaces_the_supplier_level_certificates(conn):
    """The ISO 13485 that makes the coverage number look finished gets its own
    line, out of the scored table, with its expiry — so the reader sees the
    supplier claim and the article gap as two separate facts."""
    _alias(conn, "ACME", "ACME CORP")
    seed_item(conn, "S-1", manufacturer_raw="ACME", product_class="Ir")
    iso = seed_document(
        conn, content_hash="h-s-iso", archive_url="file:///siso.pdf",
        doc_type="ISO", status="production", coverage_scope="manufacturer",
        validity_to="2028-06-06",
    )
    seed_link(conn, "S-1", iso, match_basis="mfr-scope", status="production")

    card = manufacturer_completeness(conn, "ACME CORP", today=TODAY)
    assert [(d["type"], str(d["validity_to"])) for d in card["supplier_docs"]] == [
        ("ISO", "2028-06-06")
    ]


def test_manufacturer_card_ignores_non_devices(conn):
    _alias(conn, "ACME", "ACME CORP")
    seed_item(conn, "MD-ONLY", manufacturer_raw="ACME", product_class="IIa")
    seed_item(conn, "NOT-MD", manufacturer_raw="ACME", md_flag=False)

    assert manufacturer_completeness(conn, "ACME CORP", today=TODAY)["md_items"] == 1


def test_manufacturer_with_no_devices_reports_nothing_rather_than_green(conn):
    _alias(conn, "EMPTY", "EMPTY CORP")
    card = manufacturer_completeness(conn, "EMPTY CORP", today=TODAY)
    assert card["md_items"] == 0
    assert card["severity"] == "none"


# --------------------------------------------------------------------------- #
# rendering — the card is only worth anything if a non-technical reader sees it
# --------------------------------------------------------------------------- #
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Web  # noqa: E402
from tests.conftest import TEST_API_URL as _API_URL  # noqa: E402
from web.app import create_app  # noqa: E402


@pytest.fixture
def ui(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=_API_URL, imports_dir=str(tmp_path))))


def test_item_page_renders_the_card(conn, ui):
    seed_item(conn, "RENDER-1", manufacturer_raw="ACME", product_class="Ir")
    conn.commit()

    body = ui.get("/items/RENDER-1").text
    assert "Compliance completeness" in body
    assert "Declaration of conformity" in body
    assert "Instructions for use" in body
    assert "Notified-body number" in body


def test_item_page_omits_the_card_for_a_non_device(conn, ui):
    seed_item(conn, "RENDER-NOTMD", manufacturer_raw="ACME", md_flag=False)
    conn.commit()

    assert "Compliance completeness" not in ui.get("/items/RENDER-NOTMD").text


def test_manufacturer_page_states_the_supplier_versus_article_split(conn, ui):
    """The sentence the whole card exists to produce."""
    _alias(conn, "ACME", "ACME CORP")
    seed_item(conn, "RM-1", manufacturer_raw="ACME", product_class="Ir")
    seed_item(conn, "RM-2", manufacturer_raw="ACME", product_class="Ir")
    iso = seed_document(
        conn, content_hash="h-render-iso", archive_url="file:///riso.pdf",
        doc_type="ISO", status="production", coverage_scope="manufacturer",
        validity_to="2028-06-06",
    )
    seed_link(conn, "RM-1", iso, match_basis="mfr-scope", status="production")
    seed_link(conn, "RM-2", iso, match_basis="mfr-scope", status="production")
    conn.commit()

    body = ui.get("/manufacturers/ACME CORP").text
    assert "ISO 13485 certificate" in body
    assert "2028-06-06" in body
    assert "2 of 2" in body
    assert "no declaration of conformity of their own" in body
    assert "Due a review" not in body  # nothing here is on the five-year rule


# --------------------------------------------------------------------------- #
# manufacturer rollup buckets — the card must not contradict its own headline
# --------------------------------------------------------------------------- #
def test_expired_declarations_are_not_counted_as_missing(conn):
    """The defect this section exists for. The rollup used to tally by severity,
    and `expired` / `superseded` / `missing` are all severity `bad`, so all three
    landed in a column labelled "Missing". On IVOCLAR that column read 1.050 when
    16 articles actually had no declaration — a 65x overstatement, sitting one
    line below a headline that said 16."""
    _alias(conn, "ACME", "ACME CORP")
    seed_item(conn, "B-NONE", manufacturer_raw="ACME", product_class="IIa")
    seed_item(conn, "B-OLD", manufacturer_raw="ACME", product_class="IIa")
    old = seed_document(
        conn, content_hash="h-b-old", archive_url="file:///bo.pdf",
        doc_type="DoC", status="production", validity_to="2020-01-01",
        cert_number="0888",
    )
    seed_link(conn, "B-OLD", old, match_basis="ref-item", status="production")

    row = _rows(manufacturer_completeness(conn, "ACME CORP", today=TODAY))[ROW_DOC]
    assert row["none"] == 1
    assert row["lapsed"] == 1
    assert row["held"] == 0


def test_superseded_declarations_land_with_the_lapsed_not_the_missing(conn):
    _alias(conn, "ACME", "ACME CORP")
    seed_item(conn, "B-SUP", manufacturer_raw="ACME", product_class="IIa")
    doc = seed_document(
        conn, content_hash="h-b-sup", archive_url="file:///bs.pdf",
        doc_type="DoC", status="superseded", cert_number="0999",
    )
    seed_link(conn, "B-SUP", doc, match_basis="ref-item", status="production")

    row = _rows(manufacturer_completeness(conn, "ACME CORP", today=TODAY))[ROW_DOC]
    assert row["lapsed"] == 1
    assert row["none"] == 0


def test_buckets_account_for_every_device_exactly_once(conn):
    """Whatever the buckets are, they must sum to the device count — a reader
    adds the row up and expects the total."""
    _alias(conn, "ACME", "ACME CORP")
    seed_item(conn, "S-A", manufacturer_raw="ACME", product_class="IIa")
    seed_item(conn, "S-B", manufacturer_raw="ACME", product_class="Ir")
    seed_item(conn, "S-C", manufacturer_raw="ACME", product_class="I")
    good = seed_document(
        conn, content_hash="h-s-good", archive_url="file:///sg.pdf",
        doc_type="DoC", status="production", validity_to="2030-01-01",
        cert_number="0777",
    )
    seed_link(conn, "S-A", good, match_basis="ref-item", status="production")

    card = manufacturer_completeness(conn, "ACME CORP", today=TODAY)
    for row in card["rows"]:
        total = row["held"] + row["attention"] + row["lapsed"] + row["none"] + row["not_required"]
        assert total == card["md_items"], row["row"]


def test_an_iso_certificate_held_at_supplier_level_is_held_not_a_warning(conn):
    """An ISO 13485 certificate is issued to the manufacturer and to nothing
    else — manufacturer scope is the only form it comes in. Counting that as
    "needs attention" put 1.069 IVOCLAR articles under an alarm for a document
    behaving exactly as designed. The same holding on a DECLARATION stays a
    warning, because there manufacturer scope really is weaker evidence."""
    _alias(conn, "ACME", "ACME CORP")
    seed_item(conn, "E-1", manufacturer_raw="ACME", product_class="Ir")
    iso = seed_document(
        conn, content_hash="h-e-iso", archive_url="file:///ei.pdf",
        doc_type="ISO", status="production", coverage_scope="manufacturer",
        validity_to="2030-06-06",
    )
    seed_link(conn, "E-1", iso, match_basis="mfr-scope", status="production")

    rows = _rows(manufacturer_completeness(conn, "ACME CORP", today=TODAY))
    assert rows[ROW_ISO]["held"] == 1
    assert rows[ROW_ISO]["attention"] == 0


def test_a_declaration_held_only_at_supplier_level_is_still_a_warning(conn):
    _alias(conn, "ACME", "ACME CORP")
    seed_item(conn, "E-2", manufacturer_raw="ACME", product_class="Ir")
    doc = seed_document(
        conn, content_hash="h-e-doc", archive_url="file:///ed.pdf",
        doc_type="DoC", status="production", coverage_scope="manufacturer",
        cert_number="0666",
    )
    seed_link(conn, "E-2", doc, match_basis="mfr-scope", status="production")

    rows = _rows(manufacturer_completeness(conn, "ACME CORP", today=TODAY))
    assert rows[ROW_DOC]["attention"] == 1
    assert rows[ROW_DOC]["held"] == 0


def test_the_item_card_still_names_a_supplier_wide_evidence_holding(conn):
    """Regression guard on the fix above: on ONE article the reader must still
    be told the ISO covers the supplier and not this product. Only the rollup's
    accounting changes, never the item page's wording."""
    seed_item(conn, "E-3", manufacturer_raw="ACME", product_class="Ir")
    iso = seed_document(
        conn, content_hash="h-e3-iso", archive_url="file:///e3.pdf",
        doc_type="ISO", status="production", coverage_scope="manufacturer",
        validity_to="2030-06-06",
    )
    seed_link(conn, "E-3", iso, match_basis="mfr-scope", status="production")

    assert _rows(item_completeness(conn, "E-3", today=TODAY))[ROW_ISO]["state"] == "mfr-scope-only"
