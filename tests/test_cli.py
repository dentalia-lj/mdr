"""CLI operator UX: errors must be clean one-liners, never raw tracebacks.

The closed job_type enum (invariant 7) is enforced by Postgres; the CLI's job
is to translate that rejection into a readable message and a non-zero exit.
"""

from __future__ import annotations

from app import cli


# --- seed helpers (discover-item tests) -------------------------------------

def _seed_group(conn, canonical="ACME"):
    return conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) RETURNING group_id",
        (canonical,),
    ).fetchone()["group_id"]


def _seed_member(conn, gid, item_ref):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "product_class, catalogue, mirror_rev, updated_at) "
        "VALUES (%s,'Widget','011',true,'IIa','LJ',1,now())",
        (item_ref,),
    )
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'udi')",
        (gid, item_ref),
    )


def test_enqueue_unknown_job_type_prints_clean_error(test_db_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", test_db_url)

    rc = cli.main(["enqueue", "not.a.type", "clean-error-probe"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "unknown job type" in captured.err
    assert "not.a.type" in captured.err
    assert "Traceback" not in captured.err + captured.out


def test_enqueue_valid_type_still_works(test_db_url, connect_test, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", test_db_url)

    rc = cli.main(["enqueue", "backfill.scan", "cli-happy-probe"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "enqueued job" in captured.out
    conn = connect_test()
    row = conn.execute(
        "SELECT type FROM job WHERE dedupe_key = 'cli-happy-probe'"
    ).fetchone()
    assert row["type"] == "backfill.scan"


# --- discover-item ------------------------------------------------------- #
# CLI producer for the admin refetch flag ([admin-refetch-item]): resolves
# item_ref -> group_id via item_group_member and enqueues discover.group at
# interactive priority, dedupe discover:refetch:{group_id}:{current_date}.

def test_discover_item_enqueues_discover_group(test_db_url, connect_test, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    seed = connect_test(autocommit=True)
    g = _seed_group(seed)
    _seed_member(seed, g, "IT-CLI-1")

    rc = cli.main(["discover-item", "IT-CLI-1"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "enqueued job" in captured.out
    check = connect_test(autocommit=True)
    row = check.execute(
        "SELECT payload, dedupe_key, priority FROM job WHERE type='discover.group'"
    ).fetchone()
    today = check.execute("SELECT current_date AS d").fetchone()["d"]
    assert row["payload"] == {"group_id": g, "ignore_recency": False}
    assert row["dedupe_key"] == f"discover:refetch:{g}:{today.isoformat()}"
    assert row["priority"] == "interactive"


def test_discover_item_force_sets_ignore_recency(test_db_url, connect_test, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    seed = connect_test(autocommit=True)
    g = _seed_group(seed)
    _seed_member(seed, g, "IT-CLI-2")

    rc = cli.main(["discover-item", "IT-CLI-2", "--force"])

    assert rc == 0
    check = connect_test(autocommit=True)
    row = check.execute(
        "SELECT payload FROM job WHERE type='discover.group'"
    ).fetchone()
    assert row["payload"]["ignore_recency"] is True


def test_discover_item_unknown_item_exits_2(test_db_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", test_db_url)

    rc = cli.main(["discover-item", "NOPE-CLI"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "NOPE-CLI" in captured.err
    assert "Traceback" not in captured.err + captured.out


def test_discover_item_same_day_second_call_dedupes(test_db_url, connect_test, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    seed = connect_test(autocommit=True)
    g = _seed_group(seed)
    _seed_member(seed, g, "IT-CLI-3")

    cli.main(["discover-item", "IT-CLI-3"])
    capsys.readouterr()
    rc = cli.main(["discover-item", "IT-CLI-3"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "deduped" in captured.out
