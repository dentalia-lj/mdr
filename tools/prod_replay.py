"""Task 9 of the extraction ladder: the built ladder on production's review queue.

An evaluation, not a pipeline change, and read-only towards the server. The
documents are the ones production holds in review (`staged`) on gate
confidence, and the text documents it holds on `no-item-identifier`
(research doc section 14's reading: the latest `gate.candidate` result). The
ladder runs here, on copies of their PDFs, through `tools.ladder_quality`'s
rounds: its stand-in client, request cache, Batch API submission, cap and
resumable state, with this run's own cache, state and cap in the run folder.

Everything lives in one run folder under the main checkout's gitignored
`eval/prod-replay/`:

    selection.jsonl     the selection query's output (`sql select`, psql -At on the server)
    pdfs/               the PDFs and companion annexes, rsync'd from /srv/compliance/archive
    sample.json         the documents run (all, or the trimmed set and its seed)
    cache/, state.json  this run's paid answers and batches; the cap reads state.json
    results.json        a finished run; results-partial.json what a stopped one finished
    overlap-input.json  what `sql overlap` sent; overlap.jsonl what the server answered
    review.json         verdicts on changed fields, read against the PDFs (by hand)
    notes.md            the reviewer's findings, included in the report (by hand)
    report.md, documents.csv, fields.csv

    python -m tools.prod_replay sql select                     the selection query
    python -m tools.prod_replay files --run DIR                files.txt for rsync
    python -m tools.prod_replay run --run DIR --dry-run        round 1's misses and cost; no API
    python -m tools.prod_replay run --run DIR --simulate       every round, stand-in answers; no API
    python -m tools.prod_replay sizes --run DIR                the same, per document and per sample size
    python -m tools.prod_replay run --run DIR [--cap-usd 0.95] [--nii-sample N --seed S]
    python -m tools.prod_replay partial --run DIR              a run the cap stopped: what finished; no API
    python -m tools.prod_replay sql overlap --run DIR [--results results-partial.json]
                                                               REF / UDI vs the catalogue, read-only
    python -m tools.prod_replay report --run DIR [--results results-partial.json]

Every SQL statement this prints runs inside `BEGIN READ ONLY; ... ROLLBACK;`.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import random
import sys
from decimal import Decimal

from tools import haiku_compare as hc
from tools import ladder_quality as lq

from app import playbooks as playbooks_mod  # noqa: E402
from app.extract import pdf as pdfutil  # noqa: E402
from app.extract import tiers  # noqa: E402
from app.handlers import gate  # noqa: E402
from app.handlers import validate  # noqa: E402

CAP_USD = Decimal("0.95")       # Denis, 2026-10-13: Task 9 approved "under $1"
SEED = 20261013
GATE_CONF, NII_TEXT = "gate-confidence", "no-item-identifier-text"
SHOWN_FIELDS = lq.SHOWN_FIELDS + ("stated_class",)
ARCHIVE = "/archive/"

#: VALIDATE flags this report recomputes from the new fields (and, for the REF
#: gate, the server's catalogue). Every other stored flag is carried over: the
#: date-ordering ones (C6 / C7) and the cited-certificate one need the registry.
RECOMPUTED = {"no-item-identifier", "no-ref-overlap", "manufacturer-unresolved",
              "multi-group-match", "evidence-page-missing", "not-a-device-document",
              "date-insane", "expiry-on-certless-doc", "ref-catalogue", "ref-unscoped",
              "multi-manufacturer-ref", tiers.REF_TRUNCATION_FLAG,
              "iso-without-standard-number"}

_BLOCKING = ("older-than-current", "same-date-revision", "no-item-identifier",
             "evidence-page-missing", "date-insane", "manufacturer-unresolved")

SELECT_SQL = f"""BEGIN READ ONLY;
SET LOCAL statement_timeout = '120s';
WITH lg AS (
  SELECT DISTINCT ON (j.payload->>'content_hash') j.payload->>'content_hash' AS h,
         j.id AS job_id, j.result, j.payload, j.finished_at
  FROM job j WHERE j.type = 'gate.candidate' AND j.status = 'done' AND NOT (j.result ? 'skipped')
  ORDER BY j.payload->>'content_hash', j.finished_at DESC
), d AS (
  SELECT d.*, lg.job_id, lg.result, lg.payload, lg.finished_at AS gate_at,
         COALESCE(dt.source, '?') AS text_source, dt.pages AS text_pages, dt.chars AS text_chars
  FROM document d LEFT JOIN lg ON lg.h = d.content_hash
  LEFT JOIN document_text dt ON dt.content_hash = d.content_hash
  WHERE d.status = 'staged'
), sel AS (
  SELECT *, CASE
    WHEN result->>'disposition' <> 'mfr-binding'
     AND NOT (COALESCE(result->'flags', '[]') ?| array{list(_BLOCKING)!r})
     AND (result->>'score')::numeric < 0.92 THEN '{GATE_CONF}'
    WHEN text_source = 'pdf-text' AND result->'flags' ? 'no-item-identifier' THEN '{NII_TEXT}'
  END AS selected_as
  FROM d
)
SELECT json_build_object(
  'selected_as', s.selected_as, 'doc_id', s.doc_id, 'content_hash', s.content_hash,
  'status', s.status, 'archive_url', s.archive_url, 'source_url', s.source_url,
  'canonical_manufacturer', s.canonical_manufacturer,
  'document', json_build_object('type', s.type, 'regulation', s.regulation,
      'coverage_scope', s.coverage_scope, 'validity_from', s.validity_from,
      'validity_to', s.validity_to, 'cert_number', s.cert_number,
      'basic_udi_di', s.basic_udi_di, 'stated_class', s.stated_class, 'cert_doc_id', s.cert_doc_id),
  'text_source', s.text_source, 'text_pages', s.text_pages, 'text_chars', s.text_chars,
  'gate_job', s.job_id, 'gate_at', s.gate_at, 'gate_result', s.result,
  'payload_group_id', s.payload->'group_id', 'extract_rev', s.payload->'extract_rev',
  'payload_flags', s.payload->'flags', 'ref_gate', s.payload->'ref_gate',
  'extract_group_id', x.group_id,
  'group_manufacturer', (SELECT g.canonical_manufacturer FROM item_group g WHERE g.group_id = x.group_id),
  'companion_archive_url', COALESCE(x.companion, (SELECT e.archive_url FROM evidence e
       WHERE e.doc_id = s.doc_id AND e.field = 'ref_list' AND e.archive_url <> s.archive_url
       ORDER BY e.extract_rev DESC, e.id DESC LIMIT 1)),
  'evidence', (SELECT json_agg(json_build_object('field', e.field, 'value', e.value, 'page', e.page,
        'verbatim', e.verbatim, 'tier', e.tier, 'model_id', e.model_id, 'confidence', e.confidence,
        'extract_rev', e.extract_rev, 'extracted_at', e.extracted_at) ORDER BY e.field, e.id)
     FROM evidence e WHERE e.doc_id = s.doc_id
       AND e.extract_rev = (SELECT max(e2.extract_rev) FROM evidence e2
                            WHERE e2.doc_id = e.doc_id AND e2.field = e.field)),
  'attempt', (SELECT json_build_object('tier', a.tier, 'model_id', a.model_id,
        'extract_rev', a.extract_rev, 'at', a.at, 'fields', a.fields)
     FROM extraction_attempt a WHERE a.content_hash = s.content_hash
     ORDER BY a.extract_rev DESC, a.id DESC LIMIT 1),
  'manual_task_open', (SELECT count(*) FROM manual_task m
                       WHERE m.doc_id = s.doc_id AND m.status = 'open'))
FROM sel s LEFT JOIN LATERAL (
  SELECT (j.payload->>'group_id')::bigint AS group_id, j.payload->>'companion_archive_url' AS companion
  FROM job j WHERE j.type = 'extract.doc' AND j.payload->>'content_hash' = s.content_hash
  ORDER BY j.id DESC LIMIT 1) x ON TRUE
WHERE s.selected_as IS NOT NULL ORDER BY s.selected_as, s.doc_id;
ROLLBACK;
"""


def _norm_sql(x: str) -> str:
    """`app.manufacturers.normalize` in SQL: NFKD (which also turns a
    non-breaking space into a space), combining marks dropped, whitespace
    collapsed and trimmed, spacing around `.` and `,` tightened, lower case."""
    return (f"lower(regexp_replace(btrim(regexp_replace(regexp_replace(normalize({x}, NFKD), "
            f"'[\\u0300-\\u036f]', '', 'g'), '[[:space:]]+', ' ', 'g')), "
            f"'[[:space:]]*([.,])[[:space:]]*', '\\1', 'g'))")


def _ref_sql(x: str, strategy: str) -> str:
    """`validate._normalize_ref` in SQL, for one rule's strategy."""
    return (f"CASE WHEN {strategy} = 'strip-trailing-letters' AND {x} ~ '^[0-9]+[A-Za-z]{{1,3}}$' "
            f"THEN substring({x} from '^([0-9]+)') "
            f"WHEN {strategy} = 'reorder-shank-figure-size' THEN fold_bur_code({x}) ELSE {x} END")


def overlap_sql(rows: list[dict], min_unscoped_len: int = 6) -> str:
    """REF list and Basic UDI-DI against the catalogue, per (document, arm),
    computed on the server inside a read-only transaction.

    The scope manufacturer is VALIDATE's: the requesting group's canonical
    manufacturer for a document that came through a group, else the printed
    manufacturer canonicalized through `manufacturer_alias` (the alias with the
    lowest `raw_name` folding to it, else the name itself). Then:

    * VALIDATE's REF gate (C1): every `item_group` under that manufacturer,
      its members' `mfr_ref` and `item_ref` against the list, both sides
      through the manufacturer's playbook `ref_normalize` rule; a group whose
      `basic_udi_di` equals the document's links all its members.
      `in_group_items` counts the requesting group only, which is what
      VALIDATE uses for a group document.
    * the item mirror: the rows of that manufacturer's BC codes (as
      `manufacturers.item_codes_for` resolves them), `mfr_ref` / `item_ref`
      against the list and `udi` against the Basic UDI-DI.
    * with no manufacturer, VALIDATE's unscoped path: `unscoped_holders`, the
      manufacturers holding an eligible REF (`_manufacturers_holding_refs`
      over `_eligible_unscoped_refs`), and `unscoped_any`, the groups any REF
      or the Basic UDI-DI matches as written (`_resolve_group_unscoped`, here
      without the per-manufacturer REF rule).
    """
    rules = []
    for pb in playbooks_mod.load_playbooks():
        if pb.ref_normalize:
            rules += [(n.casefold(), pb.ref_normalize.get("strategy")) for n in pb.names()]
    payload = json.dumps(rows, ensure_ascii=False)
    if "$in$" in payload:
        raise SystemExit("the input carries the dollar-quote tag; nothing printed")
    lit = lambda v: "'" + str(v).replace("'", "''") + "'"   # noqa: E731
    rule_values = ",\n    ".join(f"({lit(n)}, {lit(st)})" for n, st in rules) or "(NULL, NULL)"
    nm = _norm_sql
    canon_of = (lambda x: f"COALESCE((SELECT a.canonical_name FROM alias a WHERE a.nraw = {nm(x)} "
                          f"ORDER BY a.raw_name LIMIT 1), {x})")
    return f"""BEGIN READ ONLY;
SET LOCAL statement_timeout = '120s';
WITH input AS (
  SELECT * FROM jsonb_to_recordset($in${payload}$in$::jsonb)
    AS x(doc_id bigint, arm text, group_id bigint, printed text, refs jsonb, udi text)
), alias AS (
  SELECT raw_name, canonical_name, {nm('raw_name')} AS nraw, {nm('canonical_name')} AS ncanon
  FROM manufacturer_alias
), rule(name, strategy) AS (VALUES
    {rule_values}
), scope AS (
  SELECT i.*, CASE
      WHEN i.group_id IS NOT NULL THEN g.canonical_manufacturer
      WHEN COALESCE(btrim(i.printed), '') = '' THEN NULL
      ELSE {canon_of('i.printed')} END AS canon
  FROM input i LEFT JOIN item_group g ON g.group_id = i.group_id
), s2 AS (
  SELECT s.*, (SELECT r.strategy FROM rule r WHERE r.name = lower(s.canon)) AS strategy,
         {nm(canon_of('s.canon'))} AS ncanon_of
  FROM scope s
), refs AS (
  SELECT DISTINCT s.doc_id, s.arm, r.ref, {_ref_sql('r.ref', 's.strategy')} AS key
  FROM s2 s, jsonb_array_elements_text(COALESCE(s.refs, '[]'::jsonb)) AS r(ref)
  WHERE btrim(r.ref) <> ''
), grp AS (
  SELECT s.doc_id, s.arm, ig.group_id, ig.basic_udi_di
  FROM s2 s JOIN item_group ig ON {nm('ig.canonical_manufacturer')} = {nm('s.canon')}
  WHERE s.canon IS NOT NULL
), mem AS (
  SELECT g.doc_id, g.arm, g.group_id, g.basic_udi_di, m.item_ref,
         {_ref_sql('m.mfr_ref', 's.strategy')} AS kmfr, {_ref_sql('m.item_ref', 's.strategy')} AS kitem
  FROM grp g JOIN s2 s ON s.doc_id = g.doc_id AND s.arm = g.arm
  JOIN item_group_member m ON m.group_id = g.group_id
), hit AS (
  SELECT m.doc_id, m.arm, m.group_id, m.item_ref, 'ref-list' AS basis, r.ref
  FROM mem m JOIN refs r ON r.doc_id = m.doc_id AND r.arm = m.arm AND r.key = m.kmfr
  UNION
  SELECT m.doc_id, m.arm, m.group_id, m.item_ref, 'ref-item', r.ref
  FROM mem m JOIN refs r ON r.doc_id = m.doc_id AND r.arm = m.arm AND r.key = m.kitem
  UNION
  SELECT m.doc_id, m.arm, m.group_id, m.item_ref, 'basic-udi-di', NULL
  FROM mem m JOIN s2 s ON s.doc_id = m.doc_id AND s.arm = m.arm
  WHERE s.udi IS NOT NULL AND m.basic_udi_di = s.udi
), uref AS (
  SELECT r.doc_id, r.arm, r.ref, btrim(r.ref) AS t,
         length(btrim(r.ref)) >= {int(min_unscoped_len)} AND position('!' in r.ref) = 0 AS eligible
  FROM refs r JOIN s2 s ON s.doc_id = r.doc_id AND s.arm = r.arm WHERE s.canon IS NULL
), uhit AS (
  SELECT u.doc_id, u.arm, gm.group_id, u.eligible
  FROM uref u JOIN item_group_member gm ON gm.mfr_ref = u.t
  UNION
  SELECT u.doc_id, u.arm, gm.group_id, u.eligible
  FROM uref u JOIN item_group_member gm ON gm.item_ref = u.t
  UNION
  SELECT s.doc_id, s.arm, ig.group_id, FALSE
  FROM s2 s JOIN item_group ig ON ig.basic_udi_di = s.udi WHERE s.canon IS NULL
), codes AS (
  SELECT DISTINCT s.doc_id, s.arm, im.manufacturer_raw AS code
  FROM s2 s JOIN (SELECT DISTINCT manufacturer_raw, {nm('manufacturer_raw')} AS nm
                  FROM item_mirror WHERE manufacturer_raw IS NOT NULL) im
    ON im.nm IN ({nm('s.canon')}, s.ncanon_of)
    OR im.nm IN (SELECT a.nraw FROM alias a WHERE a.ncanon = s.ncanon_of)
  WHERE s.canon IS NOT NULL
), mir AS (
  SELECT c.doc_id, c.arm, im.item_ref, im.udi,
         {_ref_sql('im.mfr_ref', 's.strategy')} AS kmfr, {_ref_sql('im.item_ref', 's.strategy')} AS kitem
  FROM codes c JOIN s2 s ON s.doc_id = c.doc_id AND s.arm = c.arm
  JOIN item_mirror im ON im.manufacturer_raw = c.code
), mirror AS (
  SELECT m.doc_id, m.arm, count(DISTINCT m.item_ref) AS items,
    count(DISTINCT m.item_ref) FILTER (WHERE EXISTS (SELECT 1 FROM refs r
        WHERE r.doc_id = m.doc_id AND r.arm = m.arm AND r.key IN (m.kmfr, m.kitem))) AS ref_items,
    count(DISTINCT m.item_ref) FILTER (WHERE s.udi IS NOT NULL AND m.udi = s.udi) AS udi_items
  FROM mir m JOIN s2 s ON s.doc_id = m.doc_id AND s.arm = m.arm
  GROUP BY 1, 2
)
SELECT json_build_object('doc_id', s.doc_id, 'arm', s.arm, 'canon', s.canon, 'strategy', s.strategy,
  'refs', jsonb_array_length(COALESCE(s.refs, '[]'::jsonb)), 'udi', s.udi,
  'groups_found', (SELECT count(*) FROM grp g WHERE g.doc_id = s.doc_id AND g.arm = s.arm),
  'groups_matched', (SELECT count(DISTINCT h.group_id) FROM hit h
                     WHERE h.doc_id = s.doc_id AND h.arm = s.arm),
  'items_matched', (SELECT count(DISTINCT h.item_ref) FROM hit h
                    WHERE h.doc_id = s.doc_id AND h.arm = s.arm),
  'basis', (SELECT json_object_agg(b, n) FROM (SELECT h.basis AS b, count(DISTINCT h.item_ref) AS n
            FROM hit h WHERE h.doc_id = s.doc_id AND h.arm = s.arm GROUP BY 1) x),
  'in_group_items', (SELECT count(DISTINCT h.item_ref) FROM hit h WHERE h.doc_id = s.doc_id
                     AND h.arm = s.arm AND h.group_id = s.group_id),
  'udi_group_match', (SELECT count(*) FROM grp g WHERE g.doc_id = s.doc_id AND g.arm = s.arm
                      AND s.udi IS NOT NULL AND g.basic_udi_di = s.udi),
  'udi_anywhere_groups', (SELECT count(*) FROM item_group ig WHERE s.udi IS NOT NULL
                          AND ig.basic_udi_di = s.udi),
  'unscoped_holders', (SELECT count(DISTINCT ig.canonical_manufacturer)
       FROM uhit u JOIN item_group ig ON ig.group_id = u.group_id
       WHERE u.doc_id = s.doc_id AND u.arm = s.arm AND u.eligible),
  'unscoped_any', (SELECT count(DISTINCT u.group_id) FROM uhit u
                   WHERE u.doc_id = s.doc_id AND u.arm = s.arm),
  'mirror_items', COALESCE(m.items, 0), 'mirror_ref_items', COALESCE(m.ref_items, 0),
  'mirror_udi_items', COALESCE(m.udi_items, 0),
  'sample', (SELECT json_agg(y.ref) FROM (SELECT DISTINCT h.ref FROM hit h
             WHERE h.doc_id = s.doc_id AND h.arm = s.arm AND h.ref IS NOT NULL
             ORDER BY h.ref LIMIT 5) y))
FROM s2 s LEFT JOIN mirror m ON m.doc_id = s.doc_id AND m.arm = s.arm
ORDER BY s.doc_id, s.arm;
ROLLBACK;
"""


# --------------------------------------------------------------------------- #
# The selection
# --------------------------------------------------------------------------- #
def _local(archive_url: str | None) -> str | None:
    if not archive_url:
        return None
    if not archive_url.startswith(ARCHIVE):
        raise SystemExit(f"not an archive path: {archive_url}")
    return "pdfs/" + archive_url[len(ARCHIVE):]


def load_selection(run: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in (run / "selection.jsonl").read_text().splitlines()
            if line.startswith("{")]


def choose(rows: list[dict], nii_sample: int | None, seed: int) -> tuple[list[dict], dict]:
    """Every gate-confidence document; the no-item-identifier half whole, or a
    random sample of it when the estimate did not fit: the first `nii_sample`
    of its documents shuffled with a fixed seed, so a larger sample holds a
    smaller one and a run can grow without paying twice."""
    keep = [r for r in rows if r["selected_as"] == GATE_CONF]
    nii = sorted((r for r in rows if r["selected_as"] == NII_TEXT), key=lambda r: r["doc_id"])
    order = list(nii)
    random.Random(seed).shuffle(order)
    chosen = nii if nii_sample is None else order[:nii_sample]
    out = keep + sorted(chosen, key=lambda r: r["doc_id"])
    return out, {"gate_confidence": len(keep), "nii_selected": len(nii), "nii_run": len(chosen),
                 "seed": seed if nii_sample is not None else None,
                 "trimmed_doc_ids": sorted({r["doc_id"] for r in nii} - {r["doc_id"] for r in chosen})}


def to_doc(r: dict) -> dict:
    return {"set": "text" if r["text_source"] == "pdf-text" else "scan",
            "content_hash": r["content_hash"], "path": _local(r["archive_url"]), "gt": None,
            "group_manufacturer": r.get("group_manufacturer"),
            "companion": _local(r.get("companion_archive_url")), "doc_id": r["doc_id"]}


def cmd_files(args) -> int:
    run = pathlib.Path(args.run)
    paths = []
    for r in load_selection(run):
        paths += [u[len(ARCHIVE):] for u in (r["archive_url"], r.get("companion_archive_url")) if u]
    (run / "files.txt").write_text("\n".join(sorted(set(paths))) + "\n")
    print(f"{len(set(paths))} files -> {run / 'files.txt'}")
    return 0


def cmd_run(args) -> int:
    run = pathlib.Path(args.run)
    rows, sample = choose(load_selection(run), args.nii_sample, args.seed)
    docs = [to_doc(r) for r in rows]
    missing = [d["path"] for d in docs if not (run / d["path"]).exists()]
    missing += [d["companion"] for d in docs if d["companion"] and not (run / d["companion"]).exists()]
    if missing:
        raise SystemExit(f"{len(missing)} PDFs not copied, e.g. {missing[:3]}")
    if not (args.dry_run or args.simulate):
        lq._write_json(run / "sample.json", {**sample, "doc_ids": [r["doc_id"] for r in rows]})
    print(f"selection: {sample['gate_confidence']} gate-confidence, {sample['nii_run']} of "
          f"{sample['nii_selected']} no-item-identifier text"
          + (f" (seed {sample['seed']})" if sample["seed"] is not None else ""))
    main = lq.main_checkout()
    out = run / "simulate" if args.simulate else run
    return lq.drive(docs, run, lq.Cache(main / lq.BASE_CACHE, run / "cache"),
                    lq.State(run / "state.json"), run, out, args, env_root=main,
                    finish=lambda _out: 0)


def cmd_partial(args) -> int:
    """A run the cap stopped: every document of its sample replayed from the
    cache (no API), `results-partial.json` holding the finished ones and, for
    the rest, the step each is waiting on."""
    run = pathlib.Path(args.run)
    ran = json.loads((run / "sample.json").read_text())["doc_ids"]
    rows = [r for r in load_selection(run) if r["doc_id"] in set(ran)]
    docs = [to_doc(r) for r in rows]
    cache = lq.Cache(lq.main_checkout() / lq.BASE_CACHE, run / "cache")
    state = lq.State(run / "state.json")
    results, misses = lq.run_round(docs, run, cache, lq.config(), state.exhausted())
    by_hash = {r["content_hash"]: r["doc_id"] for r in rows}
    pending = {str(by_hash[m["content_hash"]]): m["label"] for m in misses.values()}
    lq._write_json(run / "results-partial.json", {
        "simulated": False, "partial": True, "ladder": str(lq.REPO), "written_at": lq._now(),
        "pending": pending, "docs": results})
    print(f"{len(results)} finished, {len(pending)} pending "
          f"{dict(collections.Counter(pending.values()))} -> {run / 'results-partial.json'}")
    return 0


def cmd_sizes(args) -> int:
    """The trim decision: every document's simulated cost (`--simulate`'s
    stand-in answers, Batch API prices, the harness's estimate), and the total
    for each size of the no-item-identifier sample, in the seeded order `run
    --nii-sample` takes it."""
    run = pathlib.Path(args.run)
    rows, _ = choose(load_selection(run), None, args.seed)
    docs = [to_doc(r) for r in rows]
    main = lq.main_checkout()
    cache = lq.Cache(main / lq.BASE_CACHE, run / "cache")
    outs = cache.output_estimate()
    _, by_doc = lq.run_round(docs, run, cache, lq.config(), set(), lq.standin_for(cache))
    usd = {d["doc_id"]: sum((lq.estimate_usd(q, outs) for q in by_doc.get(d["content_hash"], [])),
                            Decimal(0)) for d in docs}
    gc = sum(usd[r["doc_id"]] for r in rows if r["selected_as"] == GATE_CONF)
    out = {"gate_confidence_usd": str(gc), "per_doc": {str(k): str(v) for k, v in usd.items()},
           "sizes": {}}
    print(f"gate-confidence, all {sum(r['selected_as'] == GATE_CONF for r in rows)}: ~${gc:.4f}")
    n_nii = sum(r["selected_as"] == NII_TEXT for r in rows)
    for n in sorted({*range(10, n_nii, 10), n_nii}):
        sample, _ = choose(rows, n, args.seed)
        nii = sum(usd[r["doc_id"]] for r in sample if r["selected_as"] == NII_TEXT)
        out["sizes"][n] = str(gc + nii)
        print(f"  + {n:3} no-item-identifier: ~${nii:.4f}, total ~${gc + nii:.4f}")
    lq._write_json(run / "sizes.json", out)
    return 0


# --------------------------------------------------------------------------- #
# Stored and new, side by side
# --------------------------------------------------------------------------- #
def stored_fields(r: dict) -> dict:
    """The registry's current evidence per field, in the extraction's shape."""
    out = {}
    for e in r["evidence"] or []:
        v = e["value"]
        if e["field"] == "ref_list":
            try:
                v = json.loads(v)
            except ValueError:
                v = [v]
        out[e["field"]] = {"value": v, "conf": float(e["confidence"]), "verbatim": e["verbatim"],
                           "page": e["page"], "tier": e["tier"], "model_id": e["model_id"]}
    return out


def _v(fields: dict | None, f: str):
    ev = (fields or {}).get(f)
    return ev.get("value") if isinstance(ev, dict) else None


def overlap_rows(run: pathlib.Path, results: str = "results.json") -> list[dict]:
    res = json.loads((run / results).read_text())["docs"]
    ran = set(json.loads((run / "sample.json").read_text())["doc_ids"])
    rows = []
    for r in load_selection(run):
        if r["doc_id"] not in ran:
            continue
        new = res.get(r["content_hash"])
        arms = {"stored": stored_fields(r)}
        if new and not new.get("error") and new["doc_class"] != tiers.NOT_A_DOCUMENT:
            arms["new"] = new["fields"]
        for arm, f in arms.items():
            refs = _v(f, "ref_list") or []
            rows.append({"doc_id": r["doc_id"], "arm": arm, "group_id": r["extract_group_id"],
                         "printed": _v(f, "manufacturer"), "refs": [str(x) for x in refs],
                         "udi": _v(f, "basic_udi_di")})
    return rows


def cmd_sql(args) -> int:
    if args.what == "select":
        sys.stdout.write(SELECT_SQL)
        return 0
    run = pathlib.Path(args.run)
    rows = overlap_rows(run, args.results)
    lq._write_json(run / "overlap-input.json", rows)
    sys.stdout.write(overlap_sql(rows))
    return 0


def outcome(fields: dict | None, doc_class: str | None, r: dict, ov: dict | None, cfg) -> dict:
    """What GATE would do with these fields: its own `_score`, thresholds and
    flag classes, in its order; VALIDATE's REF gate from the server's answer
    (`ov`), its field rules recomputed here, and its registry-bound flags
    (C6 / C7 dates, cited certificate) carried over from the stored result."""
    if fields is None or doc_class == tiers.NOT_A_DOCUMENT:
        return {"disposition": "refused", "score": None, "flags": [], "why": "not a compliance document"}
    empty = [f for f in gate.REQUIRED_FIELDS if _v(fields, f) in (None, "", [])]
    if empty:
        return {"disposition": "no-candidate", "score": None, "flags": [],
                "why": f"VALIDATE suppresses: {', '.join(empty)} empty"}
    refs, udi = _v(fields, "ref_list") or [], _v(fields, "basic_udi_di")
    flags = set(tiers.integrity_flags(fields))
    if _v(fields, "coverage_scope") == "manufacturer" and not refs and not udi:
        return {"disposition": "mfr-binding", "score": None, "flags": sorted(flags),
                "why": "manufacturer scope, no item identifier: the binding route"}
    if not refs and not udi:
        flags.add("no-item-identifier")
    ov = ov or {}
    ref_gate, links = False, 0
    if r["extract_group_id"] is not None:
        # The requesting group's members only; no match with identifiers
        # links every member at the capped `fetch-context` basis.
        links = ov.get("in_group_items", 0)
        ref_gate = links > 0
        if not ref_gate and (refs or udi):
            links = -1
    elif ov.get("canon"):
        links = ov.get("items_matched", 0)
        if links:
            ref_gate = True
            if ov.get("groups_matched", 0) > 1:
                flags.add("multi-group-match")
        elif ov.get("groups_found", 0) == 0:
            flags.add("manufacturer-unresolved")
        else:
            flags.add("no-ref-overlap")
    elif refs or udi:
        # No manufacturer to scope by ([validate-ref-catalogue]): a REF held
        # by exactly one manufacturer proposes capped `ref-catalogue` links.
        holders = ov.get("unscoped_holders", 0)
        if holders == 1:
            flags.add("ref-catalogue")
            links = -1
        elif holders > 1:
            flags.add("multi-manufacturer-ref")
        if links == 0:
            if ov.get("unscoped_any", 0):
                flags.add("ref-unscoped")
            elif refs:
                flags.add("no-ref-overlap")
    vf, vt = _v(fields, "validity_from"), _v(fields, "validity_to")
    pf, pt = validate._to_date(vf), validate._to_date(vt)
    if (vf and pf is None) or (vt and pt is None) or (pf and pt and pf > pt) or any(
            d and not (validate._YEAR_MIN <= d.year <= validate._YEAR_MAX) for d in (pf, pt)):
        flags.add("date-insane")
    if _v(fields, "type") == "DoC" and pt is not None and not validate._expiry_is_stated(fields):
        flags.add("expiry-on-certless-doc")
    if not gate._is_md_document(fields):
        flags.add("not-a-device-document")
    try:
        flags |= set(gate._require_complete_evidence(fields))
    except ValueError as exc:
        return {"disposition": "manual", "score": None, "flags": sorted(flags), "why": str(exc)}
    flags |= {f for f in (r["gate_result"].get("flags") or []) if f not in RECOMPUTED}
    score = gate._score(fields, {}, cfg.agreement_min)
    blocking = sorted(f for f in flags if f in gate.BLOCKING_FLAGS)
    if score >= cfg.high and ref_gate and not gate._gates_disposition(flags):
        disp, why = "production", ""
    elif score >= cfg.med and not blocking and gate._is_out_of_catalogue(
            {"links": [] if links == 0 else [links]}, fields, flags):
        disp, why = "filed", "covers nothing Dentalia holds (C15)"
    elif score >= cfg.med and not blocking:
        disp = "staged"
        holds = [f for f in sorted(flags) if f not in gate.INFORMATIONAL_FLAGS]
        why = ("score below 0.92" if score < cfg.high else
               ", ".join(holds) if holds else "no REF match (no ref_gate)")
        if score < cfg.high and holds:
            why += "; " + ", ".join(holds)
    else:
        disp = "manual"
        why = ", ".join(blocking) if blocking else "score below 0.75"
    return {"disposition": disp, "score": score, "flags": sorted(flags), "why": why,
            "ref_gate": ref_gate, "links": max(links, 0) if links >= 0 else "capped"}


def _quote(ev: dict | None) -> str:
    if not ev:
        return "-"
    step = ev.get("step") or ev.get("tier") or ""
    q = " ".join(str(ev.get("verbatim") or "").split())[:160]
    return f"({step} {ev.get('model_id') or ''} {ev.get('conf')}, p{ev.get('page')}) \"{q}\""


def _on_page(ev: dict | None, pages: list[str]) -> bool | None:
    if not ev or not ev.get("verbatim") or not any(p.strip() for p in pages):
        return None
    return hc._verbatim_ok(ev["verbatim"], ev.get("page"), pages)


def cmd_report(args) -> int:
    run = pathlib.Path(args.run)
    res = json.loads((run / args.results).read_text())
    sample = json.loads((run / "sample.json").read_text())
    state = lq.State(run / "state.json")
    cfg = lq.config()
    ov = collections.defaultdict(dict)
    if (run / "overlap.jsonl").exists():
        for line in (run / "overlap.jsonl").read_text().splitlines():
            if line.startswith("{"):
                o = json.loads(line)
                ov[o["doc_id"]][o["arm"]] = o
    sel = {r["doc_id"]: r for r in load_selection(run)}
    review = {(v["doc_id"], v["field"]): v for v in
              (json.loads((run / "review.json").read_text()) if (run / "review.json").exists() else [])}
    rows, field_rows = [], []
    for doc_id in sample["doc_ids"]:
        r = sel[doc_id]
        x = res["docs"].get(r["content_hash"])
        if x is None and res.get("partial"):
            continue                       # still pending when the run stopped
        stored_att = r["attempt"]["fields"]
        st = stored_fields(r)
        o_st, o_new = ov[doc_id].get("stored"), ov[doc_id].get("new")
        before = outcome(stored_att, None, r, o_st, cfg)
        new_fields = None if not x or x.get("error") else x["fields"]
        after = outcome(new_fields, (x or {}).get("doc_class"), r, o_new, cfg)
        with pdfutil.open_doc(str(run / _local(r["archive_url"]))) as d:
            pages, has_text = pdfutil.page_texts(d), pdfutil.has_text_page(d)
        changed = []
        for f in SHOWN_FIELDS:
            sv, nv = st.get(f), (new_fields or {}).get(f)
            if hc.norm(f, _v({f: sv}, f)) == hc.norm(f, _v({f: nv}, f)):
                continue
            changed.append(f)
            field_rows.append({
                "doc_id": doc_id, "selected_as": r["selected_as"], "field": f,
                "stored": lq._short(_v({f: sv}, f)), "stored_quote": _quote(sv),
                "stored_on_page": _on_page(sv, pages),
                "new": lq._short(_v({f: nv}, f)), "new_quote": _quote(nv),
                "new_on_page": _on_page(nv, pages),
                "lost": _v({f: sv}, f) not in (None, "", []) and _v({f: nv}, f) in (None, "", []),
                "verdict": review.get((doc_id, f), {}).get("verdict", ""),
                "note": review.get((doc_id, f), {}).get("note", ""),
                "file": pathlib.Path(r["archive_url"]).name})
        used = (x or {}).get("used") or []
        rows.append({
            "doc_id": doc_id, "selected_as": r["selected_as"],
            "branch": "text" if has_text else "scan",
            "file": pathlib.Path(r["archive_url"]).name,
            "manufacturer": (o_new or o_st or {}).get("canon") or r["canonical_manufacturer"] or "",
            "stored_score": r["gate_result"].get("score"),
            "stored_score_recomputed": None if before["score"] is None else round(before["score"], 4),
            "stored_disposition": r["gate_result"].get("disposition"),
            "stored_flags": ",".join(r["gate_result"].get("flags") or []),
            "simulated_before": before["disposition"],
            "new_doc_class": (x or {}).get("doc_class", ""),
            "new_score": None if after["score"] is None else round(after["score"], 4),
            "clears_092": after["score"] is not None and after["score"] >= cfg.high,
            "new_disposition": after["disposition"],
            "leaves_review": after["disposition"] in ("production", "filed", "superseded"),
            "stays_on": "" if after["disposition"] in ("production", "filed") else after["why"],
            "new_flags": ",".join(after["flags"]),
            "stored_refs": len(_v(st, "ref_list") or []), "new_refs": len(_v(new_fields, "ref_list") or []),
            "stored_udi": _v(st, "basic_udi_di") or "", "new_udi": _v(new_fields, "basic_udi_di") or "",
            "new_items_matched": (o_new or {}).get("items_matched"),
            "new_unscoped_holders": (o_new or {}).get("unscoped_holders"),
            "new_mirror_ref_items": (o_new or {}).get("mirror_ref_items"),
            "new_udi_group_match": (o_new or {}).get("udi_group_match"),
            "new_mirror_udi_items": (o_new or {}).get("mirror_udi_items"),
            "reads": "+".join(used), "sonnet": "T2" in used,
            "disagreements": len((x or {}).get("disagreements") or []),
            "changed_fields": ",".join(changed), "error": (x or {}).get("error", "" if x else "not run")})
    _write_csv(run / "documents.csv", rows)
    for v in review.values():               # a verdict on a document, not a field
        if v["field"] == "*" and v["doc_id"] in {x["doc_id"] for x in rows}:
            field_rows.append({"doc_id": v["doc_id"], "selected_as": sel[v["doc_id"]]["selected_as"],
                               "field": "*", "stored": "", "stored_quote": "", "stored_on_page": None,
                               "new": "", "new_quote": "", "new_on_page": None, "lost": False,
                               "verdict": v["verdict"], "note": v["note"],
                               "file": pathlib.Path(sel[v["doc_id"]]["archive_url"]).name})
    _write_csv(run / "fields.csv", field_rows)
    text = render(run, rows, field_rows, sample, state, res)
    (run / "report.md").write_text(text)
    print(text)
    return 0


def _write_csv(path: pathlib.Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


#: How far a disposition is from leaving review: lower is better.
_RANK = {"production": 0, "filed": 0, "superseded": 0, "staged": 1, "mfr-binding": 1,
         "manual": 2, "no-candidate": 2, "refused": 3}


def render(run, rows, field_rows, sample, state, res) -> str:
    C = collections.Counter
    L = ["# Extraction ladder, Task 9: replay on production's review queue", ""]
    if res.get("simulated"):
        L += ["**SIMULATED RUN: stand-in answers, not real reads.**", ""]
    if res.get("partial"):
        pend = res.get("pending") or {}
        L += [f"**PARTIAL: the run stopped at the cap. {len(rows)} documents finished; "
              f"{len(pend)} were still waiting for a read ({dict(collections.Counter(pend.values()))}). "
              "The finished ones are the ones that needed no further read, so they are not a "
              "random sample of the selection.**", ""]
    L += [f"Run `{run.name}`, ladder from `{res.get('ladder')}`, written {res.get('written_at')}.", ""]
    if (run / "queue-reading.txt").exists():
        q = [x for x in (run / "queue-reading.txt").read_text().splitlines() if "|" in x]
        L += ["## Production's review queue (read-only reading, `queue-reading.sql`)", "",
              f"Read {(run / 'queue-reading.txt').read_text().splitlines()[0]}: "
              f"{sum(int(x.split('|')[2]) for x in q)} documents staged. What holds each "
              "(latest `gate.candidate` result), by text layer:", "",
              "| text layer | hold | documents |", "|---|---|---|"]
        L += [f"| {a} | {b} | {c} |" for a, b, c in (x.split("|") for x in q)] + [""]
    L += ["## Selection and spend", "",
          f"- Selected {sample['gate_confidence']} gate-confidence documents (no blocking flag, not "
          f"the binding route, score below 0.92) and {sample['nii_selected']} text documents "
          "carrying `no-item-identifier`.",
          f"- Run: all {sample['gate_confidence']} gate-confidence and {sample['nii_run']} of the "
          f"{sample['nii_selected']} no-item-identifier documents"
          + (f" (the first {sample['nii_run']} of a shuffle with seed {sample['seed']}; "
             f"{len(sample['trimmed_doc_ids'])} not run, listed in `sample.json`)"
             if sample.get("seed") is not None else "") + ".",
          "- Batch API spend (`state.json`):", ""]
    for b, v in state.data["batches"].items():
        L.append(f"  - `{b}`: {len(v['requests'])} requests, estimate ${Decimal(v['estimate_usd']):.4f}, "
                 + (f"measured ${Decimal(v['cost_usd']):.4f}" if v.get("collected") else "not collected"))
    L += [f"  - total ${state.spent():.4f}", ""]

    leave = [x for x in rows if x["leaves_review"]]
    L += ["## Headline", "",
          f"- **Would leave review: {len(leave)} of {len(rows)}** "
          f"({dict(C(x['new_disposition'] for x in leave))}).",
          f"- Stay: {len(rows) - len(leave)}, by disposition "
          f"{dict(C(x['new_disposition'] for x in rows if not x['leaves_review']))}.",
          f"- New score clears 0.92: {sum(x['clears_092'] for x in rows)} of {len(rows)}.",
          f"- Refused as not a compliance document: {sum(x['new_disposition'] == 'refused' for x in rows)} "
          f"({[x['doc_id'] for x in rows if x['new_disposition'] == 'refused']}).", "",
          "Disposition, stored (production's latest `gate.candidate`) to new (simulated):", "",
          "| stored | new | documents |", "|---|---|---|"]
    for (a, b), n in sorted(C((x["stored_disposition"], x["new_disposition"]) for x in rows).items()):
        L.append(f"| {a} | {b} | {n} |")
    L += ["", "How the new disposition is simulated: GATE's own `_score`, thresholds (0.92 / 0.75) and "
          "flag classes, in GATE's order; VALIDATE's REF gate recomputed on the server's catalogue "
          "(`overlap.sql`, read-only); VALIDATE's field rules (no-item-identifier, dates, expiry, "
          "device document, evidence pages) recomputed on the new fields; the flags that need the "
          "registry (C6 / C7 date ordering, cited certificate) carried over from the stored result. "
          "On the stored fields the same simulation reproduces production's links, flags, score and "
          "disposition: "
          f"{sum(x['simulated_before'] == x['stored_disposition'] for x in rows)} of {len(rows)} "
          "dispositions.", ""]
    if (run / "notes.md").exists():         # the reviewer's reading of the results
        L += [(run / "notes.md").read_text().rstrip(), ""]
    for sel in (GATE_CONF, NII_TEXT):
        xs = [x for x in rows if x["selected_as"] == sel]
        if not xs:
            continue
        n = len(xs)
        L += [f"## {sel} ({n})", "",
              f"- new score clears 0.92: {sum(x['clears_092'] for x in xs)} of {n} "
              f"(stored: {sum((x['stored_score'] or 0) >= 0.92 for x in xs)})",
              f"- would leave review: {sum(x['leaves_review'] for x in xs)} "
              f"({dict(C(x['new_disposition'] for x in xs if x['leaves_review']))})",
              f"- stay: {sum(not x['leaves_review'] for x in xs)}, by disposition "
              f"{dict(C(x['new_disposition'] for x in xs if not x['leaves_review']))}",
              "- stay on:", ""]
        L += [f"  - {k or '-'}: {v}" for k, v in
              C(x["stays_on"] for x in xs if not x["leaves_review"]).most_common()]
        L += ["", f"- branch: {dict(C(x['branch'] for x in xs))}; documents with a Sonnet read: "
              f"{sum(x['sonnet'] for x in xs)}", ""]
        if sel == NII_TEXT:
            found = [x for x in xs if x["new_refs"] or x["new_udi"]]
            L += [f"- a REF list or Basic UDI-DI now found: {len(found)} of {n} "
                  f"(REF list {sum(bool(x['new_refs']) for x in xs)}, UDI {sum(bool(x['new_udi']) for x in xs)}): "
                  f"{[x['doc_id'] for x in found]}",
                  f"- overlaps the catalogue for its manufacturer: VALIDATE's REF gate "
                  f"{sum(bool(x['new_items_matched']) for x in found)}; item mirror REF "
                  f"{sum(bool(x['new_mirror_ref_items']) for x in found)}; UDI in a group "
                  f"{sum(bool(x['new_udi_group_match']) for x in found)}, in the mirror "
                  f"{sum(bool(x['new_mirror_udi_items']) for x in found)}; no manufacturer, REF held "
                  f"by one manufacturer (capped `ref-catalogue`): "
                  f"{sum(x['new_unscoped_holders'] == 1 for x in found)}", ""]

    worse_disp = [x for x in rows if _RANK.get(x["new_disposition"], 2)
                  > _RANK.get(x["stored_disposition"], 2)]
    worse_fields = [f for f in field_rows if f["verdict"] == "worse"]
    L += ["## Regressions: read worse than production", "",
          f"- Documents refused as not a compliance document: "
          f"{[x['doc_id'] for x in rows if x['new_disposition'] == 'refused']}",
          f"- Disposition further from leaving review than production's: "
          f"{[(x['doc_id'], x['stored_disposition'], x['new_disposition']) for x in worse_disp]}",
          f"- Fields judged worse (`review.json`, read against the PDF): {len(worse_fields)} on "
          f"{len({f['doc_id'] for f in worse_fields})} documents", ""]
    if worse_fields:
        L += ["| doc | field | stored | new | why |", "|---|---|---|---|---|"]
        L += [f"| {f['doc_id']} | {f['field']} | {f['stored']} {f['stored_quote']} | {f['new']} "
              f"{f['new_quote']} | {f['note']} |".replace("\n", " ") for f in worse_fields]
        L.append("")

    L += ["## Fields that changed from the stored value", "",
          f"{len([f for f in field_rows if f['field'] != '*'])} fields on "
          f"{len({f['doc_id'] for f in field_rows if f['field'] != '*'})} documents; by field "
          f"{dict(C(f['field'] for f in field_rows if f['field'] != '*').most_common())}; verdicts "
          f"{dict(C(f['verdict'] or 'not reviewed' for f in field_rows).most_common())}. "
          "Quote on its page (`haiku_compare._verbatim_ok`, text pages only): stored / new.", "",
          "| doc | field | stored | new | verdict |", "|---|---|---|---|---|"]
    mark = {True: "on page", False: "NOT on page", None: "-"}
    for f in field_rows:
        if f["field"] == "*":
            continue
        L.append(f"| {f['doc_id']} | {f['field']} | {f['stored']} {f['stored_quote']} "
                 f"[{mark[f['stored_on_page']]}] | {f['new']} {f['new_quote']} "
                 f"[{mark[f['new_on_page']]}] | {f['verdict']} {f['note']} |".replace("\n", " "))
    L += ["", "## Per document", "",
          "| doc | sel | stored | new score | new | stays on | refs st/new | items | changed |",
          "|---|---|---|---|---|---|---|---|---|"]
    for x in rows:
        L.append(f"| {x['doc_id']} | {'gc' if x['selected_as'] == GATE_CONF else 'nii'} | "
                 f"{x['stored_disposition']} {x['stored_score']} | {x['new_score']} | "
                 f"{x['new_disposition']} | {x['stays_on']} | {x['stored_refs']}/{x['new_refs']} | "
                 f"{x['new_items_matched']} | {x['changed_fields']} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.prod_replay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sql")
    p.add_argument("what", choices=("select", "overlap"))
    p.add_argument("--run")
    p.add_argument("--results", default="results.json")
    p = sub.add_parser("files")
    p.add_argument("--run", required=True)
    p = sub.add_parser("run")
    p.add_argument("--run", required=True)
    p.add_argument("--cap-usd", type=float, default=float(CAP_USD))
    p.add_argument("--nii-sample", type=int, help="run this many no-item-identifier documents")
    p.add_argument("--seed", type=int, default=SEED)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--simulate", action="store_true")
    p.add_argument("--adopt", metavar="BATCH_ID")
    p.add_argument("--forget-intent", action="store_true")
    p = sub.add_parser("partial", help="the documents a stopped run finished, from its cache; no API")
    p.add_argument("--run", required=True)
    p = sub.add_parser("sizes")
    p.add_argument("--run", required=True)
    p.add_argument("--seed", type=int, default=SEED)
    p = sub.add_parser("report")
    p.add_argument("--run", required=True)
    p.add_argument("--results", default="results.json")
    args = ap.parse_args(argv)
    return {"sql": cmd_sql, "files": cmd_files, "run": cmd_run, "sizes": cmd_sizes,
            "partial": cmd_partial, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
