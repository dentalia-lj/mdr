"""Haiku 4.5 vs Haiku 5.5 on the T1 text tier. An evaluation, not a pipeline change.

Sends the requests the pipeline itself would send. Nothing here builds a prompt:
`AnthropicLlm.extract` / `.classify` run unmodified against a stand-in client
(`_ShimClient`) that captures the request on the way out and replays a cached
response on the way back, so the pipeline's own `_params`, `_response_json`,
`_fields_from_json` and classify fallback do all the work. The only thing an arm
changes is the model id and, for `h55n`, `thinking: disabled`.

Read-only: no database, no registry, no fetch_log. T0 and the playbook hints are
computed locally from `playbooks/*.json`. Everything is written under
eval/haiku-compare/.

    python -m tools.haiku_compare plan
    python -m tools.haiku_compare dry-run --run DIR [--n 5]
    python -m tools.haiku_compare batch   --run DIR [--cap-usd 10] [--allow-over-100k]
    python -m tools.haiku_compare report  --run DIR
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import csv
import datetime
import hashlib
import json
import os
import pathlib
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass
from types import SimpleNamespace

# --------------------------------------------------------------------------- #
# Prices, USD per MTok. Checked 2026-10-08 against
# platform.claude.com/docs/en/about-claude/pricing. Haiku 5.5 is priced by
# prompt length: a prompt over 100,000 tokens pays the second row.
# --------------------------------------------------------------------------- #
PRICES = {
    "claude-haiku-4-5": {"batch": (0.50, 2.50), "sync": (1.00, 5.00)},
    "claude-haiku-5-5": {"batch": (0.05, 0.25), "sync": (0.10, 0.50),
                         "batch_over": (0.25, 1.25), "sync_over": (0.50, 2.50)},
    "claude-sonnet-5-5": {"batch": (1.00, 5.00), "sync": (2.00, 10.00)},   # T2 today
}
LONG_PROMPT_TOKENS = 100_000
CACHE_READ_MULT, CACHE_WRITE_MULT = 0.1, 1.25


@dataclass(frozen=True)
class Arm:
    model: str
    thinking: dict | None   # None = whatever the API defaults to
    label: str
    effort: str | None = None   # None = the API default (medium on Haiku 5.5)
    prompt: str | None = None   # a key of PROMPT_VARIANTS; None = the pipeline's prompt as is


#: In-memory edits of the T1 prompt for an arm. The prompt file is never changed;
#: each edit must match exactly once or the capture fails, unless the prompt
#: already carries the edit, which then applies as is.
PROMPT_VARIANTS = {
    # Denis, 2026-10-12 (docs/decisions.md): a DoC's cert_number holds the
    # certificate it cites, in every tier. The pipeline prompt carries this
    # line since extraction-ladder Task 4, so the edit is a no-op now and the
    # h55a arm sends the default prompt: the same system text it was cached
    # under, so its request keys still find the cached responses.
    "ruling-a": [(
        "- `cert_number` — the certificate number (EC / ISO certs), or `null` (DoCs\n"
        "  themselves have none).",
        "- `cert_number` — for an EC / ISO certificate, its own number. For a DoC, the\n"
        "  number of the notified-body certificate the declaration cites (e.g.\n"
        "  `G1 021108 0031`), or `null` if it cites none. Never a notified body's\n"
        "  four-digit identification number (e.g. `0482`).",
    )],
}
#: The reverse edit: the T1 prompt as it stood before ruling A. The baseline
#: arms (h45, h55, h55n, h55hi) were measured and cached with it, so they put
#: the old line back; without it their requests miss the cache and a fresh
#: capture would measure "today's T1" on a prompt it never ran with.
PROMPT_VARIANTS["pre-ruling-a"] = [(new, old) for old, new in PROMPT_VARIANTS["ruling-a"]]


ARMS = {
    "h45": Arm("claude-haiku-4-5", None, "Haiku 4.5 (the T1 before 2026-10-12)",
               prompt="pre-ruling-a"),
    "h55": Arm("claude-haiku-5-5", None, "Haiku 5.5, model swap only (adaptive thinking, effort medium)",
               prompt="pre-ruling-a"),
    "h55n": Arm("claude-haiku-5-5", {"type": "disabled"}, "Haiku 5.5, thinking disabled",
                prompt="pre-ruling-a"),
    "h55hi": Arm("claude-haiku-5-5", None, "Haiku 5.5, adaptive thinking, effort high", "high",
                 prompt="pre-ruling-a"),
    "h55a": Arm("claude-haiku-5-5", None, "Haiku 5.5, T1 prompt with the DoC certificate ruling",
                prompt="ruling-a"),
}
BASE = "h45"
CHALLENGERS = ("h55", "h55n", "h55hi")
SUFFIX = {"h45": "45", "h55": "55", "h55n": "55n", "h55hi": "55hi", "h55a": "55a"}

# Fallback chars/token until a dry run measures them. 2.44 is the Slovene figure
# measured on Haiku 4.5's tokenizer (pdf.LLM_TEXT_BUDGET_CHARS); Haiku 5.5's
# tokenizer is documented at ~1.3x the tokens for the same text.
DEFAULT_CPT = {"claude-haiku-4-5": 2.44, "claude-haiku-5-5": 2.44 / 1.3}
DEFAULT_OUT = {("extract", "h45"): 1500, ("extract", "h55"): 4000, ("extract", "h55n"): 1500,
               ("extract", "h55hi"): 8192, ("extract", "h55a"): 4000, ("classify", "h55a"): 256,
               ("classify", "h45"): 100, ("classify", "h55"): 256, ("classify", "h55n"): 100,
               ("classify", "h55hi"): 256}

REPO = pathlib.Path(__file__).resolve().parent.parent
SFTP_ROOT = REPO / "imports" / "dentalia-sftp"
OUT_ROOT = REPO / "eval" / "haiku-compare"
CACHE = OUT_ROOT / "cache"

GATE_SETTINGS = {"live 0.92/0.75": (0.92, 0.75), "task 0.95/0.75": (0.95, 0.75)}
GATE_REQUIRED = ("type", "regulation", "coverage_scope")   # gate.REQUIRED_FIELDS
CONF_BUCKETS = (("conf >= 0.95", 0.95, 9.0), ("0.75 <= conf < 0.95", 0.75, 0.95),
                ("conf < 0.75", -1.0, 0.75))

sys.path.insert(0, str(REPO))

from app.extract import llm as llm_mod  # noqa: E402
from app.extract import pdf as pdfutil  # noqa: E402
from app.extract import t0_templates, tiers  # noqa: E402
from app.extract.t0_templates import t0_extract  # noqa: E402
from app.handlers.extract import _hints_for  # noqa: E402

FIELDS = list(llm_mod.SCHEMA["required"])
LIST_FIELDS = {"ref_list", "referenced_docs"}
DATE_FIELDS = {"validity_from", "validity_to"}


# --------------------------------------------------------------------------- #
# The stand-in client
# --------------------------------------------------------------------------- #
class _Captured(Exception):
    def __init__(self, params: dict):
        super().__init__("captured")
        self.params = params


def arm_params(params: dict, arm: Arm) -> dict:
    out = dict(params)
    assert out["model"] == arm.model, (out["model"], arm.model)
    if out["output_config"]["format"].get("schema") == llm_mod.DOC_CLASS_SCHEMA:
        # The pipeline's doc-class call sends thinking off since the text
        # ladder (`llm.READ_STEPS`); an arm's doc-class request carries the
        # arm's own setting, none when it names none, as it was cached.
        out.pop("thinking", None)
    if arm.thinking is not None:
        out["thinking"] = arm.thinking
    if arm.effort is not None:
        out["output_config"] = {**out["output_config"], "effort": arm.effort}
    return out


def _key_params(params: dict) -> dict:
    """The request as it was keyed before prompt caching (extraction-ladder
    Task 6, 2026-10-12). `cache_control` changes what is billed, not what the
    model reads, so it is dropped, and a system that is one block again becomes
    the plain string it was. Without this every response cached before Task 6
    misses on replay, and replay raises KeyError."""
    system = params.get("system")
    if not isinstance(system, list):
        return params
    blocks = [{k: v for k, v in b.items() if k != "cache_control"} for b in system]
    if len(blocks) == 1 and set(blocks[0]) == {"type", "text"}:
        return {**params, "system": blocks[0]["text"]}
    return {**params, "system": blocks}


def request_key(params: dict) -> str:
    return hashlib.sha256(json.dumps(_key_params(params), sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()


class _ShimClient:
    """Stands in for `anthropic.Anthropic` inside `AnthropicLlm`. In capture mode
    `messages.create` raises with the request; in replay mode it returns the
    cached response for exactly that request, or raises KeyError."""

    def __init__(self, arm: Arm, mode: str):
        self.arm, self.mode = arm, mode
        self.messages = self

    def create(self, **params):
        final = arm_params(params, self.arm)
        if self.mode == "capture":
            raise _Captured(final)
        return load_message(request_key(final))


def _llm(arm_id: str, mode: str) -> llm_mod.AnthropicLlm:
    arm = ARMS[arm_id]
    return llm_mod.AnthropicLlm(_ShimClient(arm, mode), SimpleNamespace(t1=arm.model, t2=None))


def _capture(fn) -> dict:
    try:
        fn()
    except _Captured as c:
        return c.params
    raise RuntimeError("pipeline code made no API call")


@contextlib.contextmanager
def _prompt_variant(name: str | None):
    """Swap the T1 prompt for one arm's capture, then restore it."""
    if name is None:
        yield
        return
    original = llm_mod.T1_SYSTEM
    text = original
    for old, new in PROMPT_VARIANTS[name]:
        if text.count(old) == 0 and text.count(new) == 1:
            continue          # the prompt already carries this edit
        if text.count(old) != 1:
            raise AssertionError(f"prompt variant {name}: edit does not match exactly once")
        text = text.replace(old, new)
    llm_mod.T1_SYSTEM = text
    try:
        yield
    finally:
        llm_mod.T1_SYSTEM = original


# --------------------------------------------------------------------------- #
# Response cache: one file per (content_hash, arm, kind, request)
# --------------------------------------------------------------------------- #
_INDEX: dict[str, pathlib.Path] | None = None


def _cache_path(content_hash: str, arm: str, kind: str, key: str) -> pathlib.Path:
    return CACHE / content_hash / f"{arm}-{kind}-{key[:16]}.json"


def _index() -> dict[str, pathlib.Path]:
    global _INDEX
    if _INDEX is None:
        _INDEX = {}
        for p in CACHE.glob("*/*.json"):
            _INDEX[json.loads(p.read_text())["request_key"]] = p
    return _INDEX


def cached(key: str) -> dict | None:
    p = _index().get(key)
    return json.loads(p.read_text()) if p else None


def store(req: dict, message: dict, transport: str) -> None:
    p = _cache_path(req["content_hash"], req["arm"], req["kind"], req["key"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "request_key": req["key"], "content_hash": req["content_hash"], "arm": req["arm"],
        "kind": req["kind"], "model": req["params"]["model"], "transport": transport,
        "fetched_at": datetime.datetime.now(datetime.UTC).isoformat(), "message": message,
    }, ensure_ascii=False, indent=1))
    _index()[req["key"]] = p


def load_message(key: str):
    import anthropic
    entry = cached(key)
    if entry is None:
        raise KeyError(key)
    return anthropic.types.Message.model_validate(entry["message"])


# --------------------------------------------------------------------------- #
# plan: walk, dedupe, drop scans, sample, run T0, capture every request
# --------------------------------------------------------------------------- #
def _sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _fixtures() -> list[dict]:
    sys.path.insert(0, str(REPO / "tests" / "fixtures"))
    import corpus_manifest as m
    return [dict(f, path=m.CORPUS_DIR / f["manu"] / f["name"]) for f in m.FIXTURES]


def _walk() -> tuple[list[dict], list[dict]]:
    """Every PDF in the fixtures and the SFTP mirror, deduplicated by sha256.
    Fixtures are walked first so a labelled file keeps its label."""
    kept: dict[str, dict] = {}
    excluded: list[dict] = []
    sources = [(f["path"], f["manu"], f) for f in _fixtures()]
    sources += [(p, p.relative_to(SFTP_ROOT).parts[0], None)
                for p in sorted(SFTP_ROOT.rglob("*")) if p.suffix.lower() == ".pdf"]
    for path, manu, fixture in sources:
        rel = str(path.relative_to(REPO))
        h = _sha256(path)
        if h in kept:
            excluded.append({"path": rel, "reason": "duplicate", "detail": kept[h]["path"]})
            continue
        try:
            with pdfutil.open_doc(str(path)) as doc:
                scan = pdfutil.is_scan(doc)
                chars = len(pdfutil.bounded_text(doc)[0])
        except Exception as exc:
            excluded.append({"path": rel, "reason": "unreadable", "detail": repr(exc)[:120]})
            kept[h] = {"path": rel}
            continue
        kept[h] = {"content_hash": h, "path": rel, "manu": manu, "chars": chars,
                   "gt": fixture["ground_truth"] if fixture else None}
        if scan:
            excluded.append({"path": rel, "reason": "scan (routes to T2)", "detail": ""})
            kept[h]["scan"] = True
    docs = [d for d in kept.values() if "content_hash" in d and not d.get("scan")]
    return docs, excluded


def _sample(docs: list[dict], per_manu: int, seed: int) -> list[dict]:
    chosen = [d for d in docs if d["gt"] is not None]
    by_manu = collections.defaultdict(list)
    for d in docs:
        if d["gt"] is None:
            by_manu[d["manu"]].append(d)
    rng = random.Random(seed)
    for manu in sorted(by_manu):
        pool = sorted(by_manu[manu], key=lambda d: d["path"])
        chosen += rng.sample(pool, min(per_manu, len(pool)))
    return chosen


def _prompt_chars(params: dict) -> int:
    return sum(len(json.dumps(params[k], ensure_ascii=False))
               for k in ("system", "messages", "output_config"))


def _prepare(d: dict) -> tuple[dict, list[dict]]:
    """T0 + the requests each arm would make for one document."""
    path = str(REPO / d["path"])
    with pdfutil.open_doc(path) as doc:
        doc_class, fields = t0_extract(doc, path)
        info = {**d, "doc_class": doc_class, "t0": fields, "missing": [], "hinted": False}
        if doc_class in ("business-doc", "msds"):
            return info, []
        missing = tiers.needs_escalation(fields)
        info["missing"] = missing
        if not missing:
            return info, []
        # The live handler resolves hints exactly like this for a backfilled
        # document (group_id None, so never authoritative).
        steering = t0_templates.steering_playbook("\n".join(pdfutil.page_texts(doc)))
        hints = _hints_for(steering, tiers.needs_escalation({}), authoritative=False)
        info["hinted"] = hints is not None
        kwargs = {"hints": hints} if hints is not None else {}
        reqs = []
        for arm_id in ARMS:
            llm = _llm(arm_id, "capture")
            kinds = [("extract", lambda: llm.extract("T1", doc, path, missing, **kwargs))]
            if doc_class == "unknown":
                kinds.append(("classify", lambda: llm.classify(doc, path)))
            for kind, fn in kinds:
                with _prompt_variant(ARMS[arm_id].prompt if kind == "extract" else None):
                    params = _capture(fn)
                reqs.append({"content_hash": d["content_hash"], "arm": arm_id, "kind": kind,
                             "key": request_key(params), "chars": _prompt_chars(params),
                             "params": params})
    _assert_same_input(reqs)
    return info, reqs


def _assert_same_input(reqs: list[dict]) -> None:
    """Every arm must send the same request but for model and thinking; an arm
    with a prompt variant may differ in `system` too, and in nothing else."""
    by_kind, systems = collections.defaultdict(list), collections.defaultdict(set)
    for r in reqs:
        stripped = {k: v for k, v in r["params"].items() if k not in ("model", "thinking", "system")}
        stripped["output_config"] = {k: v for k, v in stripped["output_config"].items() if k != "effort"}
        by_kind[r["kind"]].append(request_key(stripped))
        if not (r["kind"] == "extract" and ARMS[r["arm"]].prompt):
            systems[r["kind"]].add(json.dumps(r["params"]["system"], sort_keys=True))
    for kind, keys in by_kind.items():
        if len(set(keys)) != 1:
            raise AssertionError(f"arms differ beyond model/thinking/system for {kind}")
    for kind, sy in systems.items():
        if len(sy) != 1:
            raise AssertionError(f"arms without a prompt variant differ in system for {kind}")


def cmd_plan(args) -> int:
    run = OUT_ROOT / datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    (run / "raw").mkdir(parents=True)
    docs, excluded = _walk()
    sample = _sample(docs, args.per_manu, args.seed)
    plan, requests, routing = [], [], collections.Counter()
    for d in sample:
        info, reqs = _prepare(d)
        if info["doc_class"] in ("business-doc", "msds"):
            routing[f"{info['doc_class']} (no LLM)"] += 1
        elif not reqs:
            routing["T0 complete (no T1 call)"] += 1
        else:
            routing["needs T1"] += 1
        plan.append(info)
        requests += reqs
    with open(run / "requests.jsonl", "w") as fh:
        for r in requests:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (run / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1, default=str))
    _write_csv(run / "excluded.csv", excluded, ["path", "reason", "detail"])
    reasons = collections.Counter(e["reason"] for e in excluded)
    print(f"run dir: {run.relative_to(REPO)}")
    print(f"walked: {len(docs) + reasons['scan (routes to T2)'] + reasons['unreadable']} unique PDFs; "
          f"excluded {dict(reasons)}")
    print(f"text docs: {len(docs)}; sampled: {len(sample)} "
          f"({sum(d['gt'] is not None for d in sample)} labelled)")
    print(f"routing after T0: {dict(routing)}")
    print(f"requests captured: {len(requests)} "
          f"({collections.Counter((r['arm'], r['kind']) for r in requests)})")
    return 0


def cmd_extend(args) -> int:
    """Capture requests for arms added to ARMS after `plan` ran, for the same
    documents. T0 is re-run and must reproduce the planned field list."""
    run = pathlib.Path(args.run)
    plan = json.loads((run / "plan.json").read_text())
    only = set(json.loads((run / "dry_run.json").read_text())) if args.dry_run_docs else None
    have = {r["key"] for r in _requests(run)}
    added = collections.Counter()
    with open(run / "requests.jsonl", "a") as fh:
        for d in plan:
            if not d["missing"] or (only is not None and d["content_hash"] not in only):
                continue
            info, reqs = _prepare(d)
            if info["missing"] != d["missing"]:
                raise AssertionError(f"T0 no longer reproduces the plan for {d['path']}")
            for r in reqs:
                if r["key"] not in have:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                    added[(r["arm"], r["kind"])] += 1
    print(f"requests added: {dict(added)}")
    return 0


# --------------------------------------------------------------------------- #
# Estimates and the two guards
# --------------------------------------------------------------------------- #
def _requests(run: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in open(run / "requests.jsonl")]


def _measured_cpt() -> dict[str, float]:
    """chars/token per model from whatever is already cached. 0.9 safety factor
    so the estimate errs high."""
    chars, toks = collections.Counter(), collections.Counter()
    for p in CACHE.glob("*/*.json"):
        e = json.loads(p.read_text())
        chars[e["model"]] += e.get("chars", 0)
        toks[e["model"]] += e["message"]["usage"]["input_tokens"]
    return {m: (0.9 * chars[m] / toks[m] if toks[m] and chars[m] else DEFAULT_CPT[m])
            for m in DEFAULT_CPT}


def _measured_out() -> dict[tuple[str, str], float]:
    outs = collections.defaultdict(list)
    for p in CACHE.glob("*/*.json"):
        e = json.loads(p.read_text())
        outs[(e["kind"], e["arm"])].append(e["message"]["usage"]["output_tokens"])
    return {k: (1.5 * statistics.mean(outs[k]) if outs.get(k) else v)
            for k, v in DEFAULT_OUT.items()}


def _price(model: str, transport: str, prompt_tokens: int) -> tuple[float, float]:
    p = PRICES[model]
    if prompt_tokens > LONG_PROMPT_TOKENS and f"{transport}_over" in p:
        return p[f"{transport}_over"]
    return p[transport]


def _estimate(reqs: list[dict], transport: str) -> dict:
    cpt, out = _measured_cpt(), _measured_out()
    per_arm = collections.defaultdict(lambda: {"requests": 0, "in": 0, "out": 0, "usd": 0.0})
    flagged = set()
    for r in reqs:
        model = r["params"]["model"]
        tin = int(r["chars"] / cpt[model])
        tout = int(out[(r["kind"], r["arm"])])
        if model == "claude-haiku-5-5" and tin > LONG_PROMPT_TOKENS:
            flagged.add(r["content_hash"])
        pin, pout = _price(model, transport, tin)
        a = per_arm[r["arm"]]
        a["requests"] += 1
        a["in"] += tin
        a["out"] += tout
        a["usd"] += (tin * pin + tout * pout) / 1e6
    return {"per_arm": dict(per_arm), "flagged": flagged, "cpt": cpt}


def _print_estimate(est: dict) -> float:
    print(f"chars/token used: { {m: round(v, 2) for m, v in est['cpt'].items()} }")
    total = 0.0
    for arm, a in sorted(est["per_arm"].items()):
        print(f"  {arm:5} {a['requests']:4} req  in~{a['in']:>9,}  out~{a['out']:>8,}  ~${a['usd']:.3f}")
        total += a["usd"]
    print(f"  total ~${total:.3f}")
    return total


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #
def _client():
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        for line in (REPO / ".env").read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY not set in the environment or .env")
    return anthropic.Anthropic(api_key=key, max_retries=4)


def _store_with_chars(req: dict, message: dict, transport: str) -> None:
    store(req, message, transport)
    p = _index()[req["key"]]
    entry = json.loads(p.read_text())
    entry["chars"] = req["chars"]
    p.write_text(json.dumps(entry, ensure_ascii=False, indent=1))


def _pick_dry_run(run: pathlib.Path, n: int) -> list[str]:
    """Labelled docs that need T1, spread across type and manufacturer."""
    plan = json.loads((run / "plan.json").read_text())
    pool = [d for d in plan if d["gt"] is not None and d["missing"]]
    picked, types, manus = [], set(), set()
    for d in pool:
        t = d["gt"].get("type")
        if t not in types and d["manu"] not in manus:
            picked.append(d)
            types.add(t)
            manus.add(d["manu"])
    for d in pool:
        if len(picked) >= n:
            break
        if d not in picked and d["manu"] not in manus:
            picked.append(d)
            manus.add(d["manu"])
    return [d["content_hash"] for d in picked[:n]]


def cmd_dry_run(args) -> int:
    run = pathlib.Path(args.run)
    picked = run / "dry_run.json"
    hashes = json.loads(picked.read_text()) if picked.exists() else _pick_dry_run(run, args.n)
    reqs = [r for r in _requests(run) if r["content_hash"] in hashes]
    todo = [r for r in reqs if cached(r["key"]) is None]
    print(f"dry run: {len(hashes)} docs, {len(reqs)} requests, {len(todo)} not cached (sync prices)")
    total = _print_estimate(_estimate(todo, "sync"))
    if total > args.cap_usd:
        print(f"STOP: estimate ${total:.3f} exceeds cap ${args.cap_usd}")
        return 2
    client = _client()
    for r in todo:
        msg = client.messages.create(**r["params"])
        _store_with_chars(r, msg.to_dict(), "sync")
        print(f"  {r['arm']:5} {r['kind']:8} {r['content_hash'][:10]} stop={msg.stop_reason} "
              f"in={msg.usage.input_tokens} out={msg.usage.output_tokens}")
    (run / "dry_run.json").write_text(json.dumps(hashes))
    rows = _evaluate(run, only=set(hashes))
    text = _side_by_side(rows)
    (run / "dry_run.md").write_text(text)
    print(text)
    return 0


def cmd_batch(args) -> int:
    run = pathlib.Path(args.run)
    state_path = run / "batches.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    client = _client()
    submitted = {k for ids in state.values() for k in ids.values()}
    reqs = _requests(run)
    skip_path = run / "skipped_over_100k.json"
    if skip_path.exists() and state:
        skip = set(json.loads(skip_path.read_text()))
    else:
        est_all = _estimate(reqs, "batch")
        skip = set() if args.allow_over_100k else est_all["flagged"]
        if est_all["flagged"]:
            print(f"prompt over 100k tokens on Haiku 5.5 (estimated): {sorted(h[:12] for h in est_all['flagged'])}"
                  f" -> {'INCLUDED' if args.allow_over_100k else 'skipped, all arms'}")
        skip_path.write_text(json.dumps(sorted(skip)))
    todo = [r for r in reqs if r["content_hash"] not in skip and cached(r["key"]) is None
            and r["key"] not in submitted]
    print(f"batch: {len(todo)} requests neither cached nor submitted")
    if todo:
        total = _print_estimate(_estimate(todo, "batch"))
        if total > args.cap_usd:
            print(f"STOP: estimate ${total:.3f} exceeds cap ${args.cap_usd}")
            return 2
        by_arm = collections.defaultdict(list)
        for r in todo:
            by_arm[r["arm"]].append(r)
        for arm, rs in by_arm.items():
            ids = {f"{r['kind'][0]}_{arm}_{r['content_hash'][:40]}": r["key"] for r in rs}
            batch = client.messages.batches.create(requests=[
                {"custom_id": cid, "params": r["params"]} for cid, r in zip(ids, rs)])
            state[batch.id] = ids
            state_path.write_text(json.dumps(state, indent=1))
            print(f"  submitted {arm}: {batch.id} ({len(rs)} requests)")
    by_key = {r["key"]: r for r in reqs}
    pending = {bid for bid, ids in state.items() if any(cached(k) is None for k in ids.values())}
    errors = []
    while pending:
        for bid in sorted(pending):
            b = client.messages.batches.retrieve(bid)
            if b.processing_status != "ended":
                continue
            for item in client.messages.batches.results(bid):
                req = by_key[state[bid][item.custom_id]]
                if item.result.type == "succeeded":
                    _store_with_chars(req, item.result.message.to_dict(), "batch")
                else:
                    errors.append({"custom_id": item.custom_id, "type": item.result.type,
                                   "error": str(getattr(item.result, "error", ""))[:300]})
            pending.discard(bid)
            print(f"  ended {bid}: {b.request_counts}")
        if pending:
            time.sleep(30)
    (run / "batch_errors.json").write_text(json.dumps(errors, indent=1))
    print(f"batch complete; {len(errors)} non-succeeded results")
    return 0


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def _norm_str(s) -> str:
    return " ".join(str(s).split()).casefold().rstrip(".;,")


_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%Y.%m.%d",
                 "%d %B %Y", "%B %d, %Y", "%d. %m. %Y")


def _norm_date(s) -> str:
    s = " ".join(str(s).split())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return s.casefold()


def norm(field: str, v):
    if v is None:
        return None
    if field in LIST_FIELDS:
        codes = frozenset("".join(str(x).split()).casefold() for x in v if str(x).strip())
        return codes or None
    if field in DATE_FIELDS:
        return _norm_date(v)
    return _norm_str(v) or None


def _ws(s: str) -> str:
    return " ".join(s.split()).casefold()


def _verbatim_ok(verbatim: str, page, pages: list[str]) -> bool:
    """The quote is on the cited page (fuzzy >= 95); with no page, anywhere."""
    from rapidfuzz import fuzz
    v = _ws(verbatim or "")
    if not v:
        return False
    if isinstance(page, int) and 1 <= page <= len(pages):
        hay = [_ws(pages[page - 1])]
    else:
        hay = [_ws(p) for p in pages]
    return any(v in h or fuzz.partial_ratio(v, h) >= 95 for h in hay)


def _schema_problems(data: dict) -> list[str]:
    probs = []
    for f in FIELDS:
        raw = data.get(f)
        if not isinstance(raw, dict):
            probs.append(f"{f}:missing")
            continue
        v, c = raw.get("value"), raw.get("confidence")
        if f in LIST_FIELDS:
            if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                probs.append(f"{f}:type")
        elif v is not None and not isinstance(v, str):
            probs.append(f"{f}:type")
        if not isinstance(c, (int, float)) or not 0 <= c <= 1:
            probs.append(f"{f}:confidence")
        if "page" in raw and not isinstance(raw["page"], int):
            probs.append(f"{f}:page")
    return probs


class AnyOf(tuple):
    """A label with several right answers: a value equal to any of them scores
    right (`_correct`). For a document that prints more than one value of a
    single-value field."""


#: Scoring corrections to `tests/fixtures/corpus_manifest.py` ground truth, applied
#: here and never written back (the fixture fix is `[corpus-manifest-label-errors]`).
#: The labels date from 2026-07-06 and the prompt's field definitions moved since;
#: each row names the rule or the page that settles it. `SKIP` excludes a field.
LABEL_OVERRIDES: dict[tuple[str, str], object] = {
    # printed in the document, check pair valid (validate_basic_udi)
    ("Izjava o skladnosti za vsadke TL SP.pdf", "basic_udi_di"): "0763003170058QZ",   # pages 3-8
    ("everX_Posterior_12022026.pdf", "basic_udi_di"): "++J022MD0126K9",                 # page 2
    # The label `++E2265330681` fails its check pair; it is the prefix the page's
    # UDIs share. Page 1 prints four, each check pair valid: KSCH (rows 1-4 and
    # 11-14), StSEF (10 rows), PQ and PS (one row each). A multi-value basic_udi_di
    # is out of scope, so a read naming any one of them is right (Denis,
    # 2026-10-13; research doc section 13, the 15th label error).
    ("533068_RA_812_Liste_DoC.pdf", "basic_udi_di"): AnyOf((
        "++E2265330681KSCH", "++E2265330681StSEF", "++E2265330682PQ", "++E2265330683PS")),
    # The 7 DoC cert_number entries that stood here are gone: ruling A (2026-10-12)
    # says a DoC's cert_number is the certificate it cites, so the labels are right.
    # t1_text.md: "a declaration naming one product is still `group`"
    ("Viso G1 MDR EN.pdf", "coverage_scope"): "group",
    ("VOCO_DoC_(EU)2023-1542_Celalux 3.pdf", "coverage_scope"): "group",
    ("EI-INS-DoC-Siroseal Premium-6526961-2021-05-21.pdf", "coverage_scope"): "group",
    ("Declaration_of_Conformity_LUMIWHITE_OU.pdf", "coverage_scope"): "group",
    # page 1: "Reference VOCO: 9092, also part of 9090"
    ("VOCO_DoC_(EU)2023-1542_Celalux 3.pdf", "ref_list"): ["9092", "9090"],
    # the label describes the declaration + annex pair; the eval reads the annex alone
    ("533068_RA_812_Liste_DoC.pdf", "regulation"): "SKIP",
}


def corrected_gt(path: str, gt: dict | None) -> dict | None:
    """Ground truth for one document with LABEL_OVERRIDES applied."""
    if gt is None:
        return None
    name = pathlib.Path(path).name
    out = dict(gt)
    for (doc, field), value in LABEL_OVERRIDES.items():
        if doc == name:
            out[field] = "PENDING" if value == "SKIP" else value
    return out


def _evaluate(run: pathlib.Path, only: set[str] | None = None) -> list[dict]:
    """One record per (doc, arm): replayed pipeline answer, raw answer, checks."""
    plan = json.loads((run / "plan.json").read_text())
    skipped = set(json.loads((run / "skipped_over_100k.json").read_text())
                  if (run / "skipped_over_100k.json").exists() else [])
    reqs = collections.defaultdict(dict)
    for r in _requests(run):
        reqs[r["content_hash"]][(r["arm"], r["kind"])] = r
    out = []
    for d in plan:
        h = d["content_hash"]
        if not d["missing"] or h in skipped or (only is not None and h not in only):
            continue
        d["gt"] = corrected_gt(d["path"], d["gt"])
        if any(cached(r["key"]) is None for r in reqs[h].values()):
            continue
        path = str(REPO / d["path"])
        with pdfutil.open_doc(path) as doc:
            pages = pdfutil.page_texts(doc)
            full = _ws("\n".join(pages)).replace(" ", "")
            kwargs = {}
            if d["hinted"]:
                steering = t0_templates.steering_playbook("\n".join(pages))
                kwargs["hints"] = _hints_for(steering, tiers.needs_escalation({}), authoritative=False)
            for arm_id in [a for a in ARMS if (a, "extract") in reqs[h]]:
                entry = cached(reqs[h][(arm_id, "extract")]["key"])
                msg = load_message(entry["request_key"])
                llm = _llm(arm_id, "replay")
                # `llm.extract` returns every parsed field since Task 4 of the
                # extraction ladder; this report is about the fields T1 was
                # asked, as before, so the rest stays out of `answer` and `merged`.
                # The arm's prompt variant, as at capture: the replay must send
                # the request the response was cached under.
                with _prompt_variant(ARMS[arm_id].prompt):
                    answer = llm.extract("T1", doc, path, d["missing"], **kwargs)
                answer = {f: ev for f, ev in answer.items() if f in d["missing"]}
                data = llm_mod._response_json(msg)
                raw = llm_mod._fields_from_json(data, "T1", ARMS[arm_id].model)
                merged = tiers.merge(d["t0"], answer)
                rec = {"doc": d, "arm": arm_id, "answer": answer, "raw": raw, "merged": merged,
                       "stop": msg.stop_reason, "usage": msg.usage.model_dump(),
                       "transport": entry["transport"], "model": entry["model"],
                       "thinking_blocks": sum(b.type == "thinking" for b in msg.content),
                       "has_text": any(b.type == "text" for b in msg.content),
                       "parse_ok": bool(data), "schema": _schema_problems(data) if data else ["unparsed"],
                       "refusal_category": getattr(getattr(msg, "stop_details", None), "category", None),
                       "verbatim_ok": {}, "ref_unfound": 0, "ref_total": 0,
                       "after_t1_missing": tiers.needs_escalation(merged), "classify": None}
                for f, ev in raw.items():
                    if norm(f, ev["value"]) is not None:
                        rec["verbatim_ok"][f] = _verbatim_ok(ev["verbatim"], ev["page"], pages)
                refs = (raw.get("ref_list") or {}).get("value") or []
                rec["ref_total"] = len(refs)
                rec["ref_unfound"] = sum("".join(str(c).split()).casefold() not in full for c in refs)
                if (arm_id, "classify") in reqs[h]:
                    centry = cached(reqs[h][(arm_id, "classify")]["key"])
                    cmsg = load_message(centry["request_key"])
                    rec["classify"] = {"answer": llm.classify(doc, path)[0], "stop": cmsg.stop_reason,
                                       "usage": cmsg.usage.model_dump(), "transport": centry["transport"]}
                out.append(rec)
    return out


def _cost(model: str, usage: dict, transport: str) -> float:
    cr = usage.get("cache_read_input_tokens") or 0
    cw = usage.get("cache_creation_input_tokens") or 0
    tin = usage["input_tokens"]
    pin, pout = _price(model, transport, tin + cr + cw)
    return (tin * pin + cr * pin * CACHE_READ_MULT + cw * pin * CACHE_WRITE_MULT
            + usage["output_tokens"] * pout) / 1e6


def _gt_value(field: str, gt: dict):
    """Ground truth for one field, or the sentinel SKIP. The labels predate two
    rulings: regulation 'n.a.' (2026-08-11) replaced null, and validity_from
    became the issue date (2026-08-13) -- the latter is not scored at all."""
    if field not in gt or field == "validity_from":
        return SKIP
    v = gt[field]
    if v == "PENDING":
        return SKIP
    if field == "regulation" and v is None:
        return "n.a."
    return v


SKIP = object()


def _correct(field: str, model_value, gt_value) -> bool | None:
    if gt_value is SKIP:
        return None
    if isinstance(gt_value, AnyOf):
        return any(_correct(field, model_value, g) for g in gt_value)
    if field == "ref_list" and isinstance(gt_value, str):
        m = re.match(r"(~?)(\d+)\s+codes?", gt_value)
        if not m:
            return None          # 'external', 'text-list'
        n = min(int(m.group(2)), tiers.REF_LIST_PROMPT_CAP)
        got = len(model_value or [])
        return abs(got - n) <= 0.1 * n if m.group(1) else got == n
    return norm(field, model_value) == norm(field, gt_value)


def _fmt(field: str, ev: dict | None) -> str:
    if not ev:
        return "-"
    v = ev.get("value")
    if isinstance(v, list):
        v = f"[{len(v)}]"
    s = "null" if v is None else str(v)
    return f"{s[:28]} ({ev.get('conf', 0):.2f})"


def _summary(rows: list[dict]) -> list[str]:
    """Per arm: confidence, escalation to T2, accuracy on the scored labels, cost."""
    lines = ["| arm | asked: mean conf | asked < 0.95 | all fields >= 0.95 | correct (asked, scored) | "
             "correct (all, scored) | fields to T2 | docs to T2 | median out tok | $ batch-equiv |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for a in ARMS:
        rs = [r for r in rows if r["arm"] == a]
        if not rs:
            continue
        asked = [ev["conf"] for r in rs for ev in r["answer"].values()]
        allc = [ev["conf"] for r in rs for ev in r["raw"].values()]
        ca = [_correct(f, ev["value"], _gt_value(f, r["doc"]["gt"]))
              for r in rs if r["doc"]["gt"] for f, ev in r["answer"].items()]
        cl = [_correct(f, ev["value"], _gt_value(f, r["doc"]["gt"]))
              for r in rs if r["doc"]["gt"] for f, ev in r["raw"].items()]
        ca, cl = [c for c in ca if c is not None], [c for c in cl if c is not None]
        cost = sum(_cost(r["model"], r["usage"], "batch") for r in rs)
        lines.append(
            f"| {a} | {statistics.mean(asked):.3f} | {_pct(sum(c < 0.95 for c in asked), len(asked))} | "
            f"{_pct(sum(c >= 0.95 for c in allc), len(allc))} | {_pct(sum(ca), len(ca))} | "
            f"{_pct(sum(cl), len(cl))} | {sum(len(r['after_t1_missing']) for r in rs)} | "
            f"{sum(bool(r['after_t1_missing']) for r in rs)}/{len(rs)} | "
            f"{statistics.median(r['usage']['output_tokens'] for r in rs):.0f} | {cost:.4f} |")
    return lines


def _side_by_side(rows: list[dict]) -> str:
    by_doc = collections.defaultdict(dict)
    for r in rows:
        by_doc[r["doc"]["content_hash"]][r["arm"]] = r
    lines = ["# Dry run: side by side", "", "## Summary", ""] + _summary(rows) + [""]
    for h, arms in by_doc.items():
        d = arms[BASE]["doc"]
        lines += [f"## {d['path']}", "",
                  f"T0 class `{d['doc_class']}`; T1 asked for: {', '.join(d['missing'])}; "
                  f"hints: {'yes' if d['hinted'] else 'no'}", ""]
        present = [a for a in ARMS if a in arms]
        lines.append("| field | asked | GT | " + " | ".join(present) + " | verbatim ok |")
        lines.append("|---|---|---|" + "---|" * len(present) + "---|")
        for f in FIELDS:
            gt = _gt_value(f, d["gt"]) if d["gt"] else SKIP
            gts = "-" if gt is SKIP else (f"[{len(gt)}]" if isinstance(gt, list) else str(gt))[:24]
            vals = " | ".join(_fmt(f, arms[a]["raw"].get(f)) for a in present)
            vok = " ".join(f"{a}:{'y' if arms[a]['verbatim_ok'].get(f) else ('n' if f in arms[a]['verbatim_ok'] else '-')}"
                           for a in present)
            lines.append(f"| {f} | {'x' if f in d['missing'] else ''} | {gts} | {vals} | {vok} |")
        lines.append("")
        for a in present:
            r = arms[a]
            u = r["usage"]
            lines.append(f"- {a}: stop `{r['stop']}`, in {u['input_tokens']}, out {u['output_tokens']}, "
                         f"thinking blocks {r['thinking_blocks']}, ${_cost(r['model'], u, r['transport']):.5f} "
                         f"({r['transport']}), still needs T2 for: {', '.join(r['after_t1_missing']) or 'nothing'}"
                         + (f"; classify -> {r['classify']['answer']} (stop {r['classify']['stop']})"
                            if r["classify"] else ""))
        lines.append("")
    return "\n".join(lines)


def _pattern(field: str, a, b) -> str:
    na, nb = norm(field, a), norm(field, b)
    if na is None:
        return f"{field}: 4.5 null, 5.5 value"
    if nb is None:
        return f"{field}: 4.5 value, 5.5 null"
    if field in LIST_FIELDS:
        if nb < na:
            return f"{field}: 5.5 returns fewer codes"
        if nb > na:
            return f"{field}: 5.5 returns more codes"
        return f"{field}: different codes"
    if field in ("type", "regulation", "coverage_scope"):
        return f"{field}: {a} -> {b}"
    if field in DATE_FIELDS:
        return f"{field}: different date"
    return f"{field}: different value"


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "0/0"


def cmd_report(args) -> int:
    run = pathlib.Path(args.run)
    rows = _evaluate(run)
    plan = json.loads((run / "plan.json").read_text())
    by_doc = collections.defaultdict(dict)
    for r in rows:
        by_doc[r["doc"]["content_hash"]][r["arm"]] = r
    # Arms every compared doc has; an arm run on a subset (e.g. h55hi on the
    # dry-run docs) is left out rather than shrinking the comparison.
    docs = [a for a in by_doc.values() if all(x in a for x in (BASE, *CHALLENGERS[:2]))]
    RA = [x for x in ARMS if all(x in a for a in docs)]
    CH = [x for x in RA if x != BASE]

    # results.csv / disagreements.csv
    res_rows, dis_rows = [], []
    for arms in docs:
        d = arms[BASE]["doc"]
        for f in FIELDS:
            row = {"content_hash": d["content_hash"], "filename": d["path"], "field": f,
                   "requested": f in d["missing"]}
            gt = _gt_value(f, d["gt"]) if d["gt"] else SKIP
            row["gt"] = "" if gt is SKIP else json.dumps(gt, ensure_ascii=False)
            base_ev = arms[BASE]["raw"].get(f) or {}
            for a in RA:
                ev = arms[a]["raw"].get(f) or {}
                sfx = SUFFIX[a]
                row[f"v{sfx}"] = json.dumps(ev.get("value"), ensure_ascii=False)
                row[f"conf{sfx}"] = ev.get("conf", "")
                row[f"verbatim_ok_{sfx}"] = arms[a]["verbatim_ok"].get(f, "")
                c = _correct(f, ev.get("value"), gt) if ev else None
                row[f"correct_{sfx}"] = "" if c is None else c
                if a != BASE:
                    agree = norm(f, ev.get("value")) == norm(f, base_ev.get("value"))
                    row[f"agree_{sfx}"] = agree
                    if not agree:
                        dis_rows.append({"content_hash": d["content_hash"], "filename": d["path"],
                                         "field": f, "requested": f in d["missing"], "arm": a,
                                         "v45": row["v45"], "conf45": base_ev.get("conf", ""),
                                         "verbatim45": base_ev.get("verbatim", ""),
                                         "v_challenger": row[f"v{sfx}"], "conf_challenger": ev.get("conf", ""),
                                         "verbatim_challenger": ev.get("verbatim", ""),
                                         "gt": row["gt"], "pattern": _pattern(f, base_ev.get("value"), ev.get("value"))})
            res_rows.append(row)
    sx = [SUFFIX[a] for a in RA]
    cols = (["content_hash", "filename", "field", "requested"]
            + [c for s in sx for c in (f"v{s}", f"conf{s}")]
            + [f"agree_{SUFFIX[a]}" for a in CH]
            + [f"verbatim_ok_{s}" for s in sx] + ["gt"] + [f"correct_{s}" for s in sx])
    _write_csv(run / "results.csv", res_rows, cols)
    _write_csv(run / "disagreements.csv", dis_rows, list(dis_rows[0]) if dis_rows else ["content_hash"])

    # copy the raw responses this run used
    for arms in docs:
        for r in arms.values():
            h = r["doc"]["content_hash"]
            for p in (CACHE / h).glob(f"{r['arm']}-*.json"):
                (run / "raw" / f"{h[:16]}_{p.name}").write_text(p.read_text())

    L = [f"# Haiku 4.5 vs 5.5 on T1 extraction", "",
         f"Run `{run.name}`. Arms: " + "; ".join(f"`{k}` = {v.label}" for k, v in ARMS.items() if k in RA), ""]

    # routing
    skipped = json.loads((run / "skipped_over_100k.json").read_text()) if (run / "skipped_over_100k.json").exists() else []
    routing = collections.Counter(
        "business-doc/msds (no LLM)" if d["doc_class"] in ("business-doc", "msds")
        else "T0 complete, no T1 call" if not d["missing"]
        else "needs T1, skipped (>100k on 5.5)" if d["content_hash"] in skipped
        else "needs T1" for d in plan)
    excl = collections.Counter(r["reason"] for r in csv.DictReader(open(run / "excluded.csv")))
    L += ["## Corpus and routing", "",
          f"Excluded before sampling: {dict(excl)} (list in `excluded.csv`). "
          f"Sample: {len(plan)} text docs ({sum(d['gt'] is not None for d in plan)} labelled). "
          f"Compared: {len(docs)} docs with all three arms answered.", "",
          "| route after T0 | docs |", "|---|---|"] + [f"| {k} | {v} |" for k, v in routing.most_common()] + [""]
    L += ["Still needs T2 after T1 (the per-field ladder would pay for a vision call):", "",
          "| arm | docs escalating to T2 |", "|---|---|"]
    L += [f"| {a} | {_pct(sum(bool(x[a]['after_t1_missing']) for x in docs), len(docs))} |" for a in RA] + [""]

    # schema / stops
    L += ["## Response health", "", "| arm | stop reasons | refusals | parse failures | schema problems | thinking blocks (docs) |",
          "|---|---|---|---|---|---|"]
    for a in RA:
        rs = [x[a] for x in docs]
        L.append(f"| {a} | {dict(collections.Counter(r['stop'] for r in rs))} | "
                 f"{sum(r['stop'] == 'refusal' for r in rs)} | {sum(not r['parse_ok'] for r in rs)} | "
                 f"{sum(bool(r['schema']) and r['parse_ok'] for r in rs)} | {sum(r['thinking_blocks'] > 0 for r in rs)} |")
    L.append("")

    # agreement
    L += ["## Agreement with Haiku 4.5", "",
          "Normalized: dates to ISO, REF lists as sets, strings case- and whitespace-folded. "
          "`asked` = fields T1 was actually asked for (what the pipeline keeps); `all` = every field returned.", "",
          "| field | " + " | ".join(f"{a} asked | {a} all" for a in CH) + " |",
          "|---|" + "---|---|" * len(CH)]
    for f in FIELDS:
        cells = []
        for a in CH:
            for asked in (True, False):
                xs = [x for x in docs if (f in x[BASE]["doc"]["missing"]) or not asked]
                ag = sum(norm(f, (x[a]["raw"].get(f) or {}).get("value"))
                         == norm(f, (x[BASE]["raw"].get(f) or {}).get("value")) for x in xs)
                cells.append(_pct(ag, len(xs)))
        L.append(f"| {f} | " + " | ".join(cells) + " |")
    L.append("")

    # verbatim
    L += ["## Verbatim fidelity", "",
          "Quote found on the cited page (exact or fuzzy >= 95), non-null values only. "
          "REF codes checked one by one against the whole text.", "",
          "| field | " + " | ".join(RA) + " |", "|---|" + "---|" * len(RA)]
    for f in FIELDS:
        cells = []
        for a in RA:
            vals = [x[a]["verbatim_ok"][f] for x in docs if f in x[a]["verbatim_ok"]]
            cells.append(_pct(sum(vals), len(vals)))
        L.append(f"| {f} | " + " | ".join(cells) + " |")
    L.append("| REF codes not in text | " + " | ".join(
        _pct(sum(x[a]["ref_unfound"] for x in docs), sum(x[a]["ref_total"] for x in docs)) for a in RA) + " |")
    L.append("")

    # null trap
    def trap(r):
        raw = r["raw"]
        t = (raw.get("type") or r["doc"]["t0"].get("type") or {}).get("value")
        return (t == "DoC" and norm("validity_to", (raw.get("validity_to") or {}).get("value")) is not None
                and not (raw.get("referenced_docs") or {}).get("value")
                and norm("cert_number", (raw.get("cert_number") or {}).get("value")) is None)
    L += ["## Issue-vs-expiry trap", "",
          "DoC with a `validity_to` but no referenced document and no certificate number.", "",
          "| arm | docs |", "|---|---|"] + [f"| {a} | {sum(trap(x[a]) for x in docs)} |" for a in RA] + [""]

    # classify
    cls = [x for x in docs if x[BASE]["classify"]]
    L += ["## Doc-class call (`classify`, max_tokens 256)", ""]
    if cls:
        L += ["| doc | " + " | ".join(RA) + " |", "|---|" + "---|" * len(RA)]
        for x in cls:
            L.append(f"| {x[BASE]['doc']['path'][-50:]} | " + " | ".join(
                f"{x[a]['classify']['answer']} ({x[a]['classify']['stop']}, out {x[a]['classify']['usage']['output_tokens']})"
                for a in RA) + " |")
    else:
        L.append("No sampled document reached it (T0 typed every one).")
    L.append("")

    # cost
    L += ["## Cost and tokens", "",
          "| arm | input tok | output tok | actual $ | batch-equivalent $ | $/doc (batch) | projected 1,042 docs |",
          "|---|---|---|---|---|---|---|"]
    tin = {}
    for a in RA:
        rs = [x[a] for x in docs]
        usages = [(r["model"], r["usage"], r["transport"]) for r in rs]
        usages += [(r["model"], r["classify"]["usage"], r["classify"]["transport"]) for r in rs if r["classify"]]
        actual = sum(_cost(m, u, t) for m, u, t in usages)
        batch = sum(_cost(m, u, "batch") for m, u, _ in usages)
        tin[a] = [r["usage"]["input_tokens"] for r in rs]
        per_doc = batch / len(rs) if rs else 0
        L.append(f"| {a} | {sum(u['input_tokens'] for _, u, _ in usages):,} | "
                 f"{sum(u['output_tokens'] for _, u, _ in usages):,} | {actual:.4f} | {batch:.4f} | "
                 f"{per_doc:.5f} | {per_doc * 1042:.2f} |")
    ratios = [b / a for a, b in zip(tin[BASE], tin["h55"]) if a]
    outs = {a: [x[a]["usage"]["output_tokens"] for x in docs] for a in RA}
    L += ["", f"Input-token ratio 5.5/4.5 on identical requests: median {statistics.median(ratios):.3f}, "
              f"min {min(ratios):.3f}, max {max(ratios):.3f}." if ratios else "",
          f"Median output tokens per extract: " + ", ".join(
              f"{a} {statistics.median(v):.0f}" for a, v in outs.items() if v) + ".", ""]

    # ground truth
    lab = [x for x in docs if x[BASE]["doc"]["gt"]]
    L += ["## Against ground truth", "",
          f"{len(lab)} labelled docs (`tests/fixtures/corpus_manifest.py`). `validity_from` is not scored "
          "(labels predate the 2026-08-13 issue-date ruling); a null `regulation` label is read as `n.a.`; "
          "REF lists described only as a count are scored by count (capped at the prompt's "
          f"{tiers.REF_LIST_PROMPT_CAP}, ±10% where the label says '~').", ""]
    if lab:
        L += ["T1's own answer, fields it was asked for:", "",
              "| field | " + " | ".join(RA) + " |", "|---|" + "---|" * len(RA)]
        for f in FIELDS:
            cells = []
            for a in RA:
                cs = [_correct(f, x[a]["answer"][f]["value"], _gt_value(f, x[a]["doc"]["gt"]))
                      for x in lab if f in x[a]["answer"]]
                cs = [c for c in cs if c is not None]
                cells.append(_pct(sum(cs), len(cs)))
            if any(c != "0/0" for c in cells):
                L.append(f"| {f} | " + " | ".join(cells) + " |")
        L += ["", "Final merged value (T0 + T1, what VALIDATE would receive), every scored field:", "",
              "| arm | correct |", "|---|---|"]
        for a in RA:
            cs = [_correct(f, (x[a]["merged"].get(f) or {}).get("value"), _gt_value(f, x[a]["doc"]["gt"]))
                  for x in lab for f in FIELDS]
            cs = [c for c in cs if c is not None]
            L.append(f"| {a} | {_pct(sum(cs), len(cs))} |")
        L += ["", "Calibration: accuracy by the model's own confidence (asked fields, scored only):", "",
              "| bucket | " + " | ".join(RA) + " |", "|---|" + "---|" * len(RA)]
        for name, lo, hi in CONF_BUCKETS:
            cells = []
            for a in RA:
                cs = [_correct(f, ev["value"], _gt_value(f, x[a]["doc"]["gt"]))
                      for x in lab for f, ev in x[a]["answer"].items() if lo <= ev["conf"] < hi]
                cs = [c for c in cs if c is not None]
                cells.append(_pct(sum(cs), len(cs)))
            L.append(f"| {name} | " + " | ".join(cells) + " |")
        L += ["", "Simulated gate (score = min confidence over type, regulation, coverage_scope of the merged "
              "fields; REF gate and VALIDATE flags not simulated). A wrong production write = any scored field wrong.", "",
              "| thresholds | arm | production | staged | manual | wrong production |", "|---|---|---|---|---|---|"]
        for gname, (hi, med) in GATE_SETTINGS.items():
            for a in RA:
                disp, wrong = collections.Counter(), 0
                for x in lab:
                    m = x[a]["merged"]
                    if any(norm(f, (m.get(f) or {}).get("value")) is None for f in GATE_REQUIRED):
                        disp["manual"] += 1
                        continue
                    score = min(m[f]["conf"] for f in GATE_REQUIRED)
                    dd = "production" if score >= hi else "staged" if score >= med else "manual"
                    disp[dd] += 1
                    if dd == "production" and any(
                            _correct(f, (m.get(f) or {}).get("value"), _gt_value(f, x[a]["doc"]["gt"])) is False
                            for f in FIELDS):
                        wrong += 1
                L.append(f"| {gname} | {a} | {disp['production']} | {disp['staged']} | {disp['manual']} | {wrong} |")
    L.append("")

    # patterns
    pats = collections.Counter((r["arm"], r["pattern"]) for r in dis_rows if r["requested"])
    L += ["## Top 10 disagreement patterns (asked fields)", "", "| arm | pattern | count |", "|---|---|---|"]
    L += [f"| {a} | {p} | {n} |" for (a, p), n in pats.most_common(10)] + [""]
    # Judgement, not arithmetic: written by hand into the run dir after reading
    # the tables, so a re-run of `report` keeps it.
    rec = run / "recommendation.md"
    L += ["## Recommendation", "",
          rec.read_text().strip() if rec.exists() else "_To be written after reading the numbers above._", ""]
    (run / "report.md").write_text("\n".join(L))
    print("\n".join(L))
    return 0


def _write_csv(path: pathlib.Path, rows: list[dict], cols: list[str]) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.haiku_compare")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--per-manu", type=int, default=7)
    p.add_argument("--seed", type=int, default=20261008)
    p = sub.add_parser("extend")
    p.add_argument("--run", required=True)
    p.add_argument("--dry-run-docs", action="store_true", help="only the dry run's documents")
    p = sub.add_parser("dry-run")
    p.add_argument("--run", required=True)
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--cap-usd", type=float, default=1.0)
    p = sub.add_parser("batch")
    p.add_argument("--run", required=True)
    p.add_argument("--cap-usd", type=float, default=10.0)
    p.add_argument("--allow-over-100k", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    return {"plan": cmd_plan, "extend": cmd_extend, "dry-run": cmd_dry_run, "batch": cmd_batch,
            "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
