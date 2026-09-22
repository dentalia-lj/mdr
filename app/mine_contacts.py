"""Read supplier contact addresses off documents we already hold.

`manufacturer.contact_emails` is what `email.request` addresses a letter to, and
what the drafts page refuses to release a letter without. **7 of 384
manufacturers carry one** (measured 2026-09-15), so nearly every letter this
system composes lands unsendable -- including the 44 EUDAMED certificate gaps,
which span 25 manufacturers of which 2 are addressable.

`app/contacts.py` has done the hard half of this since 2026-09-03: it filters
notified bodies and regulators out, matches a company's own domain by
registrable label, and ranks role mailboxes above named people. Nothing called
it. Its own docstring named a CLI that did not exist. This module is that
caller.

No network, no LLM, no EUDAMED round trip -- `document_text` is already parsed
and already in the database.

**What it writes, and what it refuses to write.** Only an address whose domain
matches the document's own manufacturer, and only a ROLE mailbox
(`info@`, `quality@`, `regulatory@`...). Three refusals, each for its own
reason:

* a certifier or regulator is never the manufacturer to write to, and a
  renewal request sent to the notified body is worse than none (it looks right);
* another company's domain on a document filed under this manufacturer means
  the document is misfiled or a brand-parent relation nobody recorded -- both
  are findings for a person, not contacts;
* a NAMED INDIVIDUAL is a legitimate contact but not a guessable one. The
  column may hold personal data (ruling 2026-08-20, which is why contacts live
  in the database and not in git), but a machine proposing to write to a person
  it found in a PDF is a different act from a person choosing to. Reported,
  never written.

**It never overwrites.** A manufacturer whose column is already filled is left
alone, exactly as `manufacturer_seed._write_contacts` leaves it: after the
editor shipped, the column carries somebody's decision, and a re-run that
reverted it would be indistinguishable from a successful import.

Shape is `mine_srn`'s -- plan / render / apply, dry run by default.
"""

from __future__ import annotations

from app import contacts

#: Documents worth opening. `LIKE '%@%'` is the cheap pre-filter; `find_addresses`
#: does the real work. A document with no at-sign cannot carry an address, and
#: most of the corpus has none.
_CANDIDATE_SQL = """
SELECT d.doc_id, d.canonical_manufacturer, t.content
  FROM document d
  JOIN document_text t ON t.content_hash = d.content_hash
 WHERE t.content LIKE '%@%'
 ORDER BY d.doc_id
"""

_DOMAINS_SQL = """
SELECT canonical_name, body->'domains' AS domains, contact_emails
  FROM manufacturer
"""

OUTCOMES = ("writable", "own_domain_not_role", "personal", "third_party",
            "other_manufacturer", "unknown", "no_manufacturer",
            "already_addressed")


def plan(conn) -> tuple[list[dict], list[dict], dict]:
    """`(writable, reported, counts)`. Writes nothing.

    `writable` is one entry per manufacturer, addresses ranked the way
    `email.request` reads them -- role mailboxes first, which decides which box
    a letter is addressed to.

    `reported` is everything a person should see and the machine must not act
    on, each row carrying the document it came from so the judgement is
    checkable rather than a bare string.
    """
    counts = {k: 0 for k in OUTCOMES}
    counts["scanned"] = 0

    domains, addressed = {}, set()
    for r in conn.execute(_DOMAINS_SQL).fetchall():
        domains[r["canonical_name"]] = list(r["domains"] or [])
        if r["contact_emails"]:
            addressed.add(r["canonical_name"])

    found: dict[str, dict[str, dict]] = {}
    reported: list[dict] = []
    seen_report: set[tuple[str, str]] = set()

    for row in conn.execute(_CANDIDATE_SQL).fetchall():
        counts["scanned"] += 1
        mfr = row["canonical_manufacturer"]
        addresses = contacts.find_addresses(row["content"] or "")
        if not addresses:
            continue
        if not mfr:
            # Nothing to attribute the address to. Inferring the manufacturer
            # from the filename is exactly the guess this whole module avoids.
            counts["no_manufacturer"] += 1
            continue

        others = {name: doms for name, doms in domains.items() if name != mfr}
        for address in addresses:
            v = contacts.classify(address, domains.get(mfr), others)

            if v.verdict == contacts.MANUFACTURER and v.role and not v.personal:
                if mfr in addressed:
                    # Already answerable. Proposing more is noise, and
                    # overwriting is somebody's decision reverted.
                    counts["already_addressed"] += 1
                    continue
                bucket = found.setdefault(mfr, {})
                if address not in bucket:
                    bucket[address] = {"address": address, "role": v.role,
                                       "doc_id": row["doc_id"]}
                    counts["writable"] += 1
                continue

            key = (mfr, address)
            if key in seen_report:
                continue
            seen_report.add(key)
            if v.verdict != contacts.MANUFACTURER:
                bucket_name = {
                    contacts.THIRD_PARTY: "third_party",
                    contacts.OTHER_MANUFACTURER: "other_manufacturer",
                }.get(v.verdict, "unknown")
            elif v.personal:
                bucket_name = "personal"
            else:
                # The manufacturer's own domain, not on the role list:
                # `marketing@voco.com`, `incident@detax.com`. Not writable --
                # the role list is what makes an address survive staff turnover
                # and a mailbox nobody reads is worse than none -- but this is
                # the pile a person should read FIRST, because every entry is
                # already known to belong to the right company.
                bucket_name = "own_domain_not_role"
            counts[bucket_name] += 1
            reported.append({"canonical_name": mfr, "address": address,
                             "verdict": v.verdict, "reason": v.reason,
                             "matched": v.matched, "personal": v.personal,
                             "role": v.role, "doc_id": row["doc_id"]})

    writable = [
        {"canonical_name": name,
         "addresses": [v.address for v in contacts.rank(
             [contacts.Verdict(e["address"], contacts.MANUFACTURER, "",
                               role=e["role"]) for e in bucket.values()])],
         "source_doc_ids": sorted({e["doc_id"] for e in bucket.values()})}
        for name, bucket in sorted(found.items())
    ]
    # Own-domain addresses first: they are the ones a person can accept
    # without leaving the page, and burying them under twenty notified bodies
    # is how a report goes unread.
    reported.sort(key=lambda r: (r["verdict"] != contacts.MANUFACTURER,
                                 r["canonical_name"], r["address"]))
    return writable, reported, counts


def apply(conn, writable: list[dict]) -> dict:
    """Fill `contact_emails` where it is empty. The caller owns the transaction.

    The `WHERE` clause repeats the empty check `plan` already made, on purpose:
    a person may have filled the column between the dry run and the apply, and
    the guard is what makes this command safe to re-run without re-reading.
    """
    written = 0
    for w in writable:
        cur = conn.execute(
            "UPDATE manufacturer SET contact_emails = %s, "
            "  updated_by = 'mine-contacts', updated_at = now() "
            " WHERE canonical_name = %s "
            "   AND COALESCE(array_length(contact_emails, 1), 0) = 0",
            (w["addresses"], w["canonical_name"]))
        written += cur.rowcount
    return {"written": written, "proposed": len(writable)}


def render(writable: list[dict], reported: list[dict], counts: dict, *,
           apply: bool) -> str:
    """The operator's view. Every line names the document behind it."""
    lines = [
        f"scanned {counts['scanned']} document(s) carrying an address",
        f"  writable          {counts['writable']}"
        f"  (role mailbox on the manufacturer's own domain)",
        f"  own domain, not a role mailbox  {counts['own_domain_not_role']}",
        f"  personal          {counts['personal']}",
        f"  third party       {counts['third_party']}",
        f"  another company   {counts['other_manufacturer']}",
        f"  unrecognised      {counts['unknown']}",
        f"  no manufacturer   {counts['no_manufacturer']}",
        f"  already addressed {counts['already_addressed']}",
        "",
    ]
    if writable:
        lines.append(f"{'WROTE' if apply else 'WOULD WRITE'} "
                     f"{len(writable)} manufacturer(s):")
        for w in writable:
            docs = ", ".join(f"doc {d}" for d in w["source_doc_ids"][:3])
            lines.append(f"  {w['canonical_name']}: "
                         f"{', '.join(w['addresses'])}  ({docs})")
    else:
        lines.append("no writable address found")

    if reported:
        lines += ["", f"FOR A PERSON ({len(reported)}), never written:"]
        for r in reported:
            note = "names an individual" if r["personal"] else r["reason"]
            if r["verdict"] == contacts.UNKNOWN and r["role"]:
                # Not a guess about whose address it is -- a statement about
                # what this tool could not check. `info@` on an unauthored
                # domain is one playbook edit away from being writable, and
                # saying so is the difference between a dead report and a
                # to-do list.
                note += " (role mailbox: author the domain and this becomes writable)"
            lines.append(f"  {r['canonical_name']}: {r['address']} "
                         f"-- {note} (doc {r['doc_id']})")
    if not apply and writable:
        lines += ["", "dry run — nothing written. Re-run with --apply."]
    return "\n".join(lines)
