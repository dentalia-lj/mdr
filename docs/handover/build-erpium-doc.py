"""Build the Erpium API document from REAL responses.

    python3 docs/handover/build-erpium-doc.py CAPTURE.json OUT.md

CAPTURE.json is the output of a capture run (a list of exchanges: name, path,
status, headers, body, ms) made with the code being shipped. Every JSON example
in the document is inserted from it here, never typed, so the document cannot
drift from what the API answers. A placeholder naming an exchange that is
missing stops the build.

Placeholders in erpium-webshop-api.template.md:
  {{ex:NAME}}                  request line + full JSON body
  {{ex:NAME|key:N,key2:N}}     same, lists under those keys cut to N, marked
  {{err:NAME}}                 the exchange's `detail`, as code
  {{dl}} {{date}} and a few counts
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent


def main(capture: str, out: str, date: str) -> None:
    ex = {e["name"]: e for e in json.loads(Path(capture).read_text())}

    def get(name):
        if name not in ex:
            raise SystemExit(f"missing exchange in capture: {name!r}")
        return ex[name]

    def block(name, cuts=""):
        e = get(name)
        body = json.loads(json.dumps(e["body"]))
        notes = []
        for cut in filter(None, cuts.split(",")):
            key, n = cut.split(":")
            n = int(n)
            if isinstance(body.get(key), list) and len(body[key]) > n:
                notes.append(f"`{key}`: first {n} of {len(body[key])} shown")
                body[key] = body[key][:n]
        req = f"GET {e['path']}" + ("\nX-API-Key: <key>" if "X-API-Key" in e["headers_sent"] else "")
        text = (f"```http\n{req}\n```\n\n"
                f"Response `{e['status']}`:\n\n"
                f"```json\n{json.dumps(body, ensure_ascii=False, indent=2)}\n```")
        if notes:
            text += "\n\n*Shortened for this document: " + "; ".join(notes) + ".*"
        return text

    t = (HERE / "erpium-webshop-api.template.md").read_text()
    t = re.sub(r"\{\{ex:([^}|]+)(?:\|([^}]*))?\}\}", lambda m: block(m.group(1), m.group(2) or ""), t)
    t = re.sub(r"\{\{err:([^}]+)\}\}", lambda m: f"`{get(m.group(1))['body']['detail']}`", t)

    dl = get("download (browser, no key)")
    h = dl["headers"]
    t = t.replace("{{dl}}", (
        f"```http\nGET {dl['path']}\n```\n\nResponse `{dl['status']}`:\n\n```http\n"
        f"Content-Type: {h.get('content-type')}\n"
        f"Content-Disposition: {h.get('content-disposition')}\n"
        f"Content-Length: {h.get('content-length')}\n"
        f"Cache-Control: {h.get('cache-control')}\n\n"
        f"<{dl['body']['_bytes']} bytes of PDF>\n```"))


    dw = get("manufacturer documents (DENTAL WORLD)")["body"]
    cm = get("manufacturer documents (CARL MARTIN)")["body"]["documents"]
    t = (t.replace("{{date}}", date)
          .replace("{{tree_total}}", str(get("manufacturer tree")["body"]["total"]))
          .replace("{{dw_docs}}", str(len(dw["documents"])))
          .replace("{{dw_other}}", str(len(dw["other_documents"])))
          .replace("{{cm_counts}}", " and ".join(f"{d['item_count']:,}" for d in cm))
          .replace("{{creafill_n}}", str(len(get("search by name")["body"]["items"]))))
    left = re.findall(r"\{\{[^}]+\}\}", t)
    if left:
        raise SystemExit(f"unfilled placeholders: {left}")
    Path(out).write_text(t)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "2026-11-24")
