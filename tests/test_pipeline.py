from atlas.pipeline import Candidate, load_sources, parse_candidate, score, update_records


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
    assert second_records[0]["quality_score"] >= 55


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
