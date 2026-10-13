"""Task 8 of the extraction ladder: the built ladder on the evaluation set, real reads.

An evaluation, not a pipeline change. Runs `tiers.run_extraction`, the ladder as it
is checked out, on the 79 text documents of `eval/haiku-compare/20261008T092446` and
the 34 scans of `eval/ladder-probe/20261008T134050`, with a stand-in Anthropic
client that serves the cached response for exactly the request the ladder builds
(`haiku_compare.request_key`). A request no cache holds is a MISS: the document
stops there for this round. Every miss of a round, across all documents, goes out
as ONE Batch API batch; the answers are stored and every document runs again from
the start, one step further each round (doc-class, T1, image reads, Sonnet, the
second reads), until nothing misses. Then `report` scores the result.

The main checkout holds the data: the eval runs, the SFTP mirror and the cached
responses are gitignored, so a worktree has none of them. Reads them, never writes
them. Writes only under the main checkout's `eval/quality-check/`:

    cache/<hash>/<label>-<key>.json   the answers this harness paid for (re-runs reuse them)
    state.json                        every batch submitted: its requests, estimate, measured
                                      cost. The cap and a resumed run read it.
    <timestamp>/                      one run: rounds.log, results.json, report.md, differences.csv

Money guards. Before each submission the round's cost is estimated at Batch API
prices (`app.extract.economics`); the batch is refused if that estimate plus what
earlier batches cost (measured) exceeds the cap ($0.90 by default). A batch id is
written to `state.json` the moment the API returns it, and a run collects every
batch it has not collected before it builds the next round, so a crash between
rounds neither loses a paid batch nor sends one twice. A submission interrupted
before its id was written is left as an `intent` the next run refuses to pass
(`--adopt BATCH_ID` or `--forget-intent`, after a look at the console).

    python -m tools.ladder_quality run --dry-run          round 1's misses and cost; no API
    python -m tools.ladder_quality run --simulate         every round, misses answered by a
                                                          stand-in from the eval cache; no API
    python -m tools.ladder_quality run [--cap-usd 0.90]   the paid run (Batch API)
    python -m tools.ladder_quality report --run DIR       quality, routing, gate, blockers
"""

from __future__ import annotations

import argparse
import base64
import collections
import datetime
import fcntl
import json
import os
import pathlib
import statistics
import struct
import subprocess
import time
from decimal import Decimal
from types import SimpleNamespace

from tools import haiku_compare as hc   # puts this checkout first on sys.path

import anthropic  # noqa: E402

from app import playbooks as playbooks_mod  # noqa: E402
from app.config import Extract, GateThresholds, Models  # noqa: E402
from app.extract import economics, tiers  # noqa: E402
from app.extract import llm as llm_mod  # noqa: E402
from app.extract import pdf as pdfutil  # noqa: E402
from app.extract import t0_templates  # noqa: E402
from app.extract.udi import validate_basic_udi  # noqa: E402
from app.handlers import gate  # noqa: E402
from app.handlers.extract import _hints_for  # noqa: E402

REPO = hc.REPO
TEXT_RUN = pathlib.Path("eval/haiku-compare/20261008T092446")
SCAN_RUN = pathlib.Path("eval/ladder-probe/20261008T134050")
BASE_CACHE = pathlib.Path("eval/haiku-compare/cache")
OUT = pathlib.Path("eval/quality-check")
CAP_USD = Decimal("0.90")      # Denis, 2026-10-13: Task 8 approved "under $1"
MAX_ROUNDS = 8                 # the ladder makes at most 5 calls a document
MAX_ATTEMPTS = 2               # a request the Batch API failed twice is a failed read
BATCH_BYTES = 200_000_000      # the Batch API takes 256 MB a batch
POLL_S = 30

# Estimates. Measured 2026-10-13 on the eval's cached requests: Haiku 5.5 and
# Sonnet 5.5 read 2.22 characters a token (median; 1.64 the lowest single request),
# 0.9 of it kept as margin, as haiku_compare does; a page image costs w*h/750
# tokens (median 1.01 of that, 1.05 the highest; a page large enough to be scaled
# down costs less), 1.06 kept. The system prompt is priced as a cache write
# (1.25x) although most requests will not write one.
CHARS_PER_TOKEN = 2.0
IMAGE_PX_PER_TOKEN = 750
IMAGE_MARGIN = 1.06

#: What each eval arm is, for the output-token estimate: (model, read, thinking off).
ARM_CLASS = {
    "h45": ("claude-haiku-4-5", "text", False),
    "h55": ("claude-haiku-5-5", "text", False),
    "h55a": ("claude-haiku-5-5", "text", False),
    "h55hi": ("claude-haiku-5-5", "text", False),
    "h55n": ("claude-haiku-5-5", "text", True),
    "h55v": ("claude-haiku-5-5", "image", False),
    "h55vn": ("claude-haiku-5-5", "image", True),
    "s55v": ("claude-sonnet-5-5", "image", False),
    "s55t": ("claude-sonnet-5-5", "text", False),
}
#: `--simulate` only: the eval arm whose cached answer stands in for a missed read.
STANDIN_ARM = {"T1": "h55a", "T1-second": "h55n", "T2-first": "h55v",
               "T2-second": "h55vn", "T2": "s55v"}
CLASSIFY_LABELS = (llm_mod.DOC_CLASS_LABEL, llm_mod.DOC_CLASS_IMAGE_LABEL)


# --------------------------------------------------------------------------- #
# Where things are, and the documents
# --------------------------------------------------------------------------- #
def main_checkout() -> pathlib.Path:
    """The main checkout (the same rule as scripts/test.sh): `--git-common-dir`
    is the shared `.git`, inside the main checkout whether this is one or a
    linked worktree."""
    out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--path-format=absolute",
                          "--git-common-dir"], capture_output=True, text=True, check=True)
    return pathlib.Path(out.stdout.strip()).parent


def load_docs(data: pathlib.Path) -> tuple[list[dict], dict[str, list[str]]]:
    """The 79 text documents (T1 needed, not business-doc / msds, not over 100k
    tokens: the haiku-compare set) and the 34 scans; and the fields the probe's
    cached image reads were asked, by content hash."""
    plan = json.loads((data / TEXT_RUN / "plan.json").read_text())
    skipped = set(json.loads((data / TEXT_RUN / "skipped_over_100k.json").read_text()))
    probe = json.loads((data / SCAN_RUN / "plan.json").read_text())
    docs = [{"set": "text", "content_hash": d["content_hash"], "path": d["path"], "gt": d["gt"],
             "plan": d} for d in plan
            if d["missing"] and d["doc_class"] not in ("business-doc", "msds")
            and d["content_hash"] not in skipped]
    docs += [{"set": "scan", "content_hash": s["content_hash"], "path": s["path"], "gt": s["gt"],
              "plan": s} for s in probe["scans"]]
    probe_open = {x["content_hash"]: x["open"] for x in probe["text_docs"] + probe["scans"]}
    return docs, probe_open


def config() -> SimpleNamespace:
    """The defaults the server runs (no MODELS_* or threshold key reaches it)."""
    th = GateThresholds()
    return SimpleNamespace(models=Models(), image_settle=Extract().image_settle,
                           agreement_min=th.agreement_min, high=th.high, med=th.med)


# --------------------------------------------------------------------------- #
# Cache: the eval's responses (read-only) and this harness's own
# --------------------------------------------------------------------------- #
class Cache:
    def __init__(self, base: pathlib.Path, own: pathlib.Path):
        self.base, self.own = base, own
        self.entries: dict[str, dict] = {}
        self.by_arm: dict[tuple[str, str, str], dict] = {}
        for p in sorted(base.glob("*/*.json")):
            e = json.loads(p.read_text())
            e["_source"] = "eval"
            self.entries[e["request_key"]] = e
            self.by_arm[(e["content_hash"], e["arm"], e["kind"])] = e
        for p in sorted(own.glob("*/*.json")):
            e = json.loads(p.read_text())
            e["_source"] = "own"
            self.entries[e["request_key"]] = e

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def arm(self, content_hash: str, arm: str, kind: str = "extract") -> dict | None:
        return self.by_arm.get((content_hash, arm, kind))

    def put(self, meta: dict, message: dict, batch_id: str, cost: Decimal) -> None:
        p = self.own / meta["content_hash"] / f"{meta['label']}-{meta['key'][:16]}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        entry = {"request_key": meta["key"], "content_hash": meta["content_hash"],
                 "label": meta["label"], "arm": "quality-check", "kind": meta["kind"],
                 "model": meta["model"], "class": meta["class"], "transport": "batch",
                 "batch_id": batch_id, "cost_usd": str(cost),
                 "fetched_at": datetime.datetime.now(datetime.UTC).isoformat(), "message": message}
        _write_json(p, entry)
        self.entries[meta["key"]] = {**entry, "_source": "own"}

    def output_estimate(self) -> dict[tuple, int]:
        """Output tokens to expect per (model, read, thinking off): the larger of
        1.5x the mean and the 90th percentile of what the cache holds."""
        outs = collections.defaultdict(list)
        for e in self.entries.values():
            cls = tuple(e["class"]) if "class" in e else ARM_CLASS.get(e.get("arm"))
            if cls and e.get("kind") == "extract":
                outs[cls].append(e["message"]["usage"]["output_tokens"])
        est = {}
        for cls, v in outs.items():
            v = sorted(v)
            est[cls] = int(max(1.5 * statistics.mean(v), v[max(0, int(0.9 * len(v)) - 1)]))
        return est


def _write_json(path: pathlib.Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str))
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Batch state: what was sent, what it cost
# --------------------------------------------------------------------------- #
class State:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.data = (json.loads(path.read_text()) if path.exists()
                     else {"batches": {}, "attempts": {}, "intent": None})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self.path, self.data)

    def uncollected(self) -> list[str]:
        return [b for b, v in self.data["batches"].items() if not v.get("collected")]

    def spent(self) -> Decimal:
        """Measured cost of the collected batches, the estimate of the rest."""
        return sum((Decimal(v["cost_usd"]) if v.get("collected") else Decimal(v["estimate_usd"])
                    for v in self.data["batches"].values()), Decimal(0))

    def exhausted(self) -> set[str]:
        return {k for k, n in self.data["attempts"].items() if n >= MAX_ATTEMPTS}


# --------------------------------------------------------------------------- #
# The stand-in client
# --------------------------------------------------------------------------- #
class Miss(Exception):
    def __init__(self, req: dict):
        super().__init__(req["label"])
        self.req = req


class Failed(Exception):
    """A doc-class request the Batch API failed `MAX_ATTEMPTS` times: the
    document cannot be classified, and a refusal read off nothing would drop it."""


def _blocks(params: dict) -> list[dict]:
    out = []
    for m in params["messages"]:
        c = m["content"]
        out += c if isinstance(c, list) else [{"type": "text", "text": c}]
    return out


def _png_px(block: dict) -> int:
    head = base64.b64decode(block["source"]["data"][:44])
    w, h = struct.unpack(">II", head[16:24])
    return w * h


def request_record(doc: dict, label: str, params: dict, key: str) -> dict:
    """One miss: what the batch sends (`params`) and what the estimate and the
    cache entry need. `label` is the read step's own (`llm.READ_STEPS`)."""
    blocks = _blocks(params)
    images = [_png_px(b) for b in blocks if b.get("type") == "image"]
    kind = "classify" if label in CLASSIFY_LABELS else "extract"
    read = "classify" if kind == "classify" else ("image" if images else "text")
    return {"content_hash": doc["content_hash"], "path": doc["path"], "label": label, "key": key,
            "kind": kind, "model": params["model"],
            "class": (params["model"], read, params.get("thinking") == llm_mod.THINKING_OFF),
            "max_tokens": params["max_tokens"],
            "system_chars": len(json.dumps(params["system"], ensure_ascii=False)),
            "text_chars": sum(len(json.dumps(b.get("text", ""), ensure_ascii=False))
                              for b in blocks if b.get("type") == "text")
            + len(json.dumps(params.get("output_config"), ensure_ascii=False)),
            "image_px": images, "params": params}


def estimate_usd(req: dict, outs: dict) -> Decimal:
    out = req["max_tokens"] if req["kind"] == "classify" else outs.get(tuple(req["class"]),
                                                                       req["max_tokens"])
    usage = economics.Usage(
        input_tokens=int(req["text_chars"] / CHARS_PER_TOKEN
                         + sum(req["image_px"]) / IMAGE_PX_PER_TOKEN * IMAGE_MARGIN),
        cache_creation_tokens=int(req["system_chars"] / CHARS_PER_TOKEN),
        output_tokens=int(out))
    return economics.cost_usd(req["model"], usage, batch=True)


def _message(payload: dict | None, model: str) -> anthropic.types.Message:
    content = [] if payload is None else [{"type": "text", "text": json.dumps(payload)}]
    return anthropic.types.Message.model_validate({
        "id": "stand-in", "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0}})


class CachedClient:
    """Stands in for `anthropic.Anthropic` inside `AnthropicLlm`: the cached
    response for exactly this request, else `Miss`. With a `standin` it answers
    a miss itself (`--simulate`) and records it."""

    def __init__(self, cache: Cache, doc: dict, exhausted: set[str], standin=None):
        self.cache, self.doc, self.exhausted, self.standin = cache, doc, exhausted, standin
        self.messages = self
        self.label: str | None = None
        self.pages = None
        self.served: list[dict] = []
        self.misses: list[dict] = []

    def create(self, **params):
        key = hc.request_key(params)
        entry = self.cache.get(key)
        if entry is not None:
            self.served.append({"label": self.label, "key": key, "source": entry["_source"],
                                "model": entry["model"], "usage": entry["message"]["usage"]})
            return anthropic.types.Message.model_validate(entry["message"])
        req = request_record(self.doc, self.label, params, key)
        if key in self.exhausted:
            self.served.append({"label": self.label, "key": key, "source": "failed",
                                "model": params["model"], "usage": None})
            if req["kind"] == "classify":
                raise Failed(f"{self.label}: the Batch API failed it {MAX_ATTEMPTS} times")
            return _message(None, params["model"])      # a failed read: fields stay open
        if self.standin is None:
            raise Miss(req)
        self.misses.append(req)
        self.served.append({"label": self.label, "key": key, "source": "stand-in",
                            "model": params["model"], "usage": None})
        return self.standin(req, self)


class _Llm(llm_mod.AnthropicLlm):
    """Tells the client each request's label (custom ids, cache entries, cost
    rows) and an image read's page list (`--simulate` remaps a stand-in's pages)."""

    def extract(self, tier, doc, filename, missing, hints=None, **read_options):
        self.client.label, self.client.pages = tier, read_options.get("pages")
        return super().extract(tier, doc, filename, missing, hints=hints, **read_options)

    def classify(self, doc, filename):
        self.client.label, self.client.pages = llm_mod.DOC_CLASS_LABEL, None
        return super().classify(doc, filename)

    def classify_image(self, doc, filename):
        self.client.label, self.client.pages = llm_mod.DOC_CLASS_IMAGE_LABEL, None
        return super().classify_image(doc, filename)


def standin_for(cache: Cache):
    """`--simulate`: the eval arm's cached answer for this document
    (`STANDIN_ARM`), its pages remapped onto the images sent; an all-null answer
    where the eval has none (pessimistic: it sends the field on to Sonnet)."""
    def serve(req: dict, client: CachedClient):
        h = req["content_hash"]
        if req["kind"] == "classify":
            for arm in ("h55n", "h55", "h45"):
                e = cache.arm(h, arm, "classify")
                if e:
                    return anthropic.types.Message.model_validate(e["message"])
            gt_type = (client.doc["gt"] or {}).get("type")
            sonnet = cache.arm(h, "s55v")
            if not gt_type and sonnet:
                gt_type = (llm_mod._response_json(anthropic.types.Message.model_validate(
                    sonnet["message"])).get("type") or {}).get("value")
            return _message({"doc_class": gt_type if gt_type in llm_mod._DOC_CLASSES else "DoC",
                             "quote": ""}, req["model"])
        e = cache.arm(h, STANDIN_ARM.get(req["label"], ""))
        if e is None:
            return _message({f: {"value": None, "confidence": 0.0, "verbatim": ""}
                             for f in llm_mod.SCHEMA["required"]}, req["model"])
        data = llm_mod._response_json(anthropic.types.Message.model_validate(e["message"]))
        if client.pages is not None:
            sent = list(client.pages)
            for raw in data.values():
                if isinstance(raw, dict) and isinstance(raw.get("page"), int):
                    raw["page"] = sent.index(raw["page"]) + 1 if raw["page"] in sent else 99
        return _message(data, req["model"])
    return serve


# --------------------------------------------------------------------------- #
# One round
# --------------------------------------------------------------------------- #
def run_doc(doc: dict, data: pathlib.Path, cache: Cache, cfg, exhausted: set[str],
            standin=None) -> tuple[dict, list[dict]]:
    """The ladder on one document. Raises `Miss` at the first uncached request
    (unless a stand-in answers it).

    As the live handler (`app/handlers/extract.py`): the steering playbook is
    the group's manufacturer's when the document came through a group
    (`group_manufacturer`, authoritative), else T0's own text match (never
    authoritative: the eval documents, and every backfilled one). `companion`
    is the companion annex T0 reads the REF list from, a path under `data`."""
    path = str(data / doc["path"])
    companion = str(data / doc["companion"]) if doc.get("companion") else None
    group_mfr = doc.get("group_manufacturer")
    override = (playbooks_mod.for_manufacturer(group_mfr)
                if group_mfr and group_mfr != "unknown" else None)
    client = CachedClient(cache, doc, exhausted, standin)
    llm = _Llm(client, cfg.models)
    with pdfutil.open_doc(path) as pdf_doc:
        steering = t0_templates.steering_playbook("\n".join(pdfutil.page_texts(pdf_doc)),
                                                  override=override)
        hints = _hints_for(steering, tiers.needs_escalation({}),
                           authoritative=override is not None)
        doc_class, fields, used = tiers.run_extraction(
            pdf_doc, path, llm, hints=hints, companion_url=companion,
            image_settle=cfg.image_settle, agreement_min=cfg.agreement_min)
    return ({"content_hash": doc["content_hash"], "set": doc["set"], "path": doc["path"],
             "doc_class": doc_class, "used": used, "fields": fields, "served": client.served,
             "disagreements": tiers.read_disagreements(fields),
             "failed_reads": tiers.failed_reads(fields)}, client.misses)


def run_round(docs, data, cache, cfg, exhausted, standin=None):
    """Every document as far as the cache takes it. Returns (results, misses by key);
    with a stand-in, (results, every stand-in answer per document, in order)."""
    results, misses = {}, {}
    for d in docs:
        h = d["content_hash"]
        try:
            res, served_misses = run_doc(d, data, cache, cfg, exhausted, standin)
        except Miss as m:
            misses[m.req["key"]] = m.req
            continue
        except Failed as f:
            results[h] = {"content_hash": h, "set": d["set"], "path": d["path"], "error": str(f)}
            continue
        results[h] = res
        if standin is not None:
            misses[h] = served_misses
    return results, misses


def _by_label(reqs, outs) -> tuple[dict, Decimal]:
    rows = collections.defaultdict(lambda: [0, Decimal(0)])
    for r in reqs:
        rows[r["label"]][0] += 1
        rows[r["label"]][1] += estimate_usd(r, outs)
    return dict(rows), sum((v[1] for v in rows.values()), Decimal(0))


def _print_estimate(title: str, reqs, outs, log) -> Decimal:
    rows, total = _by_label(reqs, outs)
    log(f"{title}: {len(reqs)} requests")
    for label, (n, usd) in sorted(rows.items()):
        log(f"  {label:16} {n:4}  ~${usd:.4f}")
    log(f"  {'total':16} {len(reqs):4}  ~${total:.4f}")
    return total


# --------------------------------------------------------------------------- #
# Submit and collect
# --------------------------------------------------------------------------- #
def _meta(r: dict) -> dict:
    return {k: r[k] for k in ("key", "label", "content_hash", "kind", "model", "class")}


def submit(state: State, client, reqs: list[dict], outs: dict, cap: Decimal, round_no: int,
           log) -> list[str] | None:
    """One Batch API batch for this round's misses (more only past the API's
    size limit). None, and nothing sent, when the cap would be passed."""
    if state.data.get("intent"):
        raise SystemExit("an earlier submission was interrupted before its batch id was "
                         "written; check the console, then --adopt BATCH_ID or --forget-intent")
    est = _print_estimate(f"round {round_no}: to submit", reqs, outs, log)
    spent = state.spent()
    log(f"spent so far ${spent:.4f} (measured; estimated for any batch not collected) + "
        f"this round ~${est:.4f} = ~${spent + est:.4f}; cap ${cap}")
    if spent + est > cap:
        log("STOP: this round would pass the cap; nothing submitted")
        return None
    chunks, size, cur = [], 0, []
    for r in reqs:
        n = len(json.dumps(r["params"]))
        if cur and size + n > BATCH_BYTES:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += n
    chunks.append(cur)
    ids = []
    for chunk in chunks:
        cids = {llm_mod._custom_id(r["content_hash"], r["label"]): r for r in chunk}
        if len(cids) != len(chunk):
            raise SystemExit("two requests of one round share a custom id; nothing submitted")
        chunk_est = sum((estimate_usd(r, outs) for r in chunk), Decimal(0))
        meta = {cid: _meta(r) for cid, r in cids.items()}
        state.data["intent"] = {"at": _now(), "round": round_no, "requests": meta,
                                "estimate_usd": str(chunk_est)}
        for r in chunk:
            state.data["attempts"][r["key"]] = state.data["attempts"].get(r["key"], 0) + 1
        state.save()
        batch = client.messages.batches.create(requests=[
            {"custom_id": cid, "params": r["params"]} for cid, r in cids.items()])
        state.data["batches"][batch.id] = {"round": round_no, "submitted_at": _now(),
                                           "requests": meta, "estimate_usd": str(chunk_est),
                                           "collected": False}
        state.data["intent"] = None
        state.save()
        ids.append(batch.id)
        log(f"submitted {batch.id}: {len(chunk)} requests, ~${chunk_est:.4f}")
    return ids


def collect(state: State, cache: Cache, client, log, poll_s: int = POLL_S) -> None:
    """Wait for every batch not yet collected; store each answer, measure the cost."""
    for bid in state.uncollected():
        b = client.messages.batches.retrieve(bid)
        while b.processing_status != "ended":
            log(f"  {bid}: {b.processing_status} {b.request_counts}")
            time.sleep(poll_s)
            b = client.messages.batches.retrieve(bid)
        rec = state.data["batches"][bid]
        cost, errors = Decimal(0), []
        for item in client.messages.batches.results(bid):
            meta = rec["requests"][item.custom_id]
            if item.result.type == "succeeded":
                c = economics.cost_usd(meta["model"], economics.Usage.from_message(
                    item.result.message), batch=True)
                cache.put(meta, item.result.message.to_dict(), bid, c)
                cost += c
            else:
                errors.append({"custom_id": item.custom_id, "type": item.result.type,
                               "error": str(getattr(item.result, "error", ""))[:300]})
        rec.update(collected=True, cost_usd=str(cost), errors=errors, collected_at=_now(),
                   request_counts=str(b.request_counts))
        state.save()
        log(f"collected {bid}: ${cost:.4f} measured (estimate ${Decimal(rec['estimate_usd']):.4f}), "
            f"{len(errors)} not succeeded")


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def api_client(data: pathlib.Path):
    """The Anthropic client: ANTHROPIC_API_KEY from the environment, else the main
    checkout's `.env` (a worktree has none). Built only when a batch is sent or
    collected; `--dry-run` and `--simulate` never build it."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    env = data / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY not set in the environment or the main checkout's .env")
    return anthropic.Anthropic(api_key=key, max_retries=4)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def cmd_run(args) -> int:
    data = pathlib.Path(args.data) if args.data else main_checkout()
    root = data / OUT
    docs, _ = load_docs(data)
    run = (pathlib.Path(args.run) if args.run else
           root / (datetime.datetime.now().strftime("%Y%m%dT%H%M%S") + ("-sim" if args.simulate else "")))
    return drive(docs, data, Cache(data / BASE_CACHE, root / "cache"), State(root / "state.json"),
                 root, run, args, env_root=data,
                 finish=lambda run: cmd_report(SimpleNamespace(run=str(run), data=str(data))))


def drive(docs: list[dict], data: pathlib.Path, cache: Cache, state: State, root: pathlib.Path,
          run: pathlib.Path, args, env_root: pathlib.Path, finish) -> int:
    """The rounds, for any document list: `cmd_run` here, and
    `tools.prod_replay` on production's documents with its own cache, state and
    cap. `data` holds the PDFs (`doc["path"]` is relative to it), `root` the
    lock, `env_root` the `.env` with the API key; `finish(run)` reports a
    completed run. `args`: dry_run, simulate, cap_usd, adopt, forget_intent."""
    cfg = config()
    outs = cache.output_estimate()
    cap = Decimal(str(args.cap_usd))
    offline = args.dry_run or args.simulate
    if not args.dry_run:
        run.mkdir(parents=True, exist_ok=True)

    def log(s: str) -> None:
        print(s, flush=True)
        if not args.dry_run:                       # as it goes: a crash keeps the log
            with open(run / "rounds.log", "a") as fh:
                fh.write(s + "\n")

    if not offline:
        # One paid run at a time: two would both send the same misses.
        root.mkdir(parents=True, exist_ok=True)
        lock = open(root / ".lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"another run holds {root / '.lock'}") from None

    log(f"documents: {sum(d['set'] == 'text' for d in docs)} text, "
        f"{sum(d['set'] == 'scan' for d in docs)} scans; ladder from {REPO}; data from {data}")
    log(f"models {cfg.models.t1} / {cfg.models.vision_first} / {cfg.models.second_read} / "
        f"{cfg.models.t2}; image_settle {cfg.image_settle}, agreement_min {cfg.agreement_min}")

    if state.data.get("intent") and not offline:
        if args.adopt:
            intent = state.data["intent"]
            state.data["batches"][args.adopt] = {"round": intent["round"], "submitted_at": intent["at"],
                                                 "requests": intent["requests"],
                                                 "estimate_usd": intent["estimate_usd"],
                                                 "collected": False, "adopted": True}
            state.data["intent"] = None
            state.save()
            log(f"adopted {args.adopt} for the interrupted submission")
        elif args.forget_intent:
            state.data["intent"] = None
            state.save()
            log("forgot the interrupted submission (no batch was created)")
        else:
            raise SystemExit("an earlier submission was interrupted before its batch id was "
                             "written; check the console, then --adopt BATCH_ID or --forget-intent")

    if args.simulate:
        results, by_doc = run_round(docs, data, cache, cfg, state.exhausted(), standin_for(cache))
        rounds = collections.defaultdict(list)
        for reqs in by_doc.values():
            for i, r in enumerate(reqs, start=1):
                rounds[i].append(r)
        log("SIMULATED: every miss answered by the eval's cached read of the same step "
            "(STANDIN_ARM), or an all-null answer where it has none. No API call.")
        total = Decimal(0)
        for i in sorted(rounds):
            total += _print_estimate(f"round {i}", rounds[i], outs, log)
        log(f"all rounds ~${total:.4f}; spent so far ${state.spent():.4f}; cap ${cap}")
        _write_run(run, results, simulated=True)
        log(f"run dir: {run}")
        return 0

    client = None
    if state.uncollected() and not offline:
        client = api_client(env_root)
        log(f"collecting {len(state.uncollected())} batch(es) an earlier run submitted")
        collect(state, cache, client, log)
    for round_no in range(1, MAX_ROUNDS + 1):
        results, misses = run_round(docs, data, cache, cfg, state.exhausted())
        reqs = list(misses.values())
        if not reqs:
            log(f"round {round_no}: nothing misses; {len(results)} documents read")
            _write_run(run, results, simulated=False)
            log(f"spent ${state.spent():.4f} in all; run dir: {run}")
            return finish(run)
        if args.dry_run:
            pending = [k for b in state.uncollected() for k in
                       (m["key"] for m in state.data["batches"][b]["requests"].values())]
            if pending:
                log(f"note: {len(pending)} requests sit in batches not yet collected")
            _print_estimate(f"DRY RUN, round {round_no} (no API call)", reqs, outs, log)
            log(f"spent so far ${state.spent():.4f}; cap ${cap}")
            return 0
        client = client or api_client(env_root)
        if submit(state, client, reqs, outs, cap, round_no, log) is None:
            return 2
        collect(state, cache, client, log)
    log(f"STOP: still missing after {MAX_ROUNDS} rounds")
    return 2


def _write_run(run: pathlib.Path, results: dict, simulated: bool) -> None:
    run.mkdir(parents=True, exist_ok=True)
    _write_json(run / "results.json", {"simulated": simulated, "ladder": str(REPO),
                                       "written_at": _now(), "docs": results})


# --------------------------------------------------------------------------- #
# report: the baselines
# --------------------------------------------------------------------------- #
GATE_FIELDS = tuple(gate.REQUIRED_FIELDS)
ARMS = ("new", "today", "today_asked", "sonnet")
BLOCKER_FIELDS = GATE_FIELDS + ("ref_list", "basic_udi_di")
SHOWN_FIELDS = ("type", "regulation", "coverage_scope", "validity_from", "validity_to",
                "cert_number", "ref_list", "basic_udi_di", "manufacturer")


def _fields_of(entry: dict | None, tier: str) -> dict:
    if not entry:
        return {}
    msg = anthropic.types.Message.model_validate(entry["message"])
    return llm_mod._fields_from_json(llm_mod._response_json(msg), tier, entry["model"])


def today_text(d: dict, cache: Cache, probe_open: dict,
               asked_only: bool = False) -> tuple[dict | None, collections.Counter]:
    """Today's ladder on a text document: T0 and Haiku 4.5 (`h45`, the request
    built from the plan's T0), later-wins as before 2026-10-12, then Sonnet's
    image read (`s55v`) for the fields that still escalated (`_sonnet_over`).
    None when today's doc-class call (Haiku 4.5, cached) refused the file."""
    h, plan = d["content_hash"], d["plan"]
    if plan["doc_class"] == "unknown":
        c = cache.arm(h, "h45", "classify")
        if c and llm_mod._classify_answer(anthropic.types.Message.model_validate(c["message"]),
                                          "doc-class")[0] == tiers.NOT_A_DOCUMENT:
            return None, collections.Counter()
    t1 = _fields_of(cache.arm(h, "h45"), "T1")
    fields = tiers.merge(plan["t0"], {f: t1[f] for f in plan["missing"] if f in t1},
                         by_strength=False)
    return _sonnet_over(fields, tiers.needs_escalation(fields), h, cache, probe_open, asked_only)


def today_scan(d: dict, cache: Cache, probe_open: dict,
               asked_only: bool = False) -> tuple[dict | None, collections.Counter]:
    """Today on a scan: T0, then Sonnet's image read on what T0 left open (S-0
    less its T1 on empty text). None when T0 could not type it: today's text
    doc-class call finds no text and refuses it (research doc section 6)."""
    plan = d["plan"]
    if plan.get("t0_class") == "unknown":
        return None, collections.Counter()
    t0 = {f: v for f, v in plan["t0"].items() if isinstance(v, dict)}
    return _sonnet_over(t0, plan["open"], d["content_hash"], cache, probe_open, asked_only)


def _sonnet_answers(h: str, needed, cache: Cache, probe_open: dict,
                    asked_only: bool) -> tuple[dict, collections.Counter]:
    """Sonnet's cached image read (`s55v`) for the fields `needed`. The probe
    asked it the fields open after Haiku 5.5's T1, not today's or the new
    ladder's list. Its response carries every field (the schema requires
    them), mostly null at 0 ("not requested") for one it was not asked, but
    a value at times: unless `asked_only`, such a volunteered VALUE is taken
    (its null is not an answer). Counted: `asked`, `unasked` (a volunteered
    value), `none` (no answer: the arm keeps what it had)."""
    sv = _fields_of(cache.arm(h, "s55v"), "T2")
    asked = set(probe_open.get(h, []))
    out, cov = {}, collections.Counter()
    for f in needed:
        if f in sv and f in asked:
            out[f] = sv[f]
            cov["asked"] += 1
        elif f in sv and not asked_only and hc.norm(f, sv[f].get("value")) is not None:
            out[f] = sv[f]
            cov["unasked"] += 1
        else:
            cov["none"] += 1
            cov[f"none:{f}"] += 1
    return out, cov


def _sonnet_over(fields, escalated, h, cache, probe_open, asked_only):
    add, cov = _sonnet_answers(h, escalated, cache, probe_open, asked_only)
    return tiers.merge(fields, add, by_strength=False), cov


def sonnet_every_open(d: dict, new: dict | None, cache: Cache,
                      probe_open: dict) -> tuple[dict | None, collections.Counter]:
    """The new ladder with Sonnet reading every field still open after the
    deterministic steps (the fields its first image read was asked): Sonnet's
    answer, null included, closes each (T-1 / S-1)."""
    if new is None:
        return None, collections.Counter()
    first = next((e for e in new.get(tiers.READS_KEY) or [] if e.get("step") == "T2-first"), None)
    add, cov = _sonnet_answers(d["content_hash"], list((first or {}).get("fields") or {}),
                               cache, probe_open, asked_only=False)
    return {**new, **add}, cov


def disposition(fields: dict | None, cfg) -> dict:
    """GATE's confidence test with its own `_score` (identity calibration, as the
    server runs) and thresholds; `held` also applies GATE's evidence check
    (`_require_complete_evidence`: an empty quote, or a T1/T2 gate field with no
    page, which is the blocking `evidence-page-missing`). The REF gate and
    VALIDATE's flags need the catalogue and are not simulated."""
    if fields is None:
        return {"conf": "terminal", "held": "terminal", "score": None, "why": "refused"}
    if any(hc.norm(f, (fields.get(f) or {}).get("value")) is None for f in GATE_FIELDS):
        return {"conf": "manual", "held": "manual", "score": None, "why": "a gate field is empty"}
    score = gate._score(fields, {}, cfg.agreement_min)
    conf = "production" if score >= cfg.high else "staged" if score >= cfg.med else "manual"
    try:
        flags = gate._require_complete_evidence(fields)
        why = ",".join(flags)
    except ValueError as exc:
        flags, why = ["evidence-incomplete"], str(exc)
    held = "manual" if flags else conf
    return {"conf": conf, "held": held, "score": score, "why": why}


def _gt(d: dict) -> dict | None:
    return hc.corrected_gt(d["path"], d["gt"])


def _correct(f: str, fields: dict | None, gt: dict) -> bool | None:
    v = (fields or {}).get(f) or {}
    return hc._correct(f, v.get("value"), hc._gt_value(f, gt))


def _short(v) -> str:
    if isinstance(v, list):
        return f"[{len(v)}] " + ", ".join(map(str, v[:4])) + (" ..." if len(v) > 4 else "")
    return "null" if v is None else str(v)


def _ev_line(ev: dict | None) -> str:
    if not ev:
        return "-"
    step = ev.get("step") or ev.get("tier") or ""
    q = " ".join(str(ev.get("verbatim") or "").split())[:140]
    agreed = f", agreed {ev['agreed_conf']}" if ev.get("agreed_conf") is not None else ""
    return (f"{_short(ev.get('value'))} ({step} {ev.get('conf')}{agreed}, p{ev.get('page')}) "
            f"\"{q}\"")


def _udi_note(f: str, ev: dict | None) -> str:
    v = (ev or {}).get("value")
    if f != "basic_udi_di" or not v:
        return ""
    return f" [check pair {validate_basic_udi(v).status}]"


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def cmd_report(args) -> int:
    data = pathlib.Path(args.data) if getattr(args, "data", None) else main_checkout()
    run = pathlib.Path(args.run)
    res = json.loads((run / "results.json").read_text())
    docs, probe_open = load_docs(data)
    cache = Cache(data / BASE_CACHE, data / OUT / "cache")
    state = State(data / OUT / "state.json")
    cfg = config()
    rows = []
    for d in docs:
        r = res["docs"].get(d["content_hash"])
        if r is None or r.get("error"):
            rows.append({"doc": d, "missing": True, "error": (r or {}).get("error", "not run")})
            continue
        new = None if r["doc_class"] == tiers.NOT_A_DOCUMENT else r["fields"]
        baseline = today_text if d["set"] == "text" else today_scan
        today, today_cov = baseline(d, cache, probe_open)
        today_asked, _ = baseline(d, cache, probe_open, asked_only=True)
        son, son_cov = sonnet_every_open(d, new, cache, probe_open)
        rows.append({"doc": d, "res": r, "gt": _gt(d), "new": new, "today": today,
                     "today_asked": today_asked, "sonnet": son,
                     "today_cov": today_cov, "sonnet_cov": son_cov,
                     "d_new": disposition(new, cfg), "d_today": disposition(today, cfg),
                     "name": pathlib.Path(d["path"]).name})
    done = [x for x in rows if not x.get("missing")]
    L = [f"# Extraction ladder, Task 8: quality and parity on the evaluation set", ""]
    if res.get("simulated"):
        L += ["**SIMULATED RUN: misses were answered by stand-ins from the eval cache, not by "
              "real reads. These numbers test the report, they measure nothing.**", ""]
    L += [f"Run `{run.name}`, ladder from `{res.get('ladder')}`, written {res.get('written_at')}. "
          f"{len(done)} of {len(rows)} documents read"
          + (f"; not read: {[(x['doc']['path'], x['error']) for x in rows if x.get('missing')]}"
             if len(done) < len(rows) else "") + ".", ""]
    L += ["Arms. **new**: the built ladder, real reads (cached or paid by this run). **today**: "
          "T0 + Haiku 4.5 (`h45`) + Sonnet image (`s55v`) on what then still escalated, later-wins "
          "merge; on scans T0 + Sonnet image, and a scan T0 cannot type is refused. **today (asked)**: "
          "the same, Sonnet's answer taken only for a field its cached read was asked. **sonnet**: the "
          "new ladder with Sonnet's image read (`s55v`) closing every field its first image read was "
          "asked (T-1 / S-1). Labels: `tests/fixtures/corpus_manifest.py` with "
          "`haiku_compare.LABEL_OVERRIDES`; `validity_from` is not scored.", ""]
    cov_t = sum((x["today_cov"] for x in done if x["gt"]), collections.Counter())
    cov_s = sum((x["sonnet_cov"] for x in done if x["gt"]), collections.Counter())
    L += ["The cached Sonnet read (`s55v`) was asked the fields open after Haiku 5.5's T1 on "
          "2026-10-08, not today's list nor the new ladder's; it answers every field. On the "
          f"labelled documents, today's escalated fields: {cov_t['asked']} answered as asked, "
          f"{cov_t['unasked']} a value the read gave unasked, {cov_t['none']} no Sonnet answer "
          f"(Haiku 4.5's value kept). Sonnet arm: {cov_s['asked']} asked, {cov_s['unasked']} "
          f"unasked values, {cov_s['none']} no Sonnet answer (the new value kept).",
          "", "No Sonnet answer, by field: today "
          + str({k[5:]: v for k, v in sorted(cov_t.items()) if k.startswith("none:")})
          + "; sonnet " + str({k[5:]: v for k, v in sorted(cov_s.items()) if k.startswith("none:")})
          + ". Where today's arm has none, it is Haiku 4.5's answer, so the comparison with today "
          "is approximate on those fields.", ""]

    blockers, notes = [], []
    # ---- quality
    L += ["## 1. Quality per field, labelled documents", ""]
    for set_name in ("text", "scan"):
        lab = [x for x in done if x["doc"]["set"] == set_name and x["gt"]]
        L += [f"### {set_name} ({len(lab)} labelled)", "",
              "| field | new | today | today (asked) | sonnet |", "|---|---|---|---|---|"]
        tot = collections.Counter()
        for f in hc.FIELDS:
            cells, counts = [], {}
            for arm in ARMS:
                cs = [c for x in lab if (c := _correct(f, x[arm], x["gt"])) is not None]
                counts[arm] = (sum(cs), len(cs))
                cells.append(hc._pct(sum(cs), len(cs)))
                tot[(arm, "ok")] += sum(cs)
                tot[(arm, "n")] += len(cs)
            if counts["new"][1]:
                L.append(f"| {f} | " + " | ".join(cells) + " |")
            for base in ("today", "today_asked"):
                if f in BLOCKER_FIELDS and counts["new"][0] < counts[base][0]:
                    blockers.append(f"{set_name}: `{f}` {counts['new'][0]}/{counts['new'][1]} right, "
                                    f"{base} {counts[base][0]}/{counts[base][1]}")
        L.append("| all scored | " + " | ".join(hc._pct(tot[(a, "ok")], tot[(a, "n")])
                                               for a in ARMS) + " |")
        L.append("")

    L += ["### Fields that end differently, labelled documents", "",
          "Every shown field where the new ladder's value differs from today's or Sonnet's "
          "(normalized as scored). Right/wrong against the label where it is scored.", ""]
    diff_rows = []
    for x in [x for x in done if x["gt"]]:
        for f in SHOWN_FIELDS:
            nv = (x["new"] or {}).get(f)
            for arm in ("today", "sonnet"):
                ov = (x[arm] or {}).get(f)
                if hc.norm(f, (nv or {}).get("value")) == hc.norm(f, (ov or {}).get("value")):
                    continue
                gt = hc._gt_value(f, x["gt"])
                diff_rows.append({
                    "doc": x["name"], "set": x["doc"]["set"], "field": f, "vs": arm,
                    "label": "-" if gt is hc.SKIP else _short(gt),
                    "new_ok": _correct(f, x["new"], x["gt"]), "other_ok": _correct(f, x[arm], x["gt"]),
                    "new": _ev_line(nv) + _udi_note(f, nv), "other": _ev_line(ov) + _udi_note(f, ov)})
    L += _diff_table(diff_rows)

    L += ["### Fields that end differently from Sonnet, every document", "",
          "New ladder vs Sonnet closing every open field, labelled or not (section 15 counted 5 on "
          "text, 4 on scans).", ""]
    all_rows = []
    for x in done:
        for f in SHOWN_FIELDS:
            nv, sv = (x["new"] or {}).get(f), (x["sonnet"] or {}).get(f)
            if hc.norm(f, (nv or {}).get("value")) != hc.norm(f, (sv or {}).get("value")):
                gt = hc._gt_value(f, x["gt"]) if x["gt"] else hc.SKIP
                all_rows.append({"doc": x["name"], "set": x["doc"]["set"], "field": f, "vs": "sonnet",
                                 "label": "-" if gt is hc.SKIP else _short(gt),
                                 "new_ok": _correct(f, x["new"], x["gt"]) if x["gt"] else None,
                                 "other_ok": _correct(f, x["sonnet"], x["gt"]) if x["gt"] else None,
                                 "new": _ev_line(nv) + _udi_note(f, nv),
                                 "other": _ev_line(sv) + _udi_note(f, sv)})
    for s in ("text", "scan"):
        L.append(f"- {s}: {sum(r['set'] == s for r in all_rows)} fields on "
                 f"{len({r['doc'] for r in all_rows if r['set'] == s})} documents")
    L.append("")
    L += _diff_table(all_rows)
    hc._write_csv(run / "differences.csv", diff_rows + all_rows,
                  ["doc", "set", "field", "vs", "label", "new_ok", "other_ok", "new", "other"])

    # ---- routing and gate
    L += ["## 2. Routing and gate", "",
          "Gate: GATE's `_score` and thresholds "
          f"({cfg.high} / {cfg.med}, agreement_min {cfg.agreement_min}), identity calibration. "
          "`held` also applies GATE's evidence check (`evidence-page-missing` blocks). The REF gate "
          "and VALIDATE flags are not simulated, so production here is an upper bound on "
          "production + filed (section 12's correction).", ""]
    measured = {}
    for set_name in ("text", "scan"):
        xs = [x for x in done if x["doc"]["set"] == set_name]
        n = len(xs)
        used = collections.Counter(lab for x in xs for lab in set(x["res"]["used"]))
        dn = collections.Counter(x["d_new"]["conf"] for x in xs)
        dh = collections.Counter(x["d_new"]["held"] for x in xs)
        dt = collections.Counter(x["d_today"]["conf"] for x in xs)
        lab = [x for x in xs if x["gt"]]
        wrong = [(x["name"], [f for f in GATE_FIELDS if _correct(f, x["new"], x["gt"]) is False])
                 for x in lab if x["d_new"]["conf"] == "production"]
        wrong = [w for w in wrong if w[1]]
        wrong_any = [(x["name"], [f for f in GATE_FIELDS if _correct(f, x["new"], x["gt"]) is False])
                     for x in lab]
        wrong_any = [w for w in wrong_any if w[1]]
        dis = [dd for x in xs for dd in x["res"]["disagreements"]]
        failed = [(x["name"], x["res"]["failed_reads"]) for x in xs if x["res"]["failed_reads"]]
        unread = sum(bool(x["new"] and x["new"].get(tiers.PAGES_UNREAD_KEY)) for x in xs)
        second = "T1-second" if set_name == "text" else "T2-second"
        sec_fields = collections.Counter(
            f for x in xs for e in (x["new"] or {}).get(tiers.READS_KEY) or []
            if e.get("step") == second for f in e.get("fields") or {})
        measured[set_name] = {"n": n, "sonnet": used["T2"], "prod": dn["production"],
                              "today_prod": dt["production"], "wrong": wrong}
        L += [f"### {set_name} ({n} documents, {len(lab)} labelled)", "",
              "| | new | today |", "|---|---|---|",
              f"| production (confidence test) | {hc._pct(dn['production'], n)} | "
              f"{hc._pct(dt['production'], n)} |",
              f"| production, evidence check applied | {hc._pct(dh['production'], n)} | |",
              f"| staged | {dn['staged']} | {dt['staged']} |",
              f"| manual | {dn['manual']} | {dt['manual']} |",
              f"| refused (not a compliance document) | {dn['terminal']} | {dt['terminal']} |",
              f"| labelled in production with a wrong gate field | {len(wrong)} of {len(lab)} | "
              f"{sum(1 for x in lab if x['d_today']['conf'] == 'production' and any(_correct(f, x['today'], x['gt']) is False for f in GATE_FIELDS))} of {len(lab)} |",
              "",
              "| reads | documents |", "|---|---|"]
        L += [f"| {label} | {used[label]} |" for label in
              ("doc-class", "doc-class-image", "T1", "T2-first", "T2-second", "T1-second", "T2")
              if used[label]]
        L += ["",
              f"- Second read ({second}) fields: {dict(sec_fields) or 'none'}",
              f"- Disagreements: {len(dis)} fields on {sum(bool(x['res']['disagreements']) for x in xs)} "
              f"documents; by field {dict(collections.Counter(dd['field'] for dd in dis))}",
              f"- Failed reads: {failed or 'none'}",
              f"- Fields left unread (`pages-unread`): {unread} documents",
              f"- Labelled with a wrong gate field, any disposition: {wrong_any or 'none'}",
              f"- Labelled in production with a wrong gate field: {wrong or 'none'}", ""]
        held = [x for x in xs if x["d_new"]["conf"] != "production"]
        if held:
            L += ["Held from production (new):", ""]
            for x in held:
                g = {f: (_short(((x['new'] or {}).get(f) or {}).get('value')),
                         ((x['new'] or {}).get(f) or {}).get('step') or ((x['new'] or {}).get(f) or {}).get('tier'),
                         ((x['new'] or {}).get(f) or {}).get('conf'),
                         ((x['new'] or {}).get(f) or {}).get('agreed_conf'),
                         ((x['new'] or {}).get(f) or {}).get('proven'))
                     for f in GATE_FIELDS}
                L.append(f"- {x['name']}: {x['d_new']['conf']} "
                         f"{'' if x['d_new']['score'] is None else round(x['d_new']['score'], 3)} {g}")
            L.append("")
        if wrong and set_name == "text":
            blockers.append(f"text: labelled documents in production with a wrong gate field: {wrong}")
        elif wrong:
            # The plan's rule names the 19 labelled text documents; on the 4 labelled
            # scans it measured 1 wrong, "likely a label error" not checked on the page.
            notes.append(f"scan: labelled in production with a wrong gate field (not a blocker "
                         f"by the plan's wording; 1 of 4 expected): {wrong}")
        if dn["production"] < dt["production"]:
            blockers.append(f"{set_name}: production {dn['production']} of {n}, today "
                            f"{dt['production']} of {n} (same scorer)")

    t, s = measured.get("text", {}), measured.get("scan", {})
    L += ["### Against section 15 and the plan's targets", "",
          "| measure | target | measured |", "|---|---|---|",
          f"| text documents reaching Sonnet | 7 +/- 1 of 79 (7-10 if the image read disagrees on a "
          f"both-citations regulation) | {t.get('sonnet')} of {t.get('n')} |",
          f"| text gate, production | about 82-84% (Task 5: 84% +/- 2 points); today 73% simulated | "
          f"{hc._pct(t.get('prod', 0), t.get('n', 0))} |",
          f"| text, labelled in production with a wrong gate field | 0 of 19 | "
          f"{len(t.get('wrong', []))} of 19 |",
          f"| scan gate, production | 24 +/- 1 of 34; today 1 of 34 | {s.get('prod')} of {s.get('n')} |",
          f"| scans reaching Sonnet | 19 measured; up to 27 allowed (Denis, after Task 3) | "
          f"{s.get('sonnet')} of {s.get('n')} |",
          f"| scans, labelled in production with a wrong gate field | 1 of 4 measured, likely a "
          f"label error | {len(s.get('wrong', []))} of 4 |", ""]

    # ---- cost
    spend = state.spent()
    per = collections.defaultdict(list)
    for x in done:
        c = Decimal(0)
        for sv in x["res"]["served"]:
            if sv.get("usage"):
                c += economics.cost_usd(sv["model"], economics.Usage(
                    input_tokens=sv["usage"].get("input_tokens") or 0,
                    output_tokens=sv["usage"].get("output_tokens") or 0,
                    cache_read_tokens=sv["usage"].get("cache_read_input_tokens") or 0,
                    cache_creation_tokens=sv["usage"].get("cache_creation_input_tokens") or 0),
                    batch=True)
        per[x["doc"]["set"]].append(c)
    L += ["## 3. Cost", "",
          f"- This harness's batches: ${spend:.4f} (measured; state.json lists each batch)",
          "- The ladder per document at Batch API prices, every read it made (cached or paid): "
          + "; ".join(f"{k} ${statistics.mean(v):.5f}" for k, v in per.items() if v), ""]

    # ---- blockers
    L += ["## 4. Deploy blockers (plan, Task 8)", ""]
    L += [f"- **BLOCKER** {b}" for b in blockers] or ["- none"]
    L += [f"- note: {n}" for n in notes]
    L += ["", "Checked: each of `type`, `regulation`, `coverage_scope`, `ref_list`, `basic_udi_di` "
          "no less accurate than either today arm on the labelled documents (text and scans "
          "apart); no labelled text document in production with a wrong gate field (the plan's "
          "19); production no lower than today's on the same scorer.", ""]
    text = "\n".join(L)
    (run / "report.md").write_text(text)
    print(text)
    return 0


def _diff_table(rows: list[dict]) -> list[str]:
    if not rows:
        return ["None.", ""]
    mark = {True: "right", False: "wrong", None: "-"}
    out = ["| doc | field | vs | label | new | other |", "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['doc'][:40]} | {r['field']} | {r['vs']} | {r['label'][:30]} | "
                   f"{mark[r['new_ok']]}: {r['new'].replace('|', '/')} | "
                   f"{mark[r['other_ok']]}: {r['other'].replace('|', '/')} |")
    return out + [""]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.ladder_quality")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--run", help="output folder (default eval/quality-check/<timestamp>)")
    p.add_argument("--data", help="the checkout holding eval/ and imports/ (default: the main one)")
    p.add_argument("--cap-usd", type=float, default=float(CAP_USD))
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="round 1's misses and cost; no API")
    mode.add_argument("--simulate", action="store_true",
                      help="every round, misses answered from the eval cache; no API")
    p.add_argument("--adopt", metavar="BATCH_ID", help="the batch an interrupted submission created")
    p.add_argument("--forget-intent", action="store_true",
                   help="an interrupted submission created no batch")
    p = sub.add_parser("report")
    p.add_argument("--run", required=True)
    p.add_argument("--data")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
