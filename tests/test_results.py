"""Standard job-result envelope and the anomaly ledger."""
import pytest

from app.results import ANOMALY_KINDS, Result, record_anomalies


def test_envelope_has_a_stable_shape():
    r = Result()
    r.count("seen", 10)
    r.count("seen", 5)
    r.sample("skipped", {"item_ref": "A1"})
    r.note("md-unknown processing was enabled")
    out = r.as_dict()
    assert out["counts"] == {"seen": 15}
    assert out["samples"]["skipped"] == [{"item_ref": "A1"}]
    assert out["notes"] == ["md-unknown processing was enabled"]
    assert out["anomalies"] == []


def test_samples_are_bounded_to_fifty():
    r = Result()
    for i in range(120):
        r.sample("skipped", {"item_ref": f"A{i}"})
    out = r.as_dict()
    assert len(out["samples"]["skipped"]) == 50
    assert out["counts"]["skipped_sampled_of"] == 120


def test_unknown_anomaly_kind_is_rejected():
    r = Result()
    with pytest.raises(ValueError, match="unknown anomaly kind"):
        r.anomaly("something_i_invented", subject="A1")


def test_record_anomalies_upserts_and_counts(conn):
    r = Result()
    r.anomaly("md_class_blank", subject="A1", catalogue="LJ")
    r.anomaly("md_class_blank", subject="A1", catalogue="LJ")
    record_anomalies(conn, r.as_dict()["anomalies"])
    record_anomalies(conn, r.as_dict()["anomalies"])
    row = conn.execute(
        "SELECT kind, subject, seen_count FROM data_anomaly WHERE subject='A1'"
    ).fetchone()
    assert row["kind"] == "md_class_blank"
    assert row["seen_count"] == 4


def test_every_kind_is_snake_case_and_documented():
    assert ANOMALY_KINDS
    for kind in ANOMALY_KINDS:
        assert kind.islower() and " " not in kind
