import asyncio
from datetime import UTC, datetime, timedelta

import atlas.pipeline as pipeline
from atlas.pipeline import (
    Candidate,
    load_sources,
    merge_candidates,
    parse_candidate,
    publication_hold_reasons,
    score,
    select_candidates,
    update_records,
    validate_all,
)


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
        [], state, "2026-11-11T12:00:00Z"
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
