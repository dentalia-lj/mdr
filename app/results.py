"""The one shape every handler returns, and the closed vocabulary of data
anomalies.

Standardization is the point. Before this, each handler invented its own
report dict and the runner threw it away, so 'what went in and what came out'
was only answerable by hand-analysis. Counts roll up, samples are bounded so
a 19,000-row ingest cannot write a 19,000-row blob, and anomalies land in a
standing ledger keyed by (kind, subject).

ANOMALY_KINDS is closed on purpose, exactly like the job-type enum: a new
kind is added here and in migration 019's comment, deliberately, or not at
all. An unrecognised kind raises rather than being silently accepted.
"""
from __future__ import annotations

import json

SAMPLE_CAP = 50

#: Closed vocabulary. Keep in sync with migrations/019_job_result.sql.
ANOMALY_KINDS = frozenset({
    "md_class_blank",         # BC device-class column empty for an item
    "mfr_ref_missing",        # no manufacturer article number: REF gate has no key
    "mfr_ref_prose",          # mfr_ref holds a remark, not a code
    "manufacturer_code_blank",
    "reclassified_non_md",    # mirrored item BC later declassified
    # Removed 2026-08-19 ([results-anomaly-kinds]): `code_shape_unexpected` and
    # `blocked_item` had no producer and no specification to build one against.
    # Neither is named in the contract PRD, the ground truth, the schema sketch
    # or the job-type handbook -- `code_shape_unexpected` existed as a comment
    # ("breaks the 3/5-digit pattern") with no shape check anywhere in app/, and
    # `blocked_item` traces only to an open client question
    # (docs/2026-08-11-open-questions-for-dentalia.md §F q25, `Blokirano = 1`:
    # 678 rows, 43 of them classified as devices, "currently out of scope",
    # unanswered in the round-2 replies). Writing a producer for that one would
    # invent the MDR retention policy for a blocked previously-sold item, which
    # is Dentalia's call. A closed vocabulary whose members cannot be reached
    # advertises coverage that does not exist; both come back the day there is
    # a rule to implement, and neither needs a migration -- `data_anomaly.kind`
    # is plain text with no CHECK, enforced only by `Result.anomaly()` below.
    "no_text_layer",          # scan: nothing recoverable without vision
    "cert_reference_unresolved",  # DoC cites a certificate we do not hold
    # A Basic UDI-DI failed its own check-character pair (`app.extract.udi`).
    # Subject is the offending value, so `seen_count` is how many documents
    # carry that same misread string. This one is worth counting rather than
    # merely lowering confidence over, because it is otherwise invisible: an
    # unresolvable Basic UDI-DI looks identical whether the manufacturer never
    # registered it or we read it wrong, and only the check pair tells them
    # apart (docs/2026-08-20-udi-identifiers-and-registries.md §2).
    "basic_udi_check_failed",
    # A playbook REF template matched the document and still parsed no codes:
    # our own parser broke (a layout change on the manufacturer's side), which
    # is otherwise indistinguishable from "this document lists no articles".
    # Subject is the playbook slug, so repeat observations count the damage per
    # TEMPLATE -- that is the thing to go and fix.
    "t0_ref_template_miss",
})


class Result:
    """Accumulates a job's report. `as_dict()` is what the handler returns."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._samples: dict[str, list] = {}
        self._sample_totals: dict[str, int] = {}
        self._notes: list[str] = []
        self._anomalies: list[dict] = []

    def count(self, key: str, n: int = 1) -> None:
        self._counts[key] = self._counts.get(key, 0) + n

    def sample(self, key: str, row: dict) -> None:
        """Record an example. Bounded: the count is always exact, the sample
        is capped, and the total is reported so a truncation is never silent."""
        self._sample_totals[key] = self._sample_totals.get(key, 0) + 1
        bucket = self._samples.setdefault(key, [])
        if len(bucket) < SAMPLE_CAP:
            bucket.append(row)

    def note(self, text: str) -> None:
        self._notes.append(text)

    def anomaly(self, kind: str, *, subject: str, catalogue: str | None = None,
                detail: dict | None = None) -> None:
        if kind not in ANOMALY_KINDS:
            raise ValueError(
                f"unknown anomaly kind {kind!r}: add it to ANOMALY_KINDS and to "
                "migrations/019_job_result.sql, or use an existing kind"
            )
        self._anomalies.append({
            "kind": kind, "subject": subject,
            "catalogue": catalogue, "detail": detail or {},
        })

    def as_dict(self) -> dict:
        counts = dict(self._counts)
        for key, total in self._sample_totals.items():
            if total > SAMPLE_CAP:
                counts[f"{key}_sampled_of"] = total
        return {
            "counts": counts,
            "samples": self._samples,
            "notes": self._notes,
            "anomalies": self._anomalies,
        }


def record_anomalies(conn, anomalies: list[dict]) -> int:
    """Upsert anomalies into the standing ledger. Repeat observations bump
    seen_count and last_seen instead of inserting, so the table stays the size
    of the problem rather than the size of the corpus."""
    for a in anomalies:
        conn.execute(
            "INSERT INTO data_anomaly (kind, subject, catalogue, detail) "
            "VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (kind, subject) DO UPDATE SET "
            "seen_count = data_anomaly.seen_count + 1, last_seen = now(), "
            "detail = EXCLUDED.detail",
            (a["kind"], a["subject"], a.get("catalogue"), json.dumps(a.get("detail") or {})),
        )
    return len(anomalies)
