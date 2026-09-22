"""`app.alerts` — one line off this machine when something needs a person.

The contract that matters is negative: an alert must never break the caller.
Every test here that pushes a failure into `notify` is asserting that it comes
back False rather than raising, because the call site is the dead-letter path
and a job that dead-lettered correctly must not then blow up inside its own
notification.
"""

import httpx
import pytest

from app import alerts


class _Recorder:
    """Stands in for `httpx.post`. Records the call, returns what it is told."""

    def __init__(self, status=200, text="", raises=None):
        self.status, self.text, self.raises = status, text, raises
        self.calls = []

    def __call__(self, url, content=None, headers=None, timeout=None):
        self.calls.append({"url": url, "content": content,
                           "headers": headers, "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        return httpx.Response(self.status, text=self.text)


@pytest.fixture
def post(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(alerts.httpx, "post", rec)
    return rec


# --------------------------------------------------------------------------- #
# the happy path, and what actually goes on the wire
# --------------------------------------------------------------------------- #

def test_a_delivered_alert_reports_true(post):
    assert alerts.notify("https://ntfy.sh/t", "Title", "body") is True
    assert len(post.calls) == 1


def test_the_message_is_the_body_and_the_title_is_a_header(post):
    """ntfy's contract: the body is the message, the title rides in a header.
    A JSON backend would need a different shape, which is why only one is wired."""
    alerts.notify("https://ntfy.sh/t", "Dentalia: fetch.url dead-lettered",
                  "job 42 failed", priority=alerts.HIGH, tags="rotating_light")
    call = post.calls[0]
    assert call["content"] == b"job 42 failed"
    assert call["headers"]["Title"] == "Dentalia: fetch.url dead-lettered"
    assert call["headers"]["Priority"] == "high"
    assert call["headers"]["Tags"] == "rotating_light"


def test_tags_are_omitted_rather_than_sent_empty(post):
    alerts.notify("https://ntfy.sh/t", "T", "m")
    assert "Tags" not in post.calls[0]["headers"]


def test_a_non_ascii_message_survives_the_wire(post):
    """Manufacturer names carry accents; the body is encoded, not str()'d."""
    alerts.notify("https://ntfy.sh/t", "T", "GC EUROPE — izjava poteče")
    assert post.calls[0]["content"] == "GC EUROPE — izjava poteče".encode("utf-8")


def test_the_call_carries_a_short_timeout(post):
    """This runs on the failure path, sometimes while a worker is already
    unhealthy. It must never be the thing that hangs."""
    alerts.notify("https://ntfy.sh/t", "T", "m")
    assert post.calls[0]["timeout"] == alerts._TIMEOUT_S <= 10


# --------------------------------------------------------------------------- #
# unconfigured is the normal case, not an error
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("url", ["", None])
def test_no_endpoint_is_a_silent_no_op(post, url):
    """Tests, CI and a developer laptop have no endpoint and must not post
    anywhere, nor fill their logs saying so."""
    assert alerts.notify(url, "T", "m") is False
    assert post.calls == []


# --------------------------------------------------------------------------- #
# THE contract: an alert never breaks its caller
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("exc", [
    httpx.ConnectError("no route to host"),
    httpx.ReadTimeout("timed out"),
    httpx.InvalidURL("not a url"),
    RuntimeError("something entirely unexpected"),
])
def test_a_transport_failure_is_swallowed(monkeypatch, exc):
    monkeypatch.setattr(alerts.httpx, "post", _Recorder(raises=exc))
    assert alerts.notify("https://ntfy.sh/t", "T", "m") is False


@pytest.mark.parametrize("status", [400, 403, 429, 500, 503])
def test_a_rejecting_endpoint_reports_false_and_does_not_raise(monkeypatch, status):
    """429 is the one to expect in practice: ntfy rate-limits an anonymous topic."""
    monkeypatch.setattr(alerts.httpx, "post",
                        _Recorder(status=status, text="rate limit exceeded"))
    assert alerts.notify("https://ntfy.sh/t", "T", "m") is False


def test_a_redirect_is_not_treated_as_a_failure(monkeypatch):
    """< 400 is delivered. A 3xx means the endpoint moved, not that we failed."""
    monkeypatch.setattr(alerts.httpx, "post", _Recorder(status=302))
    assert alerts.notify("https://ntfy.sh/t", "T", "m") is True


def test_the_rejection_body_is_logged_not_only_the_code(monkeypatch, caplog):
    """Without the body the only symptom of a bad topic name is silence."""
    monkeypatch.setattr(alerts.httpx, "post",
                        _Recorder(status=429, text="topic rate limit exceeded"))
    with caplog.at_level("WARNING", logger="dentalia.alerts"):
        alerts.notify("https://ntfy.sh/t", "T", "m")
    assert "topic rate limit exceeded" in caplog.text
