"""backfill.scan: walk an existing local corpus, archive each file, emit extract.doc.

Handbook §sibling-producers. The file is already local (no fetch), so this enters
the spine directly at extract.doc. Hash-addressed dedup is the whole point: a
content_hash already recorded in fetch_log is never re-emitted, so re-scanning a
corpus is ~free (C2/C3/AC5 two-cycle). group_id is None — backfill docs
self-identify from their content (REF list / Basic UDI-DI), resolved downstream.

The corpus is NOT the archive. It is a hand-managed SFTP dump that gets cleared
and re-dumped, mounted read-only, and visible at a different path in every
container (host `.../imports/...` vs `/imports`). Recording the walked path as
`archive_url` therefore produced a handle that no other process could open and
that a re-dump destroyed outright. So BACKFILL archives the bytes through the
StorageAdapter exactly as FETCH does (`fetch.py` step 7) and emits the handle the
store returns — content-addressed, deduplicated, under our control (end-state
output 2, PRD §9). Nothing downstream knows which store is live (invariant 11).

`drive_folder` is a local path here and we walk the filesystem; the Drive walker
is the same code behind a different adapter.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
import re

from app import playbooks, queue
from app.adapters.storage import make_storage_adapter
from app.config import load_config
from app.handlers import archiving, register

log = logging.getLogger("dentalia.handler.backfill")


class BackfillError(Exception):
    """A scan that cannot be what the operator meant. Raised (not counted) so
    the job fails into the dead-jobs board instead of reporting success."""


#: Everything `_pdfs` yields is a PDF by extension; the archive layout only uses
#: this to pick an extension when a filename has none.
_PDF_CONTENT_TYPE = "application/pdf"

#: coverage_map index files are a spreadsheet and a Word table, not PDFs --
#: this is purely the fallback extension hint `archiving.archive_path` uses
#: when a filename carries none; both declared Komet sources already do.
_INDEX_CONTENT_TYPE = "application/octet-stream"


def _brand(folder: pathlib.Path) -> str:
    """Manufacturer bucket for the archive layout: the scanned folder itself.

    The runbook mandates one brand folder per job (`/imports/dentalia-sftp/GC`),
    so the brand is the scan root's own name. This used to read the first path
    component *relative* to the scan root on the assumption that a scan started
    at the corpus root — and those two rules contradict each other. Scanning
    `.../GC` made `parts[0]` GC's internal subfolders, so the entire GC pilot
    archived to `/archive/DOC/...` and `/archive/MSDS/...` with the manufacturer
    absent from the path (2026-08-12).

    Purely a human-facing bucket — the archive is hash-addressed (PRD §9) — so a
    nameless root degrades to "unknown" rather than raising. A scan pointed at
    the corpus root instead of a brand folder buckets everything under that
    root's name; the runbook tells you not to, and cost/failure isolation is the
    bigger reason it says so."""
    return folder.resolve().name or "unknown"


def _hash_seen(conn, content_hash: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM fetch_log WHERE content_hash = %s", (content_hash,)
    ).fetchone() is not None


def _upsert_fetch_log(conn, url: str, content_hash: str) -> None:
    conn.execute(
        """
        INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, last_checked_at)
        VALUES (%s, %s, 'backfill', now(), now())
        ON CONFLICT (url_normalized) DO UPDATE
           SET content_hash = EXCLUDED.content_hash, last_checked_at = now()
        """,
        (url, content_hash),
    )


def _pdfs(folder: pathlib.Path):
    """Every path under `folder` whose extension is .pdf, matched case-INsensitively.
    The real corpus carries 4 `.PDF` files (two ISO 13485 certs, an MDR DoC, an
    MSDS) that a plain `rglob("*.pdf")` drops on a case-sensitive filesystem —
    losing compliance documents with no skip counted anywhere.

    Deliberately does NOT filter to `is_file()`: a directory named `*.pdf` is an
    anomaly the caller surfaces via the `errors` count (its read raises OSError),
    which is the "nothing skipped is silent" contract."""
    return (p for p in folder.rglob("*") if p.suffix.lower() == ".pdf")


def _companion_rule(brand: str) -> dict | None:
    """The scanned brand's `companion` playbook rule, or None for every
    manufacturer that does not split a document across two files."""
    pb = playbooks.for_manufacturer(brand)
    return pb.companion if pb else None


def _coverage_map_rule(brand: str) -> dict | None:
    """The scanned brand's `coverage_map` playbook block, or None for every
    manufacturer that does not publish a merged declaration+list form.

    `coverage_map` is independent of `companion` (a playbook may carry either,
    both, or neither) -- this reads its OWN `canonical` sub-block, never the
    `companion` one, even though the two share a similar shape by design."""
    pb = playbooks.for_manufacturer(brand)
    return pb.coverage_map if pb else None


def _exclude_rule(brand: str) -> dict | None:
    """The scanned brand's `exclude` playbook rule, or None for every
    manufacturer whose folder holds nothing but documents."""
    pb = playbooks.for_manufacturer(brand)
    return pb.exclude if pb else None


def _apply_exclude(candidates, rule: dict | None):
    """Split `candidates` into (kept, per-pattern counts).

    Matched with `re.search` on the BASENAME only, case-insensitively: a
    corpus path contains the brand folder and the customer's own directory
    names, and a pattern accidentally anchored on either would fire on files
    it was never written for.

    Counts are per pattern rather than a single total because the failure this
    can cause is silent over-matching -- a rule meant for `PDO26+05073.pdf`
    that also eats a declaration. A pattern's count on the job result is the
    only thing that makes that visible without re-reading the corpus, which is
    the "nothing skipped is silent" contract `_pdfs` already carries.

    An unparseable pattern cannot arrive here: `playbooks._parse` compiles
    every one at load. `for_manufacturer` returns None for a malformed file
    rather than raising, so a broken playbook degrades to "exclude nothing"
    and the operator sees an unfiltered scan, never a lost document."""
    if not rule:
        return list(candidates), {}
    compiled = [(p, re.compile(p, re.IGNORECASE)) for p in rule["filenames"]]
    counts = {p: 0 for p, _ in compiled}
    kept = []
    for path in candidates:
        for pattern, regex in compiled:
            if regex.search(path.name):
                counts[pattern] += 1
                break
        else:
            kept.append(path)
    return kept, counts


def _regulation_of(name: str) -> str:
    """Which regulation a Komet filename declares. `_DoC_EU_SIGNED` with no
    marker is the MDD-era declaration; the corpus writes the MDR ones with an
    explicit MDR in the name."""
    upper = name.upper()
    if "MDR" in upper:
        return "MDR"
    if "MDD" in upper:
        return "MDD"
    return "EU"


def _canonical_choice(candidates, rule: dict) -> tuple[set, dict]:
    """Which files are NOT documents because a better form of the same
    declaration exists, and why.

    Komet publishes the MDR declaration twice: bare, and merged with its
    product list (`_List_SIGNED`). Both carry the same type, regulation and
    coverage, which is exactly the triple invariant 5 supersedes on, so filing
    both leaves two DoCs racing over one list. The merged file is the artifact
    Komet signed as a whole, so it wins.

    Two merged candidates for one `(family, regulation)` is a refusal, not a
    tie-break: the rule cannot say which is canonical, so nothing is
    suppressed and the caller counts it."""
    key_re = re.compile(rule["key"])
    prefer = tuple(rule.get("prefer", ()))
    annex = tuple(rule.get("annex", ()))
    by_pair, ambiguous = {}, set()
    for path in candidates:
        name = path.name
        if any(a in name for a in annex):
            continue
        if not any(p in name for p in prefer):
            continue
        m = key_re.search(name)
        pair = (m.group(1) if m else name, _regulation_of(name))
        if pair in by_pair:
            ambiguous.add(pair)
        by_pair[pair] = path

    for pair in ambiguous:
        by_pair.pop(pair, None)

    redundant = {}
    for path in candidates:
        name = path.name
        if any(a in name for a in annex) or any(p in name for p in prefer):
            continue
        m = key_re.search(name)
        pair = (m.group(1) if m else name, _regulation_of(name))
        winner = by_pair.get(pair)
        if winner is not None:
            redundant[path] = f"superseded by {winner.name}"
    return set(redundant), {"redundant": redundant, "ambiguous": sorted(ambiguous)}


def _pair(candidates, rule: dict) -> tuple[set, dict]:
    """`(annex paths, primary path -> its annex path)`.

    Komet issues its declaration and that declaration's product list as two
    files sharing a leading number -- `532624_RA_810_DoC_EU_SIGNED.pdf` and
    `532624_RA_812_Liste_DoC.pdf`. Measured over the 186-PDF corpus on
    2026-08-18: 39 numbers, every one carrying both, zero orphans.

    One annex may serve SEVERAL primaries and does: `532624` ships an MDD
    declaration and an MDR one over the same product list, so the mapping is
    many-to-one and the annex is not consumed by the first primary that claims
    it. A primary whose annex is missing simply gets no pairing -- it keeps
    whatever list it carries itself, which for 21 of Komet's 102 declarations is
    a complete one printed inline.

    A key claimed by MORE than one annex is dropped rather than resolved: see
    the comment below. Returns the ambiguous keys so the caller can count them
    -- an unpaired declaration is coverage the registry does not get, and that
    is never allowed to be silent."""
    key = re.compile(rule["key"])
    primary_re = re.compile(rule["primary"])
    annex_re = re.compile(rule["annex"])

    annexes: dict[str, pathlib.Path] = {}
    ambiguous: set[str] = set()
    for path in candidates:
        if annex_re.search(path.name):
            m = key.search(path.name)
            if not m:
                continue
            if m.group(1) in annexes:
                ambiguous.add(m.group(1))
            annexes[m.group(1)] = path

    # Two annexes on one key is a key that does not identify a document.
    # `533173` ships its list twice, once per device class
    # (`..._Liste_DoC_Klasse_I.pdf`, `..._Liste_DoC_Klasse_Is.pdf`) against three
    # declarations (MDD Is, MDR I, MDR Is). Last-one-wins would have handed the
    # Class Is list to a Class I declaration and called it evidence -- a
    # confident wrong coverage claim, which is worse than none. So the whole key
    # is dropped: its declarations keep no list and go to the manual queue,
    # its annexes stay ordinary documents, and the count says it happened.
    for k in ambiguous:
        annexes.pop(k, None)

    pairs = {}
    for path in candidates:
        if primary_re.search(path.name):
            m = key.search(path.name)
            if m and m.group(1) in annexes:
                pairs[path] = annexes[m.group(1)]
    return set(annexes.values()), pairs, sorted(ambiguous)


def handle_backfill_scan(conn, job: dict, *, store=None) -> dict:
    folder = pathlib.Path(job["payload"]["drive_folder"])

    # Checked before the folder even has to exist, and before a store is built:
    # a disowned dump is refused on identity, not on anything found inside it.
    # This FAILS the job (dead-jobs board) rather than returning a zero result,
    # for the same reason a missing folder does -- `scanned: 0` reads as "the
    # corpus is already ingested", which is how a scan nobody meant to run
    # stays invisible.
    pb = playbooks.for_manufacturer(_brand(folder))
    if pb and pb.skip_backfill:
        raise BackfillError(
            f"backfill.scan: {_brand(folder)} is not scanned: "
            f"{pb.skip_backfill['reason']}")

    if store is None:
        store = make_storage_adapter(load_config())

    # A wrong path is the likeliest operator error and used to be the quietest:
    # rglob on a missing directory yields nothing and raises nothing, so a
    # mistyped drive_folder returned `scanned: 0` -- indistinguishable from a
    # corpus already fully ingested. Checked BEFORE any write, so a failed scan
    # leaves no fetch_log row or job behind. Zero EMITTED is still success (that
    # is the C2/C3 two-cycle property); zero SCANNED never is.
    if not folder.is_dir():
        raise BackfillError(f"backfill.scan: drive_folder does not exist: {folder}")
    candidates = sorted(_pdfs(folder))
    if not candidates:
        raise BackfillError(f"backfill.scan: no PDFs under {folder}")

    # Excluded files are dropped here, before anything is hashed, archived or
    # ledgered -- the whole reason the key exists is to keep customer names out
    # of the archive, and archiving-then-declining (what `companion` and
    # `coverage_map` do) would not achieve that.
    candidates, excluded_by = _apply_exclude(candidates, _exclude_rule(_brand(folder)))
    excluded = sum(excluded_by.values())
    if excluded:
        log.info("backfill: %d file(s) excluded by playbook rule: %s", excluded,
                 ", ".join(f"{p} x{n}" for p, n in excluded_by.items() if n))
    # A rule that swallows the entire corpus is an authoring error, and it
    # would otherwise report `scanned: 0` -- the same indistinguishable-from-
    # success failure the missing-folder guard above exists to prevent.
    if not candidates:
        raise BackfillError(
            f"backfill.scan: every one of the {excluded} PDF(s) under {folder} "
            f"was excluded by the playbook rule")

    rule = _companion_rule(_brand(folder))
    annex_paths, pairs, ambiguous = _pair(candidates, rule) if rule else (set(), {}, [])
    annex_urls: dict[pathlib.Path, str] = {}

    cmap = _coverage_map_rule(_brand(folder))
    redundant_paths, canon = (
        _canonical_choice(candidates, cmap["canonical"])
        if cmap else (set(), {"redundant": {}, "ambiguous": []})
    )

    scanned = emitted = seen = errors = annexes = attached = redundant = 0
    if ambiguous:
        log.warning("backfill: %d companion key(s) claimed by more than one annex, "
                    "left unpaired: %s", len(ambiguous), ", ".join(ambiguous))
    if canon["ambiguous"]:
        log.warning("backfill: %d canonical-form key(s) claimed by more than one "
                    "merged candidate, nothing suppressed: %s",
                    len(canon["ambiguous"]), ", ".join(map(str, canon["ambiguous"])))

    # The coverage map's own index files (a spreadsheet, a Word table) name
    # which declaration covers which article -- Task 6 reads them, but only
    # ever the ARCHIVED copy, never the corpus path (module docstring). A
    # declared source the corpus does not carry is an authoring or dump error,
    # not a skip: raising here (before the main loop writes anything) fails
    # the job into dead-jobs instead of reporting `scanned: N` with the index
    # silently absent -- the same silent-wrong-path failure mode invariant 8's
    # neighbor guards already exist to prevent.
    index_archived = 0
    for source in (cmap or {}).get("sources", ()):
        src_path = folder / source["file"]
        if not src_path.is_file():
            raise BackfillError(
                f"backfill.scan: coverage_map index file not found: {src_path}")
        try:
            body = src_path.read_bytes()
        except OSError as exc:  # permission race, or vanished between is_file() and read
            raise BackfillError(
                f"backfill.scan: cannot read coverage_map index file {src_path}: {exc}"
            ) from exc
        content_hash = hashlib.sha256(body).hexdigest()
        _upsert_fetch_log(conn, str(src_path.resolve()), content_hash)
        store.put(body, archiving.archive_path(
            _brand(folder), content_hash, str(src_path.relative_to(folder)),
            _INDEX_CONTENT_TYPE))
        index_archived += 1

    # Annexes first, so a primary's companion handle exists by the time the
    # primary is enqueued. A payload is immutable once enqueued (invariant 9),
    # so there is no second chance to attach it.
    for path in sorted(candidates, key=lambda p: (p not in annex_paths, p)):
        scanned += 1
        try:
            # Read once and hash what we archive. The previous streaming hash
            # re-opened the file to store it, leaving a window in which a
            # corpus re-dump could swap the bytes between hashing and storing
            # and break content-addressing at its root. The corpus's largest
            # PDF is 46 MB and a worker handles one job at a time, so holding a
            # document in memory costs nothing worth that risk.
            body = path.read_bytes()
        except OSError as exc:            # unreadable / a dir matching *.pdf / vanished
            errors += 1
            log.warning("backfill: cannot read %s: %s", path, exc)
            continue
        content_hash = hashlib.sha256(body).hexdigest()

        # Seen-before check BEFORE the upsert: a hash already in fetch_log (this
        # scan or a prior one) is a document we've already routed to extraction.
        # url_normalized stays the SOURCE path — it answers "where did this come
        # from" — while the dedupe that makes a re-dump free is keyed on the
        # hash, so the same document re-appearing at a new path still skips.
        is_annex = path in annex_paths
        already = _hash_seen(conn, content_hash)
        _upsert_fetch_log(conn, str(path.resolve()), content_hash)
        if already and not is_annex:
            seen += 1
            continue
        # An annex takes no short-circuit: a re-scan must still learn its
        # handle, or every primary in it silently loses its list. `put` is
        # content-addressed, so this rewrites identical bytes to the same path.

        rel = path.relative_to(folder)
        archive_url = store.put(
            body,
            archiving.archive_path(_brand(folder), content_hash, str(rel), _PDF_CONTENT_TYPE),
        )
        # An annex is not a document. Komet's `_RA_812_Liste_DoC.pdf` reads to
        # the extractor as a Declaration of Conformity in its own right -- same
        # manufacturer, same group, same regulation, same dates as the
        # declaration it belongs to -- which is exactly the
        # (coverage subject, type, regulation) triple invariant 5 supersedes on.
        # Filing both would leave two DoCs racing to supersede each other over
        # one product list. It is archived and ledgered like everything else;
        # what it does not get is a document of its own.
        if is_annex:
            annex_urls[path] = archive_url
            annexes += 1
            continue

        if path in redundant_paths:
            redundant += 1
            continue

        payload = {"archive_url": archive_url, "content_hash": content_hash,
                   "group_id": None, "source_url": str(rel)}
        companion_url = annex_urls.get(pairs.get(path))
        if companion_url:
            payload["companion_archive_url"] = companion_url
            attached += 1
        queue.enqueue(
            conn, "extract.doc", payload,
            dedupe_key=f"extract:{content_hash}",
        )
        emitted += 1

    return {"scanned": scanned, "emitted": emitted, "seen": seen, "errors": errors,
            "annexes": annexes, "companions_attached": attached,
            "companion_ambiguous": len(ambiguous),
            "redundant": redundant, "canonical_ambiguous": len(canon["ambiguous"]),
            "index_archived": index_archived,
            "excluded": excluded, "excluded_by": excluded_by}


register("backfill.scan", handle_backfill_scan)
