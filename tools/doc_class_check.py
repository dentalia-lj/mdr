"""The text doc-class call, measured: Haiku 5.5 thinking off (as built), Haiku 5.5
with adaptive thinking, and Haiku 4.5 (as production runs it). An evaluation, not a
pipeline change. Task 9 regressions, item 2 (2026-10-13): docs 503 (MIYO
processing instructions) and 1130 (RAYFace user manual) were refused by Haiku 5.5
where production's Haiku 4.5 had accepted them, and a refusal is terminal.

The requests are the pipeline's own (`llm._classify_text_request`,
`llm._classify_image_request`); an arm changes only the model, the thinking setting,
`max_tokens` and, for a prompt candidate, the system text. The file name sent is the
`archive_url` the document was extracted under, as the extract handler sends it,
so arm `c` is production's request byte for byte.

The documents (`select`): files T0 leaves `unknown` that reach the TEXT doc-class
call under this checkout's routing (a scanned page among pages 1-2 goes to the
image call since item 1 of the same day), from two sources:

    prod    every text document production sent through the doc-class call
            (server `job` rows, read-only; files rsync'd from /srv/compliance/archive)
    local   web-fetched files the local dev database sent through it (fetch
            history; files read from the worker's /archive volume)

Labels: a production document typed DoC / EC / ISO / IFU / SPP and not rejected is a
positive; everything else is labelled by hand in `labels.json` (filename and the
page 1-2 text, `snippets.txt`). The `img` arm is the image doc-class call on the
files whose route item 1 changed, to see it refuses nothing the text call accepted.

Money: Batch API, the Task 8 harness's cache, state and collect
(`tools.ladder_quality`), one cap over every round (`--cap-usd`, $0.40 approved by
Denis 2026-10-13) checked against measured spend plus the round's estimate.

    python -m tools.doc_class_check select --run DIR
    python -m tools.doc_class_check run --run DIR --arms a,b [--set text|img] [--dry-run]
    python -m tools.doc_class_check report --run DIR
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime
import fcntl
import json
import pathlib
import random
from dataclasses import dataclass
from decimal import Decimal

from tools import haiku_compare as hc   # puts this checkout first on sys.path
from tools import ladder_quality as lq

import anthropic  # noqa: E402

from app.config import Models  # noqa: E402
from app.extract import economics  # noqa: E402
from app.extract import llm as llm_mod  # noqa: E402
from app.extract import pdf as pdfutil  # noqa: E402

OUT = pathlib.Path("eval/doc-class-check")
CAP_USD = Decimal("0.40")
POSITIVE_TYPES = ("DoC", "EC", "ISO", "IFU", "SPP")
ADAPTIVE = {"type": "adaptive"}
SEED = 20261013
LOCAL_TEXT_ACCEPTED = 110
LOCAL_IMG_EACH = 20
#: Pages 1-2 longer than this are left out (fold-out leaflets of 90k-250k characters,
#: 6 of 309): they cost as much as the rest together, at Haiku 5.5's long-prompt price.
MAX_HEADER_CHARS = 40_000


@dataclass(frozen=True)
class Arm:
    label: str                      # batch custom id prefix and cache label: hyphens only
    model: str
    thinking: dict | None           # None: no thinking key (the API default)
    max_tokens: int
    kind: str = "text"              # text: the `doc-class` request; image: `doc-class-image`
    prompt: str | None = None       # a PROMPT_VARIANTS key
    out_estimate: int | None = None  # output tokens to estimate with; None: max_tokens


#: The IFU candidate (item 2): the IFU class names what IFUs are titled, and the
#: manual bullet stops refusing a device's own instructions. The other exclusions,
#: and "when torn, refuse", stay as written.
IFU_LINE = (
    "- `IFU` — instructions for use accompanying a product.",
    "- `IFU` — instructions for use accompanying a product or a product family,\n"
    "  whatever it is titled: instructions for use, user manual, operating or\n"
    "  operation instructions, processing instructions, reprocessing instructions\n"
    "  (Gebrauchsanweisung, Bedienungsanleitung, Verarbeitungsanleitung, navodila za\n"
    "  uporabo).",
)
MANUAL_BULLET = (
    "- **An operation manual or user guide.** These are long, have chapters and a\n"
    "  table of contents, and legitimately cite MDR 2017/745 somewhere. Citing a\n"
    "  regulation does not make a manual a declaration. An IFU is short and\n"
    "  accompanies one product or family; a 300-page service manual is not an IFU.",
    "- **A service, repair or installation manual for technicians, or an archive of\n"
    "  many products' manuals.** These legitimately cite MDR 2017/745 somewhere.\n"
    "  Citing a regulation does not make a manual a declaration. A device's own\n"
    "  user manual or processing instructions is its `IFU`, however long.",
)
PROMPT_VARIANTS = {"ifu": [IFU_LINE, MANUAL_BULLET], "ifu-line": [IFU_LINE]}
#: The doc-class prompt as it stood before the IFU lines were adopted
#: (2026-10-13): the arms measured on it put the old text back, so their
#: requests keep the keys they were answered under.
PROMPT_VARIANTS["pre-ifu"] = [(new, old) for old, new in PROMPT_VARIANTS["ifu"]]

ARMS = {
    # as built: `llm.READ_STEPS["doc-class"]`, models.t1, thinking off, 256 tokens
    "a": Arm("dc-a", "claude-haiku-5-5", llm_mod.THINKING_OFF, llm_mod.DOC_CLASS_MAX_TOKENS,
             prompt="pre-ifu"),
    "b": Arm("dc-b", "claude-haiku-5-5", ADAPTIVE, 8192, prompt="pre-ifu", out_estimate=700),
    # production today: Haiku 4.5, no thinking key, 256 tokens
    "c": Arm("dc-c", "claude-haiku-4-5", None, llm_mod.DOC_CLASS_MAX_TOKENS, prompt="pre-ifu"),
    # the image doc-class call as built (`doc-class-image`), for item 1's route change
    "img": Arm("dc-img", "claude-haiku-5-5", llm_mod.THINKING_OFF,
               llm_mod.DOC_CLASS_MAX_TOKENS, kind="image", prompt="pre-ifu"),
    # fix candidates
    "a-ifu": Arm("dc-a-ifu", "claude-haiku-5-5", llm_mod.THINKING_OFF,
                 llm_mod.DOC_CLASS_MAX_TOKENS, prompt="ifu"),
    "b-ifu": Arm("dc-b-ifu", "claude-haiku-5-5", ADAPTIVE, 8192, prompt="ifu",
                 out_estimate=700),
    "img-ifu": Arm("dc-img-ifu", "claude-haiku-5-5", llm_mod.THINKING_OFF,
                   llm_mod.DOC_CLASS_MAX_TOKENS, kind="image", prompt="ifu"),
    "a-ifu-line": Arm("dc-a-ifu-line", "claude-haiku-5-5", llm_mod.THINKING_OFF,
                      llm_mod.DOC_CLASS_MAX_TOKENS, prompt="ifu-line"),
}


def system_for(arm: Arm, base: str) -> str:
    """The arm's system text: the pipeline's, with the variant's edits. An edit
    the prompt already carries applies as is; any other mismatch stops."""
    out = base
    for old, new in PROMPT_VARIANTS.get(arm.prompt, []):
        if out.count(old) == 1:
            out = out.replace(old, new)
        elif out.count(new) != 1:
            raise SystemExit(f"prompt variant {arm.prompt!r}: edit does not match once")
    return out


def build_params(arm: Arm, doc, filename: str) -> dict | None:
    """The pipeline's doc-class request with the arm's model, thinking and tokens."""
    read = llm_mod.Read("doc-class" if arm.kind == "text" else "doc-class-image",
                        arm.kind, arm.model, arm.thinking)
    if arm.kind == "text":
        params = llm_mod._classify_text_request(read, doc, filename)
        if params is None:
            return None
    else:
        params = llm_mod._classify_image_request(read, doc, filename)
    params["max_tokens"] = arm.max_tokens
    if arm.prompt:
        params["system"] = system_for(arm, params["system"])
    return params


def request_record(d: dict, arm: Arm, params: dict) -> dict:
    blocks = lq._blocks(params)
    images = [lq._png_px(b) for b in blocks if b.get("type") == "image"]
    key = hc.request_key(params)
    return {"content_hash": d["content_hash"], "path": d["path"], "label": arm.label,
            "key": key, "kind": "classify", "model": params["model"],
            "class": (params["model"], f"classify-{arm.label}", arm.thinking is None),
            "asked": [], "max_tokens": params["max_tokens"],
            "out_estimate": arm.out_estimate or params["max_tokens"],
            "system_chars": len(params["system"]),
            "text_chars": sum(len(json.dumps(b.get("text", ""), ensure_ascii=False))
                              for b in blocks if b.get("type") == "text")
            + len(json.dumps(params.get("output_config"), ensure_ascii=False)),
            "image_px": images, "params": params}


def estimate_usd(req: dict) -> Decimal:
    usage = economics.Usage(
        input_tokens=int((req["text_chars"] + req["system_chars"]) / lq.CHARS_PER_TOKEN
                         + sum(req["image_px"]) / lq.IMAGE_PX_PER_TOKEN * lq.IMAGE_MARGIN),
        output_tokens=int(req["out_estimate"]))
    return economics.cost_usd(req["model"], usage, batch=True)


# --------------------------------------------------------------------------- #
# select
# --------------------------------------------------------------------------- #
def _rows(path: pathlib.Path) -> list[list[str]]:
    return [ln.rstrip("\n").split("|") for ln in path.read_text().splitlines()
            if ln.count("|") >= 3]


def route(r: dict) -> str:
    """The doc-class call this checkout makes for a scanned file record."""
    if r.get("t0_class") != "unknown":
        return "t0:" + str(r.get("t0_class"))
    if not r.get("has_text_page"):
        return "scan"
    if r["dc_text_empty"] or "image" in r["classes12"]:
        return "image"
    return "text"


def old_route(r: dict) -> str:
    """The route before item 1: the image call only for pages 1-2 with no text."""
    out = route(r)
    if out == "image" and not r["dc_text_empty"]:
        return "text"
    return out


def cmd_select(args) -> int:
    """selection.json from the scans and the exports in the run folder (see the
    module docstring); snippets.txt for labelling. No API, no database."""
    run = pathlib.Path(args.run)
    scans = {}
    for name, root in (("prod-scan.jsonl", "pdfs"), ("mirror-scan.jsonl", None),
                       ("local-scan.jsonl", "local-pdfs")):
        p = run / name
        if not p.exists():
            continue
        for ln in p.read_text().splitlines():
            r = json.loads(ln)
            base = (run / root) if root else (lq.main_checkout() / "imports/dentalia-sftp")
            r["file"] = str(base / r["path"])
            scans.setdefault(r["sha256"], r)
    registry = {r[0]: {"doc_id": r[1], "type": r[2] or None, "status": r[3]}
                for r in _rows(run / "prod-documents.txt")}
    out = []
    for r in _rows(run / "prod-doc-class-candidates.txt"):
        h, dc, tiers, url = r[:4]
        s = scans.get(h)
        if s is None:
            continue
        doc = registry.get(h)
        out.append({"content_hash": h, "source": "prod", "path": s["file"], "filename": url,
                    "recorded": {"compliance-doc": "accepted",
                                 "not-a-compliance-document": "refused"}.get(dc, dc),
                    "registry": doc, "route": route(s), "old_route": old_route(s),
                    "t0_type": s.get("t0_type"), "t0_type_conf": s.get("t0_type_conf"),
                    "pages": s.get("pages")})
    seen = {d["content_hash"] for d in out}
    for r in _rows(run / "local-candidates.txt"):
        h, dc, tiers, url, src, typ, status = (r + [None] * 7)[:7]
        s = scans.get(h)
        if s is None or h in seen:
            continue
        seen.add(h)
        out.append({"content_hash": h, "source": "local", "path": s["file"], "filename": url,
                    "recorded": {"compliance-doc": "accepted",
                                 "not-a-compliance-document": "refused"}.get(dc, dc),
                    "local_type": typ or None, "source_url": src,
                    "route": route(s), "old_route": old_route(s),
                    "t0_type": s.get("t0_type"), "t0_type_conf": s.get("t0_type_conf"),
                    "pages": s.get("pages")})
    # Every production document; of the local ones (fetch history), every refusal and
    # a seeded sample of the acceptances for the text call, and 20 + 20 for the image
    # call: about 300 and 50, each hand-labelled where production has no type.
    rng = random.Random(SEED)
    for d in out:
        d["sets"] = []
        if d["route"] in ("text", "image"):
            with pdfutil.open_doc(d["path"]) as pdf:
                d["header_chars"] = len(_header_text(pdf))
    for which, take in (("text", {"refused": None, "accepted": LOCAL_TEXT_ACCEPTED}),
                        ("img", {"refused": LOCAL_IMG_EACH, "accepted": LOCAL_IMG_EACH})):
        for d in out:
            if d["source"] == "prod" and _in_set(d, which):
                d["sets"].append(which)
        for rec, n in take.items():
            pool = [d for d in out if d["source"] == "local" and d["recorded"] == rec
                    and _in_set(d, which)]
            for d in (pool if n is None else rng.sample(pool, min(n, len(pool)))):
                d["sets"].append(which)
    lq._write_json(run / "selection.json", out)
    with open(run / "snippets.txt", "w") as fh:
        for d in out:
            if not d["sets"]:
                continue
            with pdfutil.open_doc(d["path"]) as pdf:
                text = " ".join(_header_text(pdf).split())[:400]
            fh.write(f"{d['content_hash'][:12]} [{','.join(d['sets'])} {d['source']} "
                     f"{d['recorded']} {(d.get('registry') or {}).get('type') or d.get('local_type')} "
                     f"p={d['pages']}] {pathlib.Path(d['filename']).name}\n    {text}\n")
    print(json.dumps(collections.Counter((d["source"], d["route"], d["recorded"])
                                         for d in out).most_common(), default=str))
    return 0


def _header_text(pdf) -> str:
    """`llm.doc_class_text` without reading every page of a long manual."""
    return "\n".join(pdfutil._page_text(pdf[i])
                     for i in range(min(pdf.page_count, llm_mod.DOC_CLASS_PAGES))).strip()


def _in_set(d: dict, which: str) -> bool:
    if d.get("header_chars", 0) > MAX_HEADER_CHARS:
        return False
    if which == "text":
        return d["route"] == "text"
    return d["route"] == "image" and d["old_route"] == "text"


def label_of(d: dict, labels: dict) -> str | None:
    """pos / neg / None (unlabelled). A hand label wins; else a production
    document of a compliance type is positive."""
    hand = labels.get(d["content_hash"][:12]) or labels.get(d["content_hash"])
    if hand:
        return hand["label"] if isinstance(hand, dict) else hand
    reg = d.get("registry") or {}
    if d["source"] == "prod" and reg.get("type") in POSITIVE_TYPES:
        return "pos"
    return None


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def _docs_for(selection: list[dict], which: str) -> list[dict]:
    """`text`: the text doc-class call; `img`: the files item 1 moved from the text
    call to the image call."""
    if which not in ("text", "img"):
        raise SystemExit(f"--set {which!r}: text or img")
    return [d for d in selection if which in d.get("sets", ())]


def build_round(docs, arms, cache) -> tuple[list[dict], dict]:
    reqs, keys = [], {}
    for d in docs:
        with pdfutil.open_doc(d["path"]) as pdf:
            for a in arms:
                params = build_params(a, pdf, d["filename"])
                if params is None:
                    continue
                r = request_record(d, a, params)
                keys[(d["content_hash"], a.label)] = r["key"]
                if cache.get(r["key"]) is None:
                    reqs.append(r)
    return reqs, keys


def cmd_run(args) -> int:
    data = lq.main_checkout()
    root = data / OUT
    run = pathlib.Path(args.run)
    selection = json.loads((run / "selection.json").read_text())
    docs = _docs_for(selection, args.set)
    if args.only:
        wanted = set(args.only.split(","))
        docs = [d for d in docs if d["content_hash"][:12] in wanted]
    if args.limit:
        docs = docs[:args.limit]
    arms = [ARMS[a] for a in args.arms.split(",")]
    cache = lq.Cache(root / "no-base-cache", root / "cache")
    state = lq.State(root / "state.json")
    cap = Decimal(str(args.cap_usd))

    def log(s: str) -> None:
        print(s, flush=True)
        with open(run / "rounds.log", "a") as fh:
            fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {s}\n")

    root.mkdir(parents=True, exist_ok=True)
    lock = open(root / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"another run holds {root / '.lock'}") from None
    if state.data.get("intent"):
        raise SystemExit("an earlier submission was interrupted before its batch id was "
                         "written; look at the console first")
    client = None
    if state.uncollected() and not args.dry_run:
        client = lq.api_client(data)
        lq.collect(state, cache, client, log)
    reqs, keys = build_round(docs, arms, cache)
    known = json.loads((run / "keys.json").read_text()) if (run / "keys.json").exists() else {}
    known.update({f"{h}:{label}": k for (h, label), k in keys.items()})
    lq._write_json(run / "keys.json", known)
    by_label = collections.defaultdict(lambda: [0, Decimal(0)])
    for r in reqs:
        by_label[r["label"]][0] += 1
        by_label[r["label"]][1] += estimate_usd(r)
    est = sum((v[1] for v in by_label.values()), Decimal(0))
    log(f"set {args.set}, {len(docs)} documents, arms {args.arms}: {len(reqs)} requests to send")
    for label, (n, usd) in sorted(by_label.items()):
        log(f"  {label:14} {n:4}  ~${usd:.4f}")
    spent = state.spent()
    log(f"spent so far ${spent:.4f} + this round ~${est:.4f} = ~${spent + est:.4f}; cap ${cap}")
    if not reqs or args.dry_run:
        return 0
    if spent + est > cap:
        log("STOP: this round would pass the cap; nothing submitted")
        return 2
    cids = {llm_mod._custom_id(r["content_hash"], r["label"]): r for r in reqs}
    if len(cids) != len(reqs):
        raise SystemExit("two requests share a custom id; nothing submitted")
    meta = {cid: lq._meta(r) for cid, r in cids.items()}
    state.data["intent"] = {"at": lq._now(), "round": args.set, "requests": meta,
                            "estimate_usd": str(est)}
    for r in reqs:
        state.data["attempts"][r["key"]] = state.data["attempts"].get(r["key"], 0) + 1
    state.save()
    client = client or lq.api_client(data)
    batch = client.messages.batches.create(requests=[
        {"custom_id": cid, "params": r["params"]} for cid, r in cids.items()])
    state.data["batches"][batch.id] = {"round": args.set, "submitted_at": lq._now(),
                                       "requests": meta, "estimate_usd": str(est),
                                       "collected": False}
    state.data["intent"] = None
    state.save()
    log(f"submitted {batch.id}: {len(reqs)} requests, ~${est:.4f}")
    lq.collect(state, cache, client, log)
    log(f"spent ${state.spent():.4f} in all")
    return 0


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def answer(entry: dict | None) -> tuple[str, int | None]:
    """(answer, output tokens): a class, `refused`, or `truncated`."""
    if entry is None:
        return "missing", None
    msg = anthropic.types.Message.model_validate(entry["message"])
    out = msg.usage.output_tokens
    try:
        cls, _ = llm_mod._classify_answer(msg, entry["label"])
    except RuntimeError:
        return "truncated", out
    return ("refused" if cls == llm_mod.NOT_A_DOCUMENT else cls), out


def cmd_report(args) -> int:
    data = lq.main_checkout()
    root = data / OUT
    run = pathlib.Path(args.run)
    selection = json.loads((run / "selection.json").read_text())
    labels_path = run / "labels.json"
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else {}
    cache = lq.Cache(root / "no-base-cache", root / "cache")
    state = lq.State(root / "state.json")
    keys = json.loads((run / "keys.json").read_text()) if (run / "keys.json").exists() else {}
    lines = [f"# Doc-class check, {run.name}", ""]
    rows = []
    for which in ("text", "img"):
        docs = _docs_for(selection, which)
        arm_ids = [a for a in args.arms.split(",") if (ARMS[a].kind == "image") == (which == "img")]
        if not docs or not arm_ids:
            continue
        stats = {a: collections.Counter() for a in arm_ids + ["recorded"]}
        outs = {a: [] for a in arm_ids}
        for d in docs:
            lab = label_of(d, labels)
            row = {"set": which, "hash": d["content_hash"][:12], "source": d["source"],
                   "file": pathlib.Path(d["filename"]).name, "label": lab,
                   "recorded": d["recorded"],
                   "type": (d.get("registry") or {}).get("type") or d.get("local_type")}
            for a in arm_ids:
                key = keys.get(f"{d['content_hash']}:{ARMS[a].label}")
                ans, out = ("missing", None) if key is None else answer(cache.get(key))
                row[a] = ans
                if out is not None:
                    outs[a].append(out)
            rec = {"accepted": "accepted", "refused": "refused"}.get(d["recorded"])
            for a, ans in [(a, row[a]) for a in arm_ids] + [("recorded", rec)]:
                if ans in (None, "missing", "none"):
                    stats[a]["no answer"] += 1
                    continue
                refused = ans in ("refused", "truncated")
                stats[a]["truncated"] += ans == "truncated"
                if lab == "pos":
                    stats[a]["pos"] += 1
                    stats[a]["false refusal"] += refused
                elif lab == "neg":
                    stats[a]["neg"] += 1
                    stats[a]["false acceptance"] += not refused
                else:
                    stats[a]["unlabelled"] += 1
            rows.append(row)
        lines += [f"## {which} ({len(docs)} documents)", "",
                  "| arm | positives | false refusals | negatives | false acceptances | "
                  "truncated | unlabelled | output tokens (median / max) |",
                  "|---|---|---|---|---|---|---|---|"]
        for a in arm_ids + ["recorded"]:
            s = stats[a]
            o = sorted(outs.get(a, []))
            ot = f"{o[len(o) // 2]} / {o[-1]}" if o else ""
            lines.append(f"| {a} | {s['pos']} | {s['false refusal']} | {s['neg']} | "
                         f"{s['false acceptance']} | {s['truncated']} | {s['unlabelled']} | {ot} |")
        lines.append("")
    spend = collections.defaultdict(Decimal)
    for b in state.data["batches"].values():
        if b.get("collected"):
            for m in b["requests"].values():
                spend[m["label"]] += 0
            spend["all"] += Decimal(b["cost_usd"])
    lines += [f"Spend (measured, all batches of `{OUT}`): ${spend['all']:.4f}", ""]
    (run / "report.md").write_text("\n".join(lines) + "\n")
    keys = sorted({k for r in rows for k in r})
    with open(run / "documents.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print("\n".join(lines))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.doc_class_check")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--run", required=True)
    r = sub.add_parser("run")
    r.add_argument("--run", required=True)
    r.add_argument("--arms", required=True)
    r.add_argument("--set", default="text")
    r.add_argument("--only", default="")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--cap-usd", default=str(CAP_USD))
    r.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("--run", required=True)
    p.add_argument("--arms", default="a,b,c,img")
    args = ap.parse_args(argv)
    return {"select": cmd_select, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
