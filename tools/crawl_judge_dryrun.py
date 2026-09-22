"""Dry-run the crawl link triage against a live library page. Dev-time only.

    python -m tools.crawl_judge_dryrun --index-url URL --link-pattern REGEX
        [--allow-host HOST ...] [--max-links N] [--refs REF,REF,...]

What it does, and nothing else: one GET of robots.txt and one of the index
page through the pipeline's own `Fetcher` (same User-Agent, same politeness),
`app.crawl.harvest` on the served HTML, then `CrawlLinkRanker` over every
surviving link in batches of `ranking.CRAWL_BATCH`, and a printed report --
the type mix, how many links name one of `--refs`, and the order the budget
would be spent in.

Writes NOTHING: no `robots_cache` row (robots.txt is parsed in memory), no
`crawl_link_rank` row, no `fetch.url` job, no document fetched. It exists so
a recipe can be judged before it is authored, and so the 2026-09-04 ruling can
be measured on a real page rather than asserted.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from decimal import Decimal
from urllib.parse import urlsplit

from app import crawl as crawl_mod
from app import robots as robots_mod
from app.adapters.fetcher import make_fetcher
from app.adapters.search import SearchCandidate
from app.config import load_config
from app.extract import ranking
from app.handlers.discover import TYPE_RANK, _norm_ref


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--index-url", required=True)
    ap.add_argument("--link-pattern", required=True)
    ap.add_argument("--allow-host", action="append", default=[])
    ap.add_argument("--max-links", type=int, default=200)
    ap.add_argument("--refs", default="", help="comma-separated article numbers to match")
    ap.add_argument("--batch", type=int, default=ranking.CRAWL_BATCH)
    ap.add_argument("--top", type=int, default=30, help="how many of the ordered links to print")
    a = ap.parse_args(argv)

    cfg = load_config()
    fetcher = make_fetcher(cfg)
    parts = urlsplit(a.index_url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    r = fetcher.get(robots_url)
    if r.status == 200 and r.body:
        rules = robots_mod._parse(r.body.decode("utf-8", errors="replace"))
        if not rules.allows(a.index_url):
            print(f"robots.txt DISALLOWS {a.index_url}; stopping.")
            return 2
        print(f"robots.txt: allow (status 200, crawl-delay {rules.crawl_delay})")
    else:
        print(f"robots.txt: status {r.status} -> treated as {'allow' if r.status in (404, 410) else 'unknown'}")

    page = fetcher.get(a.index_url)
    if page.status != 200 or not page.body:
        print(f"index page: status {page.status}, no body; stopping.")
        return 2
    html = page.body.decode("utf-8", errors="replace")
    h = crawl_mod.harvest(html, base_url=a.index_url, link_pattern=a.link_pattern,
                          allow_hosts=tuple(a.allow_host), max_links=cfg.discovery.crawl_rank_max)
    print(f"index page: {page.status}, {len(page.body):,} B")
    print(f"harvest: anchors_seen={h.anchors_seen} links_matched={h.links_matched} "
          f"unique={len(h.links)} capped_at_ceiling={h.links_capped} off_host={h.off_host}")
    hosts = Counter(urlsplit(u).hostname for u in h.links)
    print(f"link hosts: {dict(hosts)}")
    if not h.links:
        return 0

    import anthropic
    judge = ranking.CrawlLinkRanker(
        anthropic.Anthropic(api_key=cfg.connection.anthropic_api_key), cfg.models)
    cands = [SearchCandidate(url=u, title=h.texts.get(u, ""), snippet="", rank=i)
             for i, u in enumerate(h.links)]
    judged = []
    cost = Decimal(0)      # NOT a float: CallCost.cost is a Decimal, and `0.0 +
                           # Decimal` raises TypeError -- which a bare except
                           # swallowed on the first run, printing $0.0000 for
                           # four real calls.
    for s in range(0, len(cands), a.batch):
        judged.extend(judge("(dry run)", cands[s:s + a.batch]))
        try:
            cost += judge.last_cost.cost
        except ranking.economics.UnknownModelPricing:
            print(f"  (unpriced model {judge.last_cost.model_id})")
    print(f"judged {len(judged)} links in {-(-len(cands) // a.batch)} batches, cost ${cost:.4f}")

    by_type = Counter(j.type for j in judged)
    print(f"by type: {dict(by_type)}")
    ours = {_norm_ref(x) for x in a.refs.split(",") if x.strip()}
    rows = []
    for i, j in enumerate(judged):
        match = any(_norm_ref(n) in ours for n in j.article_numbers)
        rows.append(((not match, TYPE_RANK.get(j.type, 4), -j.confidence, i), j, match))
    rows.sort(key=lambda r: r[0])
    kept = rows[:a.max_links]
    print(f"ref matches: {sum(1 for r in rows if r[2])} of {len(rows)}")
    print(f"budget {a.max_links}: kept by type {dict(Counter(j.type for _, j, _ in kept))}; "
          f"page-order would have kept {dict(Counter(j.type for j in judged[:a.max_links]))}")
    print(f"\nfirst {a.top} in spend order:")
    for _, j, match in rows[:a.top]:
        text = (h.texts.get(j.url, "") or "")[:60]
        refs = ",".join(j.article_numbers[:3])
        print(f"  {'*' if match else ' '} {j.type:5} {j.confidence:.2f} {j.url.rsplit('/', 1)[-1][:50]:50} "
              f"| {text} | {refs} | {j.reason[:40]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
