"""P7c: the correspondence and the add pages in one vocabulary (spec § 9).

The same five demands Task 9 makes of the registry pages, made here of the
pages an office person uses to look at post and to put something in:

1. A stored value shown to a person is shown as its § 9 word. "staged" is
   "Waiting for review" on the Emails page exactly as it is on Documents.
2. Every page opens with one sentence saying what it is.
3. No timestamp carries microseconds.
4. The catalogue tag, model ids and source keys (job tags, match bases, state
   slugs) are readable, but under "Technical details" — never in the sentence
   a person reads first.
5. Where a page labels a week, it labels it the way `words.week_label` does:
   "Week 36 (31 Aug-6 Sep)", not `2026-W36`.

Plus the two names § 9 sets outright: the inbound page is **Emails received**
and the outbound one is **Renewal emails**.

Real Postgres, no mocking. Rows are committed by the owner connection because
the TestClient reads on its own `dentalia_api` connection.
"""

from __future__ import annotations

import html as html_lib
import re

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.config import Web
from tests.conftest import TEST_API_URL
from web.app import create_app

# --------------------------------------------------------------------------- #
# reading a page the way a person does
# --------------------------------------------------------------------------- #
#: A collapsed "Technical details" disclosure. Everything inside one is filed,
#: not hidden: it is the right place for a job number, a model id or a source
#: key, and the wrong place for anything a person needs to act.
_TECHNICAL = re.compile(
    r'<details class="technical">.*?</details>', re.S)
_TAGS = re.compile(r"<[^>]+>")
_INTRO = re.compile(
    r'<p class="page-lede">(.*?)</p>'
    r'|<details class="page-intro">\s*<summary>(.*?)</summary>', re.S)
#: A sentence ends at a full stop, a question mark or a colon-free line end.
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


def visible(page: str) -> str:
    """What the page says before anybody opens a disclosure."""
    return html_lib.unescape(_TAGS.sub(" ", _TECHNICAL.sub(" ", page)))


def intro_of(page: str) -> str:
    """The one sentence under the heading, whichever form it takes."""
    m = _INTRO.search(page)
    assert m, "the page opens with no intro at all"
    return html_lib.unescape(_TAGS.sub(" ", m.group(1) or m.group(2))).strip()


#: Values that are ours, not the reader's. Each is legitimate under "Technical
#: details" and wrong anywhere else (spec § 9, rule 5).
JARGON = [
    "claude-",              # a model id
    "ingest.run",           # a job tag
    "vendor.import",
    "upload.ingest",
    "ref-list",             # a match basis
    "name-family",
    "not-a-manufacturer",   # an onboarding exit state
    "gate-manual",          # a manual-task kind
    "dry_run",              # a payload field
]


# --------------------------------------------------------------------------- #
# the pages, seeded once
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


def _document(conn, content_hash, *, status="production", doc_type="DoC",
              regulation="MDR", expires="2028-01-01") -> int:
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status, validity_to) "
        "VALUES (%s,%s,'group',%s,'local://seed/x.pdf',%s::doc_status,%s::date) "
        "RETURNING doc_id",
        (doc_type, regulation, content_hash, status, expires),
    ).fetchone()["doc_id"]


def _email(conn, *, uid, attachments, summary=None, model=None, status="no-body"):
    conn.execute(
        "INSERT INTO email_poll_log (mailbox, uid_validity, imap_uid, message_id, "
        "from_addr, subject, received_at, had_attachments, attachments, "
        "body_summary, summary_intent, summary_status, summary_model) "
        "VALUES ('mb/rep@x/INBOX','1',%s,%s,'rep@manu.example','Updated DoC',"
        "now(),true,%s,%s,'sends-documents',%s,%s)",
        (uid, f"<{uid}@x>", Json(attachments), summary, status, model),
    )


def _attachment(filename, content_hash, *, verdict="useful", disposition="archived"):
    return {"filename": filename, "content_type": "application/pdf",
            "verdict": verdict, "reason": None, "content_hash": content_hash,
            "archive_url": "file:///a", "disposition": disposition}


def _draft(conn, *, manufacturer="IVOCLAR", period="2026-W34", status="draft",
           kind="reminder", doc_ids=()) -> int:
    req = conn.execute(
        "INSERT INTO renewal_request (doc_id, state, manufacturer, reason, "
        "period_key, created_at, updated_at) "
        "VALUES (%s,'due',%s,'expiry',%s, now(), now()) RETURNING id",
        (doc_ids[0] if doc_ids else None, manufacturer, period),
    ).fetchone()["id"]
    for d in doc_ids:
        conn.execute("INSERT INTO renewal_request_document (renewal_request_id, "
                     "doc_id) VALUES (%s,%s)", (req, d))
    return conn.execute(
        "INSERT INTO email_draft (renewal_request_id, kind, to_addrs, subject, "
        "body, status) VALUES (%s,%s,%s,'RE: MDR DOCUMENTS','Dear all,',%s) "
        "RETURNING id",
        (req, kind, ["quality@ivoclar.example"], status),
    ).fetchone()["id"]


def _preview_job(conn, *, job_type="ingest.run", result=None) -> int:
    upload_id = conn.execute(
        "INSERT INTO import_inbox (kind, filename, content, catalogue) "
        "VALUES ('items','Artikli.csv','\\x00','LJ') RETURNING id").fetchone()["id"]
    payload = ({"source": "upload", "ref": {"upload_id": upload_id},
                "catalogue": "LJ", "dry_run": True, "filename": "Artikli.csv"}
               if job_type == "ingest.run" else
               {"upload_id": upload_id, "filename": "Proizvajalci.xlsx",
                "dry_run": True, "allow_renames": False})
    return conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, result) "
        "VALUES (%s,%s,%s,'done',%s) RETURNING id",
        (job_type, Json(payload), f"{job_type}:upload:{upload_id}:preview",
         Json(result if result is not None else
              {"seen": 2, "changed": 1, "unchanged": 1, "dry_run": True})),
    ).fetchone()["id"]


def _supplier(conn, name, *, items=3):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s)", (name,))
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s,%s,'vendor-master')", (name, name))
    for i in range(items):
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, "
            "updated_at) VALUES (%s,'Composite',%s,'LJ',now())",
            (f"{name}-{i}", name))


def _reports_dir(monkeypatch, tmp_path):
    (tmp_path / "report-2026-W34.html").write_text("<h1>34</h1>")
    (tmp_path / "report-2026-W36.html").write_text("<h1>36</h1>")
    import web.app as wa
    cfg = wa.load_config()
    monkeypatch.setattr(
        wa, "load_config",
        lambda: cfg.__class__(**{**cfg.__dict__,
                                 "web": type(cfg.web)(**{
                                     **cfg.web.__dict__,
                                     "reports_dir": str(tmp_path)})}))


#: Every page this slice owns, by the name the tests below use.
PAGE_NAMES = ["emails", "drafts", "draft", "upload", "upload-for-a-task",
              "import", "preview", "vendor-preview", "onboarding",
              "onboarding-who", "reports"]


@pytest.fixture
def pages(client, conn, tmp_path, monkeypatch) -> dict[str, str]:
    """One render of every page in this slice, with a row of everything on it."""
    staged = _document(conn, "a" * 64, status="staged")
    _document(conn, "b" * 64, status="production")
    _document(conn, "c" * 64, status="filed", doc_type="ISO", regulation="n.a.")
    _document(conn, "d" * 64, status="superseded")
    _email(conn, uid="1",
           attachments=[_attachment("doc-a.pdf", "a" * 64),
                        _attachment("doc-b.pdf", "b" * 64),
                        _attachment("doc-c.pdf", "c" * 64),
                        _attachment("doc-d.pdf", "d" * 64),
                        _attachment("unseen.pdf", "e" * 64),
                        _attachment("sds.pdf", None, verdict="skipped",
                                    disposition=None)],
           summary="Asks for the renewed Nexco declaration.",
           model="claude-haiku-4-5", status="ok")
    draft = _draft(conn, doc_ids=(staged,))
    _supplier(conn, "SANOLABOR")
    conn.execute(
        "INSERT INTO onboarding_state (canonical_name, state, reason, decided_by) "
        "VALUES ('SANOLABOR','not-a-manufacturer','distributor','natasa')")
    _supplier(conn, "KOMET")
    item_preview = _preview_job(conn)
    vendor_preview = _preview_job(conn, job_type="vendor.import", result={
        "seen": 9, "added": 2, "renamed": [], "disappeared": [], "unchanged": 7,
        "dry_run": True})
    task = conn.execute(
        "INSERT INTO manual_task (kind, group_id, payload, status) "
        "VALUES ('discovery-dead-end', NULL, %s, 'open') RETURNING id",
        (Json({"label": "Tetric EvoCeram"}),)).fetchone()["id"]
    conn.commit()
    _reports_dir(monkeypatch, tmp_path)

    def get(path: str) -> str:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        return resp.text

    return {
        "emails": get("/emails"),
        "drafts": get("/drafts"),
        "draft": get(f"/drafts/{draft}"),
        "upload": get("/upload"),
        "upload-for-a-task": get(f"/upload?manual_task_id={task}"),
        "import": get("/import"),
        "preview": get(f"/import/{item_preview}/preview"),
        "vendor-preview": get(f"/import/{vendor_preview}/preview"),
        "onboarding": get("/onboarding"),
        "onboarding-who": get("/onboarding/KOMET/start"),
        "reports": get("/reports"),
    }


# --------------------------------------------------------------------------- #
# 1. the two names § 9 sets
# --------------------------------------------------------------------------- #
def test_the_inbound_page_is_called_emails_received(pages):
    page = pages["emails"]

    assert "<h1>Emails received</h1>" in page
    assert "Emails received — Dentalia Compliance Registry" in page
    assert "<h1>Emails</h1>" not in page


def test_the_outbound_page_is_called_renewal_emails(pages):
    page = pages["drafts"]

    assert "<h1>Renewal emails</h1>" in page
    assert "Renewal emails — Dentalia Compliance Registry" in page
    assert "Renewal drafts" not in page


def test_a_renewal_email_is_named_by_who_it_is_to(pages):
    """Rule 1: the name first, the number second. "Draft #12" names nothing."""
    page = pages["draft"]

    assert "Renewal email to IVOCLAR" in visible(page)
    assert "<h1>Draft #" not in page


# --------------------------------------------------------------------------- #
# 2. status pills in the § 9 words
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stored,shown", [
    ("production", "Published"),
    ("staged", "Waiting for review"),
    ("filed", "On file"),
    ("superseded", "Replaced"),
])
def test_an_attachment_shows_its_document_status_in_words(pages, stored, shown):
    page = pages["emails"]

    assert shown in visible(page)
    assert f">{stored}<" not in page


def test_an_attachment_still_being_read_says_so(pages):
    """"in extraction" is our word for it; § 9's is "Being read"."""
    text = visible(pages["emails"])

    assert "Being read" in text
    assert "in extraction" not in text


def test_a_renewal_email_shows_its_own_state_in_words(pages):
    """`cancelled` is stored, but the button says Archive and so must the pill
    (rule 2: one word per thing)."""
    board = visible(pages["drafts"])

    assert "Ready to send" in board
    assert "Archived" in board
    assert "cancelled" not in board


# --------------------------------------------------------------------------- #
# 3. one sentence at the top of every page
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", PAGE_NAMES)
def test_every_page_opens_with_one_sentence(pages, name):
    intro = intro_of(pages[name])

    assert intro, f"{name}: empty intro"
    assert len(_SENTENCE_END.findall(intro)) == 1, f"{name}: {intro!r}"


# --------------------------------------------------------------------------- #
# 4. no microseconds, no keys on the surface
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", PAGE_NAMES)
def test_no_timestamp_carries_microseconds(pages, name):
    assert not re.search(r"\d{2}:\d{2}:\d{2}\.\d+", pages[name]), name


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_our_own_keys_stay_under_technical_details(pages, name):
    text = visible(pages[name])
    found = [j for j in JARGON if j in text]

    assert not found, f"{name}: {found} on the surface"


def test_the_catalogue_tag_is_not_a_question_put_to_the_office(pages):
    """One Business Central, one numbering: the tag is written, never chosen,
    and "Catalogue LJ" on the Import page only ever raised the question."""
    text = visible(pages["import"])

    assert "Catalogue LJ" not in text
    assert "LJ" not in text


def test_the_model_that_wrote_a_summary_is_still_readable(pages):
    """Filed, not deleted: a summary read as the sender's own words is the
    failure this attribution exists to prevent."""
    page = pages["emails"]

    assert "claude-haiku-4-5" in page
    assert "Technical details" in page


# --------------------------------------------------------------------------- #
# 5. a week is a week
# --------------------------------------------------------------------------- #
def test_the_reports_index_names_each_week(pages):
    text = visible(pages["reports"])

    assert "Week 36 (31 Aug–6 Sep)" in text
    assert "Week 34 (17–23 Aug)" in text
    assert "2026-W36" not in text


def test_a_renewal_email_names_the_week_it_covers(pages):
    for name in ("drafts", "draft"):
        text = visible(pages[name])
        assert "Week 34 (17–23 Aug)" in text, name
        assert "2026-W34" not in text, name


# --------------------------------------------------------------------------- #
# 6. the words the add pages use
# --------------------------------------------------------------------------- #
def test_onboarding_says_playbook_where_it_used_to_say_recipe(pages):
    for name in ("onboarding", "onboarding-who"):
        text = visible(pages[name])
        assert "playbook" in text.lower(), name
        assert "recipe" not in text.lower(), name


def test_onboarding_counts_items_not_articles(pages):
    """§ 9: item · article · product are one word, and it is "Item"."""
    for name in ("onboarding", "onboarding-who"):
        text = visible(pages[name])
        assert "article" not in text.lower(), name


def test_a_supplier_taken_out_of_the_queue_says_why_in_words(pages):
    text = visible(pages["onboarding"])

    assert "issues no declarations of its own" in text


def test_the_upload_page_names_the_item_it_is_for(pages):
    """Spec § 5: nobody types a group number, so the page says what it is for."""
    text = visible(pages["upload-for-a-task"])

    assert "Tetric EvoCeram" in text


def test_an_import_preview_leads_with_the_file_not_the_job_number(pages):
    page = pages["preview"]

    assert "Artikli.csv" in visible(page)
    assert "Job #" not in visible(page)
    assert "Job #" in page
