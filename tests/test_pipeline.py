from atlas.pipeline import load_sources, parse_candidate, score


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
