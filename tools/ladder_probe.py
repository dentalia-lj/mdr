"""Routing variants for the extraction ladder, measured on real documents.

An evaluation, not a pipeline change. Builds on a `tools.haiku_compare` run: the
T1 answers there (Haiku 5.5) are replayed through deterministic evidence checks,
and every field still open is sent to three reads, each built by the pipeline's
own `AnthropicLlm.extract` (via the same capture/replay stand-in client):

    s55t  Sonnet 5.5 on the whole extracted text (the T1 prompt, all pages)
    s55v  Sonnet 5.5 on page images 1-4 (exactly today's T2)
    h55v  Haiku 5.5 on page images 1-4 (the T2 prompt, cheap model)

Scans (no text layer) get s55v and h55v. `report` assembles every routing chain
from those measured calls: cost, how often the expensive call is avoided, and
accuracy where labels exist. Writes only under eval/ladder-probe/.

    python -m tools.ladder_probe plan   --from eval/haiku-compare/<run>
    python -m tools.ladder_probe run    --run DIR [--cap-usd 10]
    python -m tools.ladder_probe report --run DIR
    python -m tools.ladder_probe gate   --run DIR   # simplified GATE per chain, no API
    python -m tools.ladder_probe extend --run DIR --arm h55vn [--text]   # an arm added after plan
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib
import random
import re
import statistics
import time
from types import SimpleNamespace

import pymupdf

from tools import haiku_compare as hc

from app.extract import llm as llm_mod  # noqa: E402
from app.extract import pdf as pdfutil  # noqa: E402
from app.extract import t0_templates, tiers  # noqa: E402
from app.extract.t0_templates import _DATE_RE, _parse_date, t0_extract  # noqa: E402
from app.extract.udi import INVALID, validate_basic_udi  # noqa: E402
from app.handlers.extract import _hints_for  # noqa: E402

REPO = hc.REPO
OUT_ROOT = REPO / "eval" / "ladder-probe"
T1_ARM = "h55"     # the T1 read the chains start from

PROBE_ARMS = {
    "s55t": (hc.Arm("claude-sonnet-5-5", None, "Sonnet 5.5, whole text"), "T1"),
    "s55v": (hc.Arm("claude-sonnet-5-5", None, "Sonnet 5.5, page images 1-4 (today's T2)"), "T2"),
    "h55v": (hc.Arm("claude-haiku-5-5", None, "Haiku 5.5, page images 1-4"), "T2"),
    # second cheap image read, for an agreement rule on scans (added 2026-10-12)
    "h55vn": (hc.Arm("claude-haiku-5-5", {"type": "disabled"}, "Haiku 5.5, page images 1-4, thinking off"), "T2"),
}
IMAGE_TOKENS = 2836          # one 150-dpi page on the 5.5 models (count_tokens, 2026-10-08)
EST_OUT = {"claude-sonnet-5-5": 3000, "claude-haiku-5-5": 2000}
HAIKU_IMAGE_SETTLE = 0.85    # scans: a Haiku image read settles at this confidence or above

# --------------------------------------------------------------------------- #
# Evidence checks (the rule proposed in docs/private/2026-10-08-extraction-ladder-research.md)
# --------------------------------------------------------------------------- #
LABELS = {
    "validity_to": r"valid until|valid to|expir|gültig bis|gueltig bis|scadenza|valido fino|velja do|vrijedi do|valable jusqu",
    "validity_from": r"date|datum|issued|gültig ab|valid from|data|izdan",
    "cert_number": r"certific|zertifikat|certifikat|certificato|registration no",
    "basic_udi_di": r"udi",
    "ref_list": r"\bref\b|article|artikel|art\.|item|catalog|katalog|šifra|codice",
    "regulation": r"2017/745|93/42|regulation|richtlinie|directive|uredb|direktiv",
    "manufacturer": r"manufactur|hersteller|fabbricante|proizvajalec|proizvođač|fabricant",
}
LITERAL = {"validity_from", "validity_to", "regulation", "ref_list", "basic_udi_di",
           "cert_number", "manufacturer"}


def compact(s) -> str:
    return "".join(str(s).split()).casefold()


def dates_in(text: str) -> set[str]:
    out = {d for m in _DATE_RE.finditer(text) if (d := _parse_date(m.group(0)))}
    return out | set(re.findall(r"\d{4}-\d{2}-\d{2}", text))


def passes(f: str, ev: dict, pages: list[str], full_c: str) -> bool:
    """A non-null value proven present in the document's text layer."""
    v = ev.get("value")
    if hc.norm(f, v) is None:
        return False
    grounded = hc._verbatim_ok(ev.get("verbatim", ""), ev.get("page"), pages)
    if f in ("validity_from", "validity_to"):
        return grounded and v in dates_in(ev.get("verbatim", ""))
    if f == "regulation":
        mdr, mdd = "2017/745" in full_c, "93/42" in full_c
        return {"MDR": mdr, "MDD": mdd, "n.a.": not (mdr or mdd)}.get(v, False)
    if f == "ref_list":
        codes = [compact(c) for c in v if str(c).strip()]
        return bool(codes) and sum(c in full_c for c in codes) >= 0.95 * len(codes)
    if f == "basic_udi_di":
        return compact(v) in full_c and validate_basic_udi(v).status != INVALID
    if f in ("cert_number", "manufacturer"):
        return compact(v) in full_c
    return grounded


def format_ok(f: str, v) -> bool:
    """Shape checks that need no text layer (for reads off a scan)."""
    if hc.norm(f, v) is None:
        return False
    if f in ("validity_from", "validity_to"):
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)))
    if f == "basic_udi_di":
        return validate_basic_udi(v).status != INVALID
    if f == "regulation":
        return v in ("MDR", "MDD", "n.a.")
    return True


def has_label(f: str, text: str) -> bool:
    pat = LABELS.get(f)
    return True if pat is None else re.search(pat, text, re.IGNORECASE) is not None


def _coverage(page) -> float:
    area = page.rect.width * page.rect.height or 1
    total = 0.0
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        if not r.is_empty:
            total += r.width * r.height
    return min(total / area, 1.0)


def page_classes(doc) -> list[str]:
    out = []
    for page in doc:
        chars = len(page.get_text("text").strip())
        cov = _coverage(page)
        out.append("text" if chars >= 200 else "scanned" if chars < 20 and cov >= 0.5
                   else "sparse-img" if cov >= 0.3 else "sparse")
    return out


def open_after_t1(rec: dict, pages: list[str], image_pages: bool) -> tuple[dict, list[str]]:
    """Fields still open after T0 + the T1 read under the evidence rule (label
    policy for nulls). Returns (fields, open field list)."""
    full = "\n".join(pages)
    full_c = compact(full)
    t0 = rec["doc"]["t0"]
    fields = {}
    for f in hc.FIELDS:
        e0, e1 = t0.get(f), rec["raw"].get(f)
        if isinstance(e0, dict) and e0.get("conf", 0) >= 0.95:
            fields[f] = e0
        elif e1 and passes(f, e1, pages, full_c):
            fields[f] = dict(e1, conf=1.0)
        elif (e1 and hc.norm(f, e1.get("value")) is None and not has_label(f, full)
              and not image_pages):
            fields[f] = dict(e1, conf=1.0)
        elif e1:
            fields[f] = dict(e1, conf=0.0)
        elif isinstance(e0, dict):
            fields[f] = dict(e0, conf=0.0)
    return fields, tiers.needs_escalation(fields)


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def _probe_llm(arm_id: str, mode: str):
    arm, _ = PROBE_ARMS[arm_id]
    return llm_mod.AnthropicLlm(hc._ShimClient(arm, mode), SimpleNamespace(t1=arm.model, t2=arm.model))


def _hints(pages: list[str]):
    steering = t0_templates.steering_playbook("\n".join(pages))
    return _hints_for(steering, tiers.needs_escalation({}), authoritative=False)


def _capture_reads(h: str, path: str, doc, asked: list[str], hints, arms) -> list[dict]:
    reqs = []
    kwargs = {"hints": hints} if hints is not None else {}
    for arm_id in arms:
        tier = PROBE_ARMS[arm_id][1]
        llm = _probe_llm(arm_id, "capture")
        params = hc._capture(lambda: llm.extract(tier, doc, path, asked, **kwargs))
        blocks = [b for m in params["messages"] for b in m["content"]]
        n_img = sum(b.get("type") == "image" for b in blocks)
        # Text characters only: base64 page images would swamp a chars/token estimate.
        chars = (len(json.dumps(params["system"], ensure_ascii=False))
                 + len(json.dumps(params["output_config"], ensure_ascii=False))
                 + sum(len(b.get("text", "")) for b in blocks if b.get("type") == "text"))
        reqs.append({"content_hash": h, "arm": arm_id, "kind": "extract", "key": hc.request_key(params),
                     "chars": chars, "images": n_img, "params": params})
    return reqs


def cmd_plan(args) -> int:
    src = pathlib.Path(args.src)
    run = OUT_ROOT / datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    run.mkdir(parents=True)
    rows = [r for r in hc._evaluate(src) if r["arm"] == T1_ARM]
    text_docs, reqs = [], []
    for r in rows:
        d = r["doc"]
        path = str(REPO / d["path"])
        with pdfutil.open_doc(path) as doc:
            pages = pdfutil.page_texts(doc)
            image_pages = any(c in ("scanned", "sparse-img") for c in page_classes(doc))
            _, open_f = open_after_t1(r, pages, image_pages)
            if not open_f:
                continue
            text_docs.append({"content_hash": d["content_hash"], "path": d["path"], "gt": d["gt"],
                              "open": open_f, "image_pages": image_pages, "hinted": d["hinted"]})
            reqs += _capture_reads(d["content_hash"], path, doc, open_f, _hints(pages) if d["hinted"] else None,
                                   ("s55t", "s55v", "h55v"))
    # scans: the labelled ones plus a stratified sample of the SFTP mirror
    seen, scans, pool = set(), [], collections.defaultdict(list)
    fixtures = [(f["path"], f["manu"], f["ground_truth"]) for f in hc._fixtures()]
    sftp = [(p, p.relative_to(hc.SFTP_ROOT).parts[0], None)
            for p in sorted(hc.SFTP_ROOT.rglob("*")) if p.suffix.lower() == ".pdf"]
    for path, manu, gt in fixtures + sftp:
        h = hc._sha256(path)
        if h in seen:
            continue
        seen.add(h)
        try:
            with pdfutil.open_doc(str(path)) as doc:
                classes = page_classes(doc)
        except Exception:
            continue
        if classes and all(c in ("scanned", "sparse") for c in classes):
            item = {"content_hash": h, "path": str(path.relative_to(REPO)), "manu": manu, "gt": gt}
            (scans if gt is not None else pool[manu]).append(item)
    rng = random.Random(args.seed)
    for manu in sorted(pool):
        scans += rng.sample(pool[manu], min(args.scans_per_manu, len(pool[manu])))
    for s in scans:
        path = str(REPO / s["path"])
        with pdfutil.open_doc(path) as doc:
            doc_class, fields = t0_extract(doc, path)
            s["t0_class"] = doc_class
            s["t0"] = fields
            s["open"] = tiers.needs_escalation(fields)
            reqs += _capture_reads(s["content_hash"], path, doc, s["open"], None, ("s55v", "h55v"))
    (run / "plan.json").write_text(json.dumps({"src": str(src), "text_docs": text_docs, "scans": scans},
                                              ensure_ascii=False, indent=1, default=str))
    with open(run / "requests.jsonl", "w") as fh:
        for r in reqs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"run dir: {run.relative_to(REPO)}")
    print(f"text docs with open fields: {len(text_docs)}; scans: {len(scans)} "
          f"({sum(s['gt'] is not None for s in scans)} labelled)")
    print(f"requests: {dict(collections.Counter(r['arm'] for r in reqs))}")
    return 0


def cmd_extend(args) -> int:
    """Capture requests for a probe arm added after `plan`, for the planned scans
    (and text documents with --text), asking the same open fields."""
    run = pathlib.Path(args.run)
    plan = json.loads((run / "plan.json").read_text())
    have = {json.loads(line)["key"] for line in open(run / "requests.jsonl")}
    docs = list(plan["scans"]) + (list(plan["text_docs"]) if args.text else [])
    added = 0
    with open(run / "requests.jsonl", "a") as fh:
        for d in docs:
            if not d.get("open"):
                continue
            path = str(REPO / d["path"])
            with pdfutil.open_doc(path) as doc:
                hints = _hints(pdfutil.page_texts(doc)) if d.get("hinted") else None
                for r in _capture_reads(d["content_hash"], path, doc, d["open"], hints, (args.arm,)):
                    if r["key"] not in have:
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                        have.add(r["key"])
                        added += 1
    print(f"requests added for {args.arm}: {added}")
    return 0


# --------------------------------------------------------------------------- #
# run (Batch API, cached, budget guard)
# --------------------------------------------------------------------------- #
def _estimate(reqs) -> float:
    total = collections.Counter()
    for r in reqs:
        m = r["params"]["model"]
        text_chars = r["chars"]
        tin = int(text_chars / hc.DEFAULT_CPT["claude-haiku-5-5"]) + r["images"] * IMAGE_TOKENS
        pin, pout = hc._price(m, "batch", tin)
        total[r["arm"]] += (tin * pin + EST_OUT[m] * pout) / 1e6
    for a, v in sorted(total.items()):
        print(f"  {a:5} ~${v:.3f}")
    print(f"  total ~${sum(total.values()):.3f}")
    return sum(total.values())


def cmd_run(args) -> int:
    run = pathlib.Path(args.run)
    reqs = [json.loads(line) for line in open(run / "requests.jsonl")]
    state_path = run / "batches.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    client = hc._client()
    submitted = {k for ids in state.values() for k in ids.values()}
    todo = [r for r in reqs if hc.cached(r["key"]) is None and r["key"] not in submitted]
    print(f"{len(todo)} requests neither cached nor submitted")
    if todo:
        print("estimate (batch):")
        if _estimate(todo) > args.cap_usd:
            print(f"STOP: estimate exceeds cap ${args.cap_usd}")
            return 2
        by_arm = collections.defaultdict(list)
        for r in todo:
            by_arm[r["arm"]].append(r)
        for arm, rs in by_arm.items():
            ids = {f"p_{arm}_{r['content_hash'][:40]}": r["key"] for r in rs}
            b = client.messages.batches.create(requests=[
                {"custom_id": cid, "params": r["params"]} for cid, r in zip(ids, rs)])
            state[b.id] = ids
            state_path.write_text(json.dumps(state, indent=1))
            print(f"  submitted {arm}: {b.id} ({len(rs)})", flush=True)
    by_key = {r["key"]: r for r in reqs}
    pending = {bid for bid, ids in state.items() if any(hc.cached(k) is None for k in ids.values())}
    errors = []
    while pending:
        for bid in sorted(pending):
            b = client.messages.batches.retrieve(bid)
            if b.processing_status != "ended":
                continue
            for item in client.messages.batches.results(bid):
                req = by_key[state[bid][item.custom_id]]
                if item.result.type == "succeeded":
                    hc._store_with_chars(req, item.result.message.to_dict(), "batch")
                else:
                    errors.append({"custom_id": item.custom_id, "type": item.result.type})
            pending.discard(bid)
            print(f"  ended {bid}: {b.request_counts}", flush=True)
        if pending:
            time.sleep(30)
    (run / "batch_errors.json").write_text(json.dumps(errors, indent=1))
    print(f"done; {len(errors)} non-succeeded")
    return 0


# --------------------------------------------------------------------------- #
# report: assemble the chains from measured calls
# --------------------------------------------------------------------------- #
def _read(run_reqs, h, arm, doc, path, asked, hints):
    """Replay one cached read through the pipeline's own extract()."""
    key = run_reqs[(h, arm)]["key"]
    entry = hc.cached(key)
    if entry is None:
        return None
    tier = PROBE_ARMS[arm][1]
    kwargs = {"hints": hints} if hints is not None else {}
    answer = _probe_llm(arm, "replay").extract(tier, doc, path, asked, **kwargs)
    msg = entry["message"]
    return {"answer": answer, "cost": hc._cost(entry["model"], msg["usage"], "batch"),
            "stop": msg["stop_reason"], "out": msg["usage"]["output_tokens"]}


def _chain(steps, reads, open_f, settle) -> dict:
    """Walk a chain of reads over the open fields. `settle(arm, f, ev)` says
    whether a read closes a field; the last step always closes (stronger
    read prevails). Returns cost, calls, final values."""
    remaining, final, cost, calls = list(open_f), {}, 0.0, []
    for i, arm in enumerate(steps):
        if not remaining:
            break
        r = reads[arm]
        cost += r["cost"]
        calls.append(arm)
        last = i == len(steps) - 1
        for f in list(remaining):
            ev = r["answer"].get(f)
            if last or (ev and settle(arm, f, ev)):
                final[f] = ev
                remaining.remove(f)
    return {"cost": cost, "calls": calls, "final": final}


TEXT_CHAINS = {
    "T-1 T1 text -> Sonnet image (today's shape)": ("s55v",),
    "T-2 T1 text -> Sonnet text -> Sonnet image": ("s55t", "s55v"),
    "T-3 T1 text -> Haiku image -> Sonnet image": ("h55v", "s55v"),
    "T-4 T1 text -> Haiku image -> Sonnet text -> Sonnet image": ("h55v", "s55t", "s55v"),
    "T-5 T1 text -> Sonnet text -> Haiku image -> Sonnet image": ("s55t", "h55v", "s55v"),
}
SCAN_CHAINS = {
    "S-1 skip T1 -> Sonnet image": ("s55v",),
    "S-2 skip T1 -> Haiku image -> Sonnet image": ("h55v", "s55v"),
}


def cmd_report(args) -> int:
    run = pathlib.Path(args.run)
    plan = json.loads((run / "plan.json").read_text())
    for d in plan["text_docs"] + plan["scans"]:
        d["gt"] = hc.corrected_gt(d["path"], d["gt"])
    reqs = {}
    for line in open(run / "requests.jsonl"):
        r = json.loads(line)
        reqs[(r["content_hash"], r["arm"])] = r
    L = ["# Ladder routing probe", "", f"Run `{run.name}`, T1 = `{T1_ARM}` answers from `{plan['src']}`. "
         "Costs are measured batch prices per call; a chain's later calls are priced as if asked every open "
         "field (an upper bound: in the pipeline they would be asked only what is still open).", ""]

    # ---- text documents
    res = collections.defaultdict(list)
    for d in plan["text_docs"]:
        h, path = d["content_hash"], str(REPO / d["path"])
        with pdfutil.open_doc(path) as doc:
            pages = pdfutil.page_texts(doc)
            full_c = compact("\n".join(pages))
            hints = _hints(pages) if d["hinted"] else None
            reads = {a: _read(reqs, h, a, doc, path, d["open"], hints) for a in ("s55t", "s55v", "h55v")}
        if any(v is None for v in reads.values()):
            continue

        def settle(arm, f, ev, pages=pages, full_c=full_c):
            if f in LITERAL:
                return passes(f, ev, pages, full_c)
            return hc.norm(f, ev.get("value")) is not None

        for name, steps in TEXT_CHAINS.items():
            c = _chain(steps, reads, d["open"], settle)
            c["doc"] = d
            res[name].append(c)
    n = len(next(iter(res.values()), []))
    L += [f"## Text documents with a field open after T1 ({n})", "",
          "| chain | $ per doc | Sonnet image calls | Sonnet text calls | Haiku image calls | "
          "final values = T-1's | correct / scored (labelled) |", "|---|---|---|---|---|---|---|"]
    ref = {c["doc"]["content_hash"]: c["final"] for c in res[next(iter(TEXT_CHAINS))]}
    for name, cs in res.items():
        calls = collections.Counter(a for c in cs for a in c["calls"])
        same = tot = ok = scored = 0
        for c in cs:
            for f, ev in c["final"].items():
                tot += 1
                rv = (ref[c["doc"]["content_hash"]].get(f) or {}).get("value")
                same += hc.norm(f, (ev or {}).get("value")) == hc.norm(f, rv)
                if c["doc"]["gt"]:
                    k = hc._correct(f, (ev or {}).get("value"), hc._gt_value(f, c["doc"]["gt"]))
                    if k is not None:
                        scored += 1
                        ok += k
        L.append(f"| {name} | {statistics.mean(c['cost'] for c in cs):.5f} | {calls['s55v']} | "
                 f"{calls['s55t']} | {calls['h55v']} | {hc._pct(same, tot)} | {hc._pct(ok, scored)} |")
    L.append("")

    # ---- scans
    sres = collections.defaultdict(list)
    for s in plan["scans"]:
        h, path = s["content_hash"], str(REPO / s["path"])
        if not s["open"]:
            continue
        with pdfutil.open_doc(path) as doc:
            reads = {a: _read(reqs, h, a, doc, path, s["open"], None) for a in ("s55v", "h55v")}
        if any(v is None for v in reads.values()):
            continue

        def settle(arm, f, ev):
            return format_ok(f, ev.get("value")) and ev.get("conf", 0) >= HAIKU_IMAGE_SETTLE

        for name, steps in SCAN_CHAINS.items():
            c = _chain(steps, reads, s["open"], settle)
            c["doc"] = s
            sres[name].append(c)
    m = len(next(iter(sres.values()), []))
    dropped = sum(s["t0_class"] == "unknown" for s in plan["scans"])
    L += [f"## Scans ({m}; T0 left {dropped} as `unknown`)", "",
          f"Haiku image read settles a field when the value is well-formed and its confidence >= {HAIKU_IMAGE_SETTLE}.", "",
          "| chain | $ per doc | Sonnet image calls | Haiku image calls | final values = Sonnet's | correct / scored |",
          "|---|---|---|---|---|---|"]
    sref = {c["doc"]["content_hash"]: c["final"] for c in sres[next(iter(SCAN_CHAINS))]}
    for name, cs in sres.items():
        calls = collections.Counter(a for c in cs for a in c["calls"])
        same = tot = ok = scored = 0
        for c in cs:
            for f, ev in c["final"].items():
                tot += 1
                rv = (sref[c["doc"]["content_hash"]].get(f) or {}).get("value")
                same += hc.norm(f, (ev or {}).get("value")) == hc.norm(f, rv)
                if c["doc"]["gt"]:
                    k = hc._correct(f, (ev or {}).get("value"), hc._gt_value(f, c["doc"]["gt"]))
                    if k is not None:
                        scored += 1
                        ok += k
        L.append(f"| {name} | {statistics.mean(c['cost'] for c in cs):.5f} | {calls['s55v']} | "
                 f"{calls['h55v']} | {hc._pct(same, tot)} | {hc._pct(ok, scored)} |")
    L.append("")

    # ---- per-read health
    L += ["## Per read", "", "| read | calls | median $ | median output tokens | stop reasons |", "|---|---|---|---|---|"]
    for arm in PROBE_ARMS:
        es = [hc.cached(r["key"]) for (h, a), r in reqs.items() if a == arm]
        es = [e for e in es if e]
        if es:
            L.append(f"| {arm} | {len(es)} | {statistics.median(hc._cost(e['model'], e['message']['usage'], 'batch') for e in es):.5f} | "
                     f"{statistics.median(e['message']['usage']['output_tokens'] for e in es):.0f} | "
                     f"{dict(collections.Counter(e['message']['stop_reason'] for e in es))} |")
    (run / "report.md").write_text("\n".join(L))
    print("\n".join(L))
    return 0


# --------------------------------------------------------------------------- #
# gate: what each chain hands GATE, plus threshold sensitivity
# --------------------------------------------------------------------------- #
GATE_HIGH, GATE_MED = 0.92, 0.75        # config.GateThresholds (live)
FACTORS = (1.0, 1.05, 1.08, 1.12)       # candidate calibration factors for Haiku 5.5 evidence


def _disposition(fields: dict, factor: float = 1.0, proven: frozenset = frozenset()) -> str:
    """Simplified GATE: min confidence over gate.REQUIRED_FIELDS (REF gate and
    VALIDATE flags not simulated). `factor` scales Haiku 5.5 evidence only;
    fields in `proven` count as confident (passed the literal checks)."""
    confs = []
    for f in hc.GATE_REQUIRED:
        ev = fields.get(f) or {}
        if hc.norm(f, ev.get("value")) is None:
            return "manual"
        c = float(ev.get("conf", 0.0))
        if f in proven:
            c = 1.0
        elif ev.get("model_id") == "claude-haiku-5-5":
            c = min(1.0, c * factor)
        confs.append(c)
    s = min(confs)
    return "production" if s >= GATE_HIGH else "staged" if s >= GATE_MED else "manual"


def _wrong(fields: dict, gt: dict | None) -> bool | None:
    if not gt:
        return None
    return any(hc._correct(f, (fields.get(f) or {}).get("value"), hc._gt_value(f, gt)) is False
               for f in hc.FIELDS)


def _t1_full(r: dict) -> dict:
    """T0 where it is confident, otherwise T1's full answer (the reads pool)."""
    out = {}
    for f in hc.FIELDS:
        e0, e1 = r["doc"]["t0"].get(f), r["raw"].get(f)
        if isinstance(e0, dict) and e0.get("conf", 0) >= 0.95:
            out[f] = e0
        elif e1:
            out[f] = e1
        elif isinstance(e0, dict):
            out[f] = e0
    return out


def _tally(name, items, L, factor=1.0, use_proven=False):
    disp, wrong, n = collections.Counter(), 0, 0
    for fields, gt, proven in items:
        d = _disposition(fields, factor, proven if use_proven else frozenset())
        disp[d] += 1
        n += 1
        if d == "production" and _wrong(fields, gt):
            wrong += 1
    lab = sum(1 for _, gt, _ in items if gt)
    L.append(f"| {name} | {hc._pct(disp['production'], n)} | {disp['staged']} | {disp['manual']} | "
             f"{wrong} of {lab} labelled |")


def cmd_gate(args) -> int:
    run = pathlib.Path(args.run)
    plan = json.loads((run / "plan.json").read_text())
    for d in plan["text_docs"] + plan["scans"]:
        d["gt"] = hc.corrected_gt(d["path"], d["gt"])
    reqs = {}
    for line in open(run / "requests.jsonl"):
        r = json.loads(line)
        reqs[(r["content_hash"], r["arm"])] = r
    t1 = collections.defaultdict(dict)
    for r in hc._evaluate(pathlib.Path(plan["src"])):
        t1[r["doc"]["content_hash"]][r["arm"]] = r
    probe = {d["content_hash"]: d for d in plan["text_docs"]}
    L = ["# Gate and threshold evaluation", "",
         f"Run `{run.name}`. Labels corrected per `haiku_compare.LABEL_OVERRIDES`. Gate simulated at "
         f"{GATE_HIGH}/{GATE_MED} on min confidence over type, regulation, coverage_scope; REF gate and "
         "VALIDATE flags not simulated, so these are upper bounds on production.", ""]

    # ---- text documents: what reaches GATE per chain
    base, chosen, sem = [], {t: [] for t in (0.0, 0.80, 0.85, 0.90)}, {}
    for h, arms in t1.items():
        r45, r55 = arms["h45"], arms["h55"]
        gt = r55["doc"]["gt"]
        base.append((r45["merged"], gt, frozenset()))
        fields = _t1_full(r55)
        path = str(REPO / r55["doc"]["path"])
        with pdfutil.open_doc(path) as doc:
            pages = pdfutil.page_texts(doc)
            full_c = compact("\n".join(pages))
            proven = frozenset(f for f in hc.GATE_REQUIRED if (fields.get(f) or {}).get("tier") in ("T1", "T2")
                               and f in LITERAL and passes(f, fields[f], pages, full_c))
            reads = None
            if h in probe:
                d = probe[h]
                hints = _hints(pages) if d["hinted"] else None
                reads = {a: _read(reqs, h, a, doc, path, d["open"], hints) for a in ("s55v", "h55v")}
        for thr in chosen:
            f2 = dict(fields)
            if reads and all(reads.values()):
                def settle(arm, f, ev, thr=thr):
                    if f in LITERAL:
                        return passes(f, ev, pages, full_c)
                    return hc.norm(f, ev.get("value")) is not None and ev.get("conf", 0) >= thr
                c = _chain(("h55v", "s55v"), reads, probe[h]["open"], settle)
                f2 = tiers.merge(f2, {k: v for k, v in c["final"].items() if v})
                sem.setdefault(thr, []).append(c)
            chosen[thr].append((f2, gt, proven))
    L += [f"## Text documents ({len(base)}): what GATE would receive", "",
          "| ladder | production | staged | manual | wrong in production |", "|---|---|---|---|---|"]
    _tally("Today, T0 + Haiku 4.5 only (no T2 read)", base, L)
    for fac in FACTORS:
        _tally(f"Chosen: Haiku 5.5 + checks + T-3, Haiku factor {fac}", chosen[0.0], L, fac)
    _tally("Chosen, factor 1.0, check-proven `regulation` counts as confident", chosen[0.0], L, 1.0, True)
    _tally("Chosen, factor 1.08 + check-proven `regulation`", chosen[0.0], L, 1.08, True)
    L.append("")

    # ---- text: semantic settle threshold for the Haiku image read
    L += ["## Text documents, Haiku image read: confidence a semantic field needs to settle", "",
          "Literal fields settle on the evidence checks either way; this varies only type / coverage_scope / "
          "stated_class / referenced_docs.", "",
          "| threshold | $ / open doc | Sonnet image calls | wrong in production |", "|---|---|---|---|"]
    for thr, cs in sem.items():
        items = chosen[thr]
        wrong = sum(1 for fields, gt, pr in items if _disposition(fields) == "production" and _wrong(fields, gt))
        L.append(f"| {thr or 'any non-null'} | {statistics.mean(c['cost'] for c in cs):.5f} | "
                 f"{sum('s55v' in c['calls'] for c in cs)} / {len(cs)} | {wrong} |")
    L.append("")

    # ---- scans
    rows_by_thr = collections.defaultdict(list)
    s1, s1_cost = [], []
    for s in plan["scans"]:
        if not s["open"]:
            continue
        path = str(REPO / s["path"])
        with pdfutil.open_doc(path) as doc:
            reads = {a: _read(reqs, s["content_hash"], a, doc, path, s["open"], None) for a in ("s55v", "h55v")}
        if not all(reads.values()):
            continue
        t0 = {k: v for k, v in s["t0"].items() if isinstance(v, dict)}
        c1 = _chain(("s55v",), reads, s["open"], lambda a, f, ev: False)
        f1 = tiers.merge(t0, {k: v for k, v in c1["final"].items() if v})
        s1.append((f1, s["gt"], frozenset()))
        s1_cost.append(c1["cost"])
        for thr in (0.80, 0.85, 0.90, 0.95):
            c2 = _chain(("h55v", "s55v"), reads, s["open"],
                        lambda a, f, ev, thr=thr: format_ok(f, ev.get("value")) and ev.get("conf", 0) >= thr)
            f2 = tiers.merge(t0, {k: v for k, v in c2["final"].items() if v})
            same = sum(hc.norm(f, (c2["final"].get(f) or {}).get("value")) == hc.norm(f, (c1["final"].get(f) or {}).get("value"))
                       for f in s["open"])
            rows_by_thr[thr].append((c2, f2, s["gt"], same, len(s["open"])))
    L += [f"## Scans ({len(s1)}): Haiku image settle threshold", "",
          "| chain | $ / scan | Sonnet image calls | same answers as Sonnet only | correct (scored) | production | wrong in production |",
          "|---|---|---|---|---|---|---|"]

    def score(items):
        ok = sc = 0
        for fields, gt in items:
            if not gt:
                continue
            for f in hc.FIELDS:
                k = hc._correct(f, (fields.get(f) or {}).get("value"), hc._gt_value(f, gt))
                if k is not None:
                    sc += 1
                    ok += k
        return f"{ok} / {sc}"
    prod1 = sum(_disposition(f) == "production" for f, _, _ in s1)
    w1 = sum(1 for f, gt, _ in s1 if _disposition(f) == "production" and _wrong(f, gt))
    L.append(f"| S-1 Sonnet image only | {statistics.mean(s1_cost):.5f} | "
             f"{len(s1)} / {len(s1)} | 100% | {score([(f, g) for f, g, _ in s1])} | {prod1} | {w1} |")
    for thr, rows in rows_by_thr.items():
        cost = statistics.mean(c["cost"] for c, *_ in rows)
        calls = sum("s55v" in c["calls"] for c, *_ in rows)
        same = sum(x[3] for x in rows)
        tot = sum(x[4] for x in rows)
        prod = sum(_disposition(f2) == "production" for _, f2, *_ in rows)
        w = sum(1 for _, f2, gt, *_ in rows if _disposition(f2) == "production" and _wrong(f2, gt))
        L.append(f"| S-2 at {thr} | {cost:.5f} | {calls} / {len(rows)} | {hc._pct(same, tot)} | "
                 f"{score([(f2, gt) for _, f2, gt, *_ in rows])} | {prod} | {w} |")
    (run / "gate.md").write_text("\n".join(L))
    print("\n".join(L))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.ladder_probe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--from", dest="src", required=True)
    p.add_argument("--scans-per-manu", type=int, default=4)
    p.add_argument("--seed", type=int, default=20261008)
    p = sub.add_parser("run")
    p.add_argument("--run", required=True)
    p.add_argument("--cap-usd", type=float, default=10.0)
    p = sub.add_parser("extend")
    p.add_argument("--run", required=True)
    p.add_argument("--arm", required=True, choices=sorted(PROBE_ARMS))
    p.add_argument("--text", action="store_true", help="also the planned text documents")
    p = sub.add_parser("report")
    p.add_argument("--run", required=True)
    p = sub.add_parser("gate")
    p.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    return {"plan": cmd_plan, "extend": cmd_extend, "run": cmd_run, "report": cmd_report,
            "gate": cmd_gate}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
