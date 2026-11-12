import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import atlas.pipeline as pipeline
from atlas.pipeline import (
    Candidate,
    load_sources,
    merge_candidates,
    parse_candidate,
    publication_hold_reasons,
    read_json,
    score,
    select_candidates,
    update_records,
    validate_all,
    write_state,
)


def test_compact_state_writer_round_trips_valid_json(tmp_path):
    path = tmp_path / "state.json"
    state = {
        "http|8.8.8.8|8080": {
            "quality_policy": 2,
            "last_seen": "2026-11-12T10:00:00Z",
            "last_attempt_at": "2026-11-12T10:00:00Z",
            "success_streak": 2,
        }
    }

    write_state(path, state)

    assert path.read_bytes().endswith(b"\n")
    assert read_json(path, {}) == state
    assert b": " not in path.read_bytes()


def test_read_json_recovers_legacy_state_literal_newline_suffix(tmp_path, capsys):
    path = tmp_path / "state.json"
    state = {"http|8.8.8.8|8080": {"quality_policy": 2, "success_streak": 2}}
    path.write_text(json.dumps(state) + "\\n", encoding="utf-8")

    assert read_json(path, {}) == state
    assert "recovered state.json" in capsys.readouterr().out


def test_read_json_warns_when_state_file_is_corrupt(tmp_path, capsys):
    path = tmp_path / "state.json"
    path.write_text("{invalid", encoding="utf-8")

    assert read_json(path, {}) == {}
    assert "WARNING: could not read state.json" in capsys.readouterr().out


def test_read_json_logs_expected_missing_initial_state(tmp_path, capsys):
    path = tmp_path / "state.json"

    assert read_json(path, {}) == {}
    assert "INFO: state.json does not exist yet" in capsys.readouterr().out


def test_parse_and_normalize_socks5_hostname():
    candidate = parse_candidate(
        "socks5h://8.8.8.8:1080",
        {"id": "test", "group": "test-group"},
    )
    assert candidate is not None
    assert candidate.protocol == "socks5"
    assert candidate.source_groups == ("test-group",)


def test_https_feed_override_is_connect_http():
    candidate = parse_candidate(
        "https://1.1.1.1:8080",
        {"id": "mirror", "group": "mirror", "scheme_overrides": {"https": "http"}},
    )
    assert candidate is not None
    assert candidate.protocol == "http"


def test_private_and_non_allowlisted_endpoints_are_rejected():
    source = {"id": "test", "group": "test"}
    assert parse_candidate("http://127.0.0.1:8080", source) is None
    assert parse_candidate("http://8.8.8.8:25", source) is None


def test_non_independent_mirror_does_not_count_for_diversity():
    candidate = parse_candidate(
        "8.8.8.8:1080",
        {
            "id": "mirror", "group": "mirror", "independent": False,
            "default_protocol": "socks5",
        },
    )
    assert candidate is not None
    assert candidate.source_groups == ()


def test_publish_source_count_counts_only_groups_with_parsed_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    sources = [
        {
            "id": "feed-a", "group": "publisher-a", "name": "Feed A",
            "url": "https://feed-a.invalid/proxies.txt",
            "status": "ok",
            "line_count": 20, "error": None, "independent": True,
            "unique_candidates": 5,
        },
        {
            "id": "feed-a-mirror", "group": "publisher-a", "name": "Feed A mirror",
            "url": "https://feed-a-mirror.invalid/proxies.txt",
            "status": "ok",
            "line_count": 20, "error": None, "independent": True,
            "unique_candidates": 2,
        },
        {
            "id": "feed-empty", "group": "publisher-empty", "name": "Empty Feed",
            "url": "https://feed-empty.invalid/proxies.txt",
            "status": "ok",
            "line_count": 0, "error": None, "independent": True,
            "unique_candidates": 0,
        },
    ]

    pipeline.publish([], sources, {}, "2026-11-12T16:00:00Z")
    stats = json.loads((tmp_path / "data" / "stats.json").read_text(encoding="utf-8"))

    assert stats["source_count"] == 1
    assert stats["sources_configured"] == 2
    assert stats["feeds_with_candidates"] == 2


def test_repeat_success_increases_score():
    assert score(200, 3, 2) > score(200, 1, 1)



def test_feed_merge_reports_parse_rejections_duplicates_and_empty_feeds():
    feeds = [
        {
            "id": "feed-a", "group": "publisher-a", "status": "ok",
            "default_protocol": "http",
            "lines": ["8.8.8.8:8080", "8.8.8.8:8080", "not-a-proxy"],
        },
        {
            "id": "feed-empty", "group": "publisher-empty", "status": "ok",
            "lines": ["# no candidates here", ""],
        },
        {"id": "feed-down", "group": "publisher-down", "status": "error", "lines": []},
    ]

    candidates = merge_candidates(feeds)

    assert len(candidates) == 1
    assert feeds[0]["candidate_lines"] == 3
    assert feeds[0]["parsed_candidate_lines"] == 2
    assert feeds[0]["unique_candidates"] == 1
    assert feeds[0]["duplicate_lines"] == 1
    assert feeds[0]["rejected_lines"] == 1
    assert feeds[0]["parse_status"] == "ok"
    assert feeds[1]["parse_status"] == "no_candidates"
    assert feeds[2]["parse_status"] == "fetch_failed"



def test_publication_holds_explain_pending_repeat_and_slow_successes():
    pending = Candidate("8.8.8.8", 8080, "http", ("feed-a",), ("publisher-a",))
    slow = Candidate("1.1.1.1", 8080, "http", ("feed-b",), ("publisher-b",))
    published = Candidate("9.9.9.9", 8080, "http", ("feed-c",), ("publisher-c",))
    passed = [
        {"candidate": pending, "latency_ms": 350.0, "status_code": 200},
        {"candidate": slow, "latency_ms": 1400.0, "status_code": 200},
        {"candidate": published, "latency_ms": 250.0, "status_code": 200},
    ]
    records = [{
        "ip": published.ip, "port": published.port, "protocol": published.protocol,
    }]
    state = {
        pending.key: {"success_streak": 1},
        slow.key: {"success_streak": 0},
        published.key: {"success_streak": 3},
    }

    assert publication_hold_reasons(passed, records, state) == {
        "latency_over_limit": 1,
        "repeat_validation_pending": 1,
    }


def _verified_record(candidate, checked_at):
    return {
        "ip": candidate.ip,
        "port": candidate.port,
        "address": candidate.address,
        "protocol": candidate.protocol,
        "proxy": candidate.proxy,
        "latency_ms": 400.0,
        "status": "verified",
        "status_code": 200,
        "supports_https": True,
        "quality_score": 80,
        "quality_tier": "verified",
        "success_streak": 2,
        "source_count": 1,
        "sources": ["feed-a"],
        "last_checked_at": checked_at,
    }


def test_unattempted_verified_proxy_is_retained_inside_90_minute_window():
    candidate = Candidate("8.8.8.8", 8080, "http")
    prior_record = _verified_record(candidate, "2026-11-11T10:00:00Z")

    records, _ = update_records(
        [],
        {},
        "2026-11-11T10:20:00Z",
        previous_records=[prior_record],
    )

    assert len(records) == 1
    assert records[0]["last_checked_at"] == "2026-11-11T10:00:00Z"


def test_unattempted_verified_proxy_expires_after_90_minutes():
    candidate = Candidate("8.8.8.8", 8080, "http")
    prior_record = _verified_record(candidate, "2026-11-11T10:00:00Z")

    records, _ = update_records(
        [],
        {},
        "2026-11-11T11:31:00Z",
        previous_records=[prior_record],
    )

    assert records == []


def test_selected_failed_proxy_is_removed_even_inside_retention_window():
    candidate = Candidate("8.8.8.8", 8080, "http")
    prior_record = _verified_record(candidate, "2026-11-11T10:00:00Z")
    previous_state = {
        candidate.key: {
            "quality_policy": 2,
            "last_seen": "2026-11-11T10:00:00Z",
            "success_streak": 2,
        }
    }

    records, state = update_records(
        [],
        previous_state,
        "2026-11-11T10:05:00Z",
        attempted=[candidate],
        previous_records=[prior_record],
    )

    assert records == []
    assert state[candidate.key]["success_streak"] == 0


def test_feed_outage_disables_carry_forward():
    candidate = Candidate("8.8.8.8", 8080, "http")
    prior_record = _verified_record(candidate, "2026-11-11T10:00:00Z")

    records, _ = update_records(
        [],
        {},
        "2026-11-11T10:05:00Z",
        previous_records=[prior_record],
        retain_unattempted=False,
    )

    assert records == []




def test_state_records_keep_only_fields_used_by_validation_policy():
    candidate = Candidate("8.8.8.8", 8080, "http")
    passed = [{"candidate": candidate, "latency_ms": 400.0, "status_code": 200}]

    _, state = update_records(passed, {}, "2026-11-12T10:00:00Z")

    assert set(state[candidate.key]) == {
        "quality_policy", "last_seen", "last_attempt_at", "success_streak"
    }


def test_unselected_state_is_compacted_without_losing_repeat_fields():
    candidate = Candidate("8.8.8.8", 8080, "http")
    previous = {
        candidate.key: {
            "quality_policy": 2,
            "last_seen": "2026-11-12T10:00:00Z",
            "last_attempt_at": "2026-11-12T10:00:00Z",
            "success_streak": 2,
            "first_seen": "2026-11-11T10:00:00Z",
            "total_passes": 7,
            "last_latency_ms": 400,
            "sources": ["feed-a", "feed-b"],
        }
    }

    _, state = update_records([], previous, "2026-11-12T10:05:00Z")

    assert state[candidate.key] == {
        "quality_policy": 2,
        "last_seen": "2026-11-12T10:00:00Z",
        "last_attempt_at": "2026-11-12T10:00:00Z",
        "success_streak": 2,
    }


def test_unselected_successful_candidate_keeps_its_repeat_streak():
    candidate = Candidate("8.8.8.8", 8080, "http")
    previous = {
        candidate.key: {
            "quality_policy": 2,
            "last_seen": "2026-11-12T10:00:00Z",
            "last_attempt_at": "2026-11-12T10:00:00Z",
            "success_streak": 2,
            "total_passes": 2,
        }
    }

    _, state = update_records([], previous, "2026-11-12T10:05:00Z")

    assert state[candidate.key]["success_streak"] == 2
    assert state[candidate.key]["last_seen"] == "2026-11-12T10:00:00Z"


def test_state_retention_uses_latest_failed_attempt_not_old_success():
    candidate = Candidate("8.8.8.8", 8080, "http")
    previous = {
        candidate.key: {
            "last_seen": "2026-11-09T10:00:00Z",
            "last_attempt_at": "2026-11-11T23:55:00Z",
            "success_streak": 0,
        }
    }

    _, state = update_records([], previous, "2026-11-12T00:00:00Z")

    assert candidate.key in state
    assert state[candidate.key]["last_attempt_at"] == "2026-11-11T23:55:00Z"

def test_production_canaries_are_real_https_endpoints():
    assert pipeline.CHECK_TARGETS == (
        ("https://www.google.com/robots.txt", "User-agent: *"),
        ("https://www.cloudflare.com/cdn-cgi/trace", "ip="),
    )
    assert all(url.startswith("https://") for url, _ in pipeline.CHECK_TARGETS)
    assert len({url.split("/")[2] for url, _ in pipeline.CHECK_TARGETS}) == 2


def test_canary_response_requires_status_and_expected_marker():
    assert pipeline.canary_response_valid(200, b"User-agent: *", "User-agent: *")
    assert not pipeline.canary_response_valid(403, b"User-agent: *", "User-agent: *")
    assert not pipeline.canary_response_valid(200, b"Access denied", "User-agent: *")
    assert pipeline.canary_response_valid(200, b"ip=203.0.113.1", "ip=")


def test_vmheaven_https_feed_is_normalized_to_http_connect():
    sources = load_sources()
    source = next(item for item in sources if item["id"] == "vmheaven-https-connect")
    candidate = parse_candidate("8.8.8.8:8080", source)
    assert candidate is not None
    assert candidate.protocol == "http"
    assert candidate.source_groups == ()


def test_relayglass_connect_feed_is_normalized_to_http():
    sources = load_sources()
    relayglass = next(source for source in sources if source["id"] == "relayglass-https-connect")
    candidate = parse_candidate("8.8.8.8:8080", relayglass)
    assert candidate is not None
    assert candidate.protocol == "http"
    assert candidate.source_groups == ()


def test_validate_all_reports_failure_categories(monkeypatch):
    candidates = [
        Candidate("8.8.8.8", 8080, "http"),
        Candidate("1.1.1.1", 8080, "http"),
        Candidate("9.9.9.9", 8080, "http"),
    ]

    async def fake_validate(candidate, semaphore, proxy_connector):
        if candidate.ip == "8.8.8.8":
            return {
                "candidate": candidate, "latency_ms": 250.0, "status_code": 200
            }, None
        if candidate.ip == "1.1.1.1":
            return None, "timeout"
        return None, "canary_marker_missing"

    monkeypatch.setattr(pipeline, "validate_one", fake_validate)
    passed, failures = asyncio.run(validate_all(candidates, concurrency=3))

    assert len(passed) == 1
    assert failures == {"canary_marker_missing": 1, "timeout": 1}

def test_expanded_common_proxy_ports_are_supported():
    candidate = parse_candidate(
        "http://8.8.8.8:10808", {"id": "test", "group": "test"}
    )
    assert candidate is not None
    assert candidate.port == 10808


def test_source_catalogue_is_well_formed_and_diversity_groups_are_unique():
    sources = load_sources()
    ids = [source["id"] for source in sources]
    assert len(ids) >= 30
    assert len(ids) == len(set(ids))
    assert all(source["url"].startswith("https://") for source in sources)
    independent = {
        source.get("group", source["id"])
        for source in sources if source.get("independent", True)
    }
    all_groups = {source.get("group", source["id"]) for source in sources}
    assert len(independent) >= 10
    assert len(all_groups) >= len(independent)


def test_proxywing_https_label_is_normalized_to_connect_http():
    sources = load_sources()
    proxywing = next(source for source in sources if source["id"] == "proxywing-all")
    candidate = parse_candidate("https://8.8.8.8:8080", proxywing)
    assert candidate is not None
    assert candidate.protocol == "http"



def test_publication_requires_two_successful_checks():
    candidate = Candidate("8.8.8.8", 8080, "http", ("feed-a",), ("publisher-a",))
    passed = [{"candidate": candidate, "latency_ms": 500.0, "status_code": 200}]
    first_records, state = update_records(passed, {}, "2026-11-11T10:00:00Z")
    assert first_records == []

    second_records, _ = update_records(passed, state, "2026-11-11T11:00:00Z")
    assert len(second_records) == 1
    assert second_records[0]["success_streak"] == 2
    assert second_records[0]["quality_score"] >= 60


def test_slow_proxy_is_never_published_even_after_repeat_success():
    candidate = Candidate("8.8.8.8", 8080, "http", ("feed-a",), ("publisher-a",))
    passed = [{"candidate": candidate, "latency_ms": 1501.0, "status_code": 200}]
    _, state = update_records(passed, {}, "2026-11-11T10:00:00Z")
    records, _ = update_records(passed, state, "2026-11-11T11:00:00Z")
    assert records == []


def test_failed_proxy_is_removed_from_public_snapshot_immediately():
    candidate = Candidate("8.8.8.8", 8080, "http", ("feed-a",), ("publisher-a",))
    passed = [{"candidate": candidate, "latency_ms": 400.0, "status_code": 200}]
    _, state = update_records(passed, {}, "2026-11-11T10:00:00Z")
    records, state = update_records(passed, state, "2026-11-11T11:00:00Z")
    assert len(records) == 1

    records_after_failure, retained_state = update_records(
        [],
        state,
        "2026-11-11T12:00:00Z",
        attempted=[candidate],
    )
    assert records_after_failure == []
    assert retained_state[candidate.key]["success_streak"] == 0


def test_quality_score_rewards_speed_repeatability_and_source_diversity():
    assert score(300, 4, 2) > score(1400, 2, 0)



def test_slow_success_does_not_count_toward_fast_quality_streak():
    candidate = Candidate("8.8.8.8", 8080, "http", ("feed-a",), ("publisher-a",))
    slow = [{"candidate": candidate, "latency_ms": 1501.0, "status_code": 200}]
    fast = [{"candidate": candidate, "latency_ms": 500.0, "status_code": 200}]

    _, state = update_records(slow, {}, "2026-11-11T10:00:00Z")
    first_fast, state = update_records(fast, state, "2026-11-11T11:00:00Z")
    assert first_fast == []
    assert state[candidate.key]["success_streak"] == 1

    second_fast, _ = update_records(fast, state, "2026-11-11T12:00:00Z")
    assert len(second_fast) == 1
    assert second_fast[0]["success_streak"] == 2



def test_legacy_success_streaks_are_not_trusted_under_new_policy():
    candidate = Candidate("8.8.8.8", 8080, "http", ("feed-a",), ("publisher-a",))
    previous = {
        candidate.key: {
            "last_seen": "2026-11-11T10:00:00Z",
            "success_streak": 20,
            "total_passes": 20,
        }
    }
    passed = [{"candidate": candidate, "latency_ms": 400.0, "status_code": 200}]

    first, state = update_records(passed, previous, "2026-11-11T11:00:00Z")
    assert first == []
    assert state[candidate.key]["success_streak"] == 1
    assert state[candidate.key]["quality_policy"] == 2

    second, _ = update_records(passed, state, "2026-11-11T12:00:00Z")
    assert len(second) == 1



def test_recently_checked_candidates_are_held_out_until_observation_window():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    recent = Candidate("8.8.8.8", 8080, "http")
    due = Candidate("1.1.1.1", 8080, "http")
    new = Candidate("9.9.9.9", 8080, "http")
    state = {
        recent.key: {
            "last_seen": (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
            "success_streak": 1,
        },
        due.key: {
            "last_seen": (now - timedelta(minutes=60)).isoformat().replace("+00:00", "Z"),
            "success_streak": 1,
        },
    }

    selected = select_candidates([recent, due, new], state, 10, now=now)

    assert recent not in selected
    assert due in selected
    assert new in selected



def test_failed_candidates_are_not_retested_every_five_minutes():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    failed = Candidate("8.8.8.8", 8080, "http")
    _, state = update_records([], {}, "2026-11-12T12:00:00Z", attempted=[failed])

    selected_soon = select_candidates(
        [failed], state, 10, now=now + timedelta(minutes=10)
    )
    selected_later = select_candidates(
        [failed], state, 10, now=now + timedelta(minutes=50)
    )

    assert failed not in selected_soon
    assert failed in selected_later



def test_failed_candidate_cooldown_survives_following_empty_run():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    failed = Candidate("8.8.8.8", 8080, "http")
    _, first_state = update_records(
        [], {}, "2026-11-12T12:00:00Z", attempted=[failed]
    )
    _, next_state = update_records([], first_state, "2026-11-12T12:05:00Z")

    selected = select_candidates(
        [failed], next_state, 10, now=now + timedelta(minutes=10)
    )

    assert failed not in selected


class _ChunkedBody:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, chunk_size):
        for chunk in self.chunks:
            yield chunk


def test_bounded_source_reader_reads_every_chunk():
    body = asyncio.run(pipeline.read_bounded_body(
        _ChunkedBody([b"first\n", b"second\n", b"third\n"]), limit=32
    ))
    assert body == b"first\nsecond\nthird\n"


def test_bounded_source_reader_rejects_oversized_stream():
    with pytest.raises(ValueError, match="byte limit"):
        asyncio.run(pipeline.read_bounded_body(
            _ChunkedBody([b"1234", b"5678"]), limit=7
        ))


def test_source_catalogue_rejects_reserved_example_domains(tmp_path, monkeypatch):
    source_file = tmp_path / "sources.toml"
    source_file.write_text(
        '[[sources]]\nid = "bad-feed"\nurl = "https://example.com/proxies.txt"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "SOURCE_CONFIG", source_file)
    with pytest.raises(RuntimeError, match="real public HTTPS"):
        load_sources()


def test_source_catalogue_rejects_non_https_feeds(tmp_path, monkeypatch):
    source_file = tmp_path / "sources.toml"
    source_file.write_text(
        '[[sources]]\nid = "bad-feed"\nurl = "http://proxy-feed.example.org/list.txt"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "SOURCE_CONFIG", source_file)
    with pytest.raises(RuntimeError, match="real public HTTPS"):
        load_sources()


class _RedirectContext:
    async def __aenter__(self):
        return SimpleNamespace(
            status=302,
            url=SimpleNamespace(scheme="https"),
            raise_for_status=lambda: None,
        )

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _RedirectSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _RedirectContext()


def test_source_fetch_rejects_redirects_without_following_them(monkeypatch):
    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(pipeline.asyncio, "sleep", no_wait)
    session = _RedirectSession()
    source = {"id": "redirecting-feed", "url": "https://feeds.example.invalid/list.txt"}
    result = asyncio.run(pipeline.fetch_source(
        session, source, asyncio.Semaphore(1)
    ))

    assert result["status"] == "error"
    assert "source redirects are disabled" in result["error"]
    assert len(session.calls) == 3
    assert all(call[1]["allow_redirects"] is False for call in session.calls)
