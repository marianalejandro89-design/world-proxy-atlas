from __future__ import annotations

import asyncio
import csv
import hashlib
import ipaddress
import json
import os
import time
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
SOURCE_CONFIG = ROOT / "config" / "sources.toml"
STATE_FILE = ROOT / "data" / "state.json"
CHECK_URL = "https://example.com/"
CHECK_MARKER = "Example Domain"
USER_AGENT = "WorldProxyAtlas/1.1 (+https://github.com/marianalejandro89-design/world-proxy-atlas)"
PROTOCOLS = {"http", "https", "socks4", "socks5"}
ALLOWED_PORTS = {
    80, 81, 443, 1080, 1081, 10802, 10803, 10804, 10805, 10806,
    10807, 10808, 10809, 11080, 11111, 3128, 4145, 4153, 8000,
    8080, 8081, 8082, 8083, 8084, 8085, 8086, 8088, 8089, 8090,
    8091, 8100, 8118, 8181, 8191, 8282, 8443, 8880, 8888, 8889,
    9000, 9001, 9050, 9080, 9090, 9091, 9191, 9200, 9443, 999,
    9998, 9999, 10000, 10001, 10080, 12051, 20001, 30000, 30001,
}
MAX_SOURCE_BYTES = 6 * 1024 * 1024
MAX_SOURCE_LINES = 30000
PUBLIC_RECORD_MAX_AGE = timedelta(minutes=90)


@dataclass(frozen=True)
class Candidate:
    ip: str
    port: int
    protocol: str
    sources: tuple[str, ...] = field(default_factory=tuple)
    source_groups: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return f"{self.protocol}|{self.ip}|{self.port}"

    @property
    def address(self) -> str:
        return f"[{self.ip}]:{self.port}" if ":" in self.ip else f"{self.ip}:{self.port}"

    @property
    def proxy(self) -> str:
        return f"{self.protocol}://{self.address}"


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(value, encoding="utf-8", newline="\n")
    tmp.replace(path)


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def load_sources() -> list[dict[str, Any]]:
    with SOURCE_CONFIG.open("rb") as stream:
        payload = tomllib.load(stream)
    sources = payload.get("sources", [])
    if not sources:
        raise RuntimeError("No source feeds configured")
    ids = [item["id"] for item in sources]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Source IDs must be unique")
    return sources


def parse_candidate(line: str, source: dict[str, Any]) -> Candidate | None:
    value = line.strip().split("#", 1)[0].strip().strip("\"'")
    if not value or value.startswith(("//", ";")):
        return None
    protocol = source.get("default_protocol")
    if "://" in value:
        try:
            parsed = urlsplit(value)
            raw_scheme = parsed.scheme.lower()
            protocol = source.get("scheme_overrides", {}).get(raw_scheme, raw_scheme)
            if protocol in {"socks4a", "socks4"}:
                protocol = "socks4"
            if protocol in {"socks5h", "socks5"}:
                protocol = "socks5"
            if protocol not in PROTOCOLS or parsed.username or parsed.password:
                return None
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                return None
            host, port = parsed.hostname, parsed.port
        except ValueError:
            return None
    else:
        if not protocol or protocol not in PROTOCOLS or "@" in value:
            return None
        try:
            parsed = urlsplit(f"//{value}")
            if parsed.path not in (None, "", "/"):
                return None
            host, port = parsed.hostname, parsed.port
        except ValueError:
            return None

    if not host or port is None or port not in ALLOWED_PORTS:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not address.is_global or address.is_multicast or address.is_reserved:
        return None

    independent = source.get("independent", True)
    groups = (source.get("group", source["id"]),) if independent else ()
    return Candidate(str(address), port, protocol, (source["id"],), groups)


async def fetch_source(
    session: aiohttp.ClientSession,
    source: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    error = "fetch failed"
    async with semaphore:
        for attempt in range(3):
            try:
                async with session.get(source["url"], allow_redirects=True) as response:
                    response.raise_for_status()
                    raw = await response.content.read(MAX_SOURCE_BYTES + 1)
                    if len(raw) > MAX_SOURCE_BYTES:
                        raise ValueError("source exceeds byte limit")
                    lines = raw.decode("utf-8-sig").splitlines()
                    if len(lines) > MAX_SOURCE_LINES:
                        raise ValueError("source exceeds line limit")
                    return {
                        **source, "status": "ok", "lines": lines,
                        "line_count": len(lines), "error": None,
                    }
            except (aiohttp.ClientError, TimeoutError, UnicodeError, ValueError) as exc:
                error = f"{type(exc).__name__}: {str(exc)[:160]}"
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2 ** attempt))
    return {
        **source, "status": "error", "lines": [],
        "line_count": 0, "error": error,
    }


async def collect_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeout = aiohttp.ClientTimeout(total=15, connect=6, sock_read=8)
    connector = aiohttp.TCPConnector(limit=12, ttl_dns_cache=300)
    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
        trust_env=False,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.5"},
    ) as session:
        semaphore = asyncio.Semaphore(6)
        return await asyncio.gather(
            *(fetch_source(session, item, semaphore) for item in sources)
        )


def merge_candidates(results: list[dict[str, Any]]) -> list[Candidate]:
    """Merge parsed endpoints and attach per-feed parse diagnostics in place."""
    merged: dict[str, Candidate] = {}
    for source in results:
        source.update({
            "candidate_lines": 0,
            "parsed_candidate_lines": 0,
            "unique_candidates": 0,
            "duplicate_lines": 0,
            "rejected_lines": 0,
            "parse_status": "no_candidates" if source["status"] == "ok" else "fetch_failed",
        })
        if source["status"] != "ok":
            continue

        local_keys: set[str] = set()
        for line in source["lines"]:
            cleaned = line.strip().split("#", 1)[0].strip().strip("\"'")
            if not cleaned or cleaned.startswith(("//", ";")):
                continue
            source["candidate_lines"] += 1
            candidate = parse_candidate(line, source)
            if candidate is None:
                source["rejected_lines"] += 1
                continue

            source["parsed_candidate_lines"] += 1
            if candidate.key in local_keys:
                source["duplicate_lines"] += 1
            local_keys.add(candidate.key)

            old = merged.get(candidate.key)
            if old:
                merged[candidate.key] = Candidate(
                    old.ip, old.port, old.protocol,
                    tuple(sorted(set(old.sources) | set(candidate.sources))),
                    tuple(sorted(set(old.source_groups) | set(candidate.source_groups))),
                )
            else:
                merged[candidate.key] = candidate

        source["unique_candidates"] = len(local_keys)
        source["parse_status"] = "ok" if local_keys else "no_candidates"
    return list(merged.values())


def select_candidates(
    candidates: list[Candidate], state: dict[str, Any], limit: int,
    now: datetime | None = None,
) -> list[Candidate]:
    now = now or datetime.now(UTC)
    window_key = now.strftime("%Y%m%d%H")
    repeatable = []
    cooling_down = set()
    for item in candidates:
        previous = state.get(item.key, {})
        if not isinstance(previous, dict):
            continue
        last_attempt = previous.get("last_attempt_at") or previous.get("last_seen")
        last_success = previous.get("last_seen")
        streak = int(previous.get("success_streak", 0))
        if not last_attempt:
            continue
        try:
            attempt_elapsed = now - datetime.fromisoformat(last_attempt.replace("Z", "+00:00"))
            success_elapsed = (
                now - datetime.fromisoformat(last_success.replace("Z", "+00:00"))
                if last_success else None
            )
        except (TypeError, ValueError):
            continue
        if attempt_elapsed < timedelta(minutes=45):
            # Avoid retesting both passing and failing candidates too frequently.
            # This keeps the repeat-observation interval meaningful and frees slots.
            cooling_down.add(item.key)
        elif (
            streak >= 1 and success_elapsed is not None
            and success_elapsed <= timedelta(hours=2, minutes=15)
        ):
            repeatable.append(item)
    repeatable.sort(
        key=lambda item: (-int(state[item.key].get("success_streak", 0)), item.key)
    )
    chosen = repeatable[: min(limit // 3, len(repeatable))]
    chosen_keys = {item.key for item in chosen}
    exploration = [
        item for item in candidates
        if item.key not in chosen_keys and item.key not in cooling_down
    ]
    exploration.sort(
        key=lambda item: hashlib.sha256(
            f"{window_key}:{item.key}".encode()
        ).hexdigest()
    )
    chosen.extend(exploration[: max(0, limit - len(chosen))])
    return chosen

async def validate_one(
    candidate: Candidate,
    semaphore: asyncio.Semaphore,
    proxy_connector: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return the canary result plus a low-cardinality failure reason."""
    async with semaphore:
        started = time.perf_counter()
        timeout = aiohttp.ClientTimeout(total=7, connect=4, sock_read=5)
        try:
            headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
            if candidate.protocol == "https":
                # TLS is negotiated with the proxy itself before the CONNECT tunnel.
                async with aiohttp.ClientSession(
                    timeout=timeout, trust_env=False, headers=headers
                ) as client:
                    async with client.get(
                        CHECK_URL, proxy=candidate.proxy, ssl=True, allow_redirects=False
                    ) as response:
                        body = await response.content.read(8192)
                        status = response.status
            else:
                # ProxyConnector supports HTTP, SOCKS4(a) and SOCKS5(h).
                connector = proxy_connector.from_url(candidate.proxy)
                async with aiohttp.ClientSession(
                    connector=connector, timeout=timeout, trust_env=False, headers=headers
                ) as client:
                    async with client.get(
                        CHECK_URL, ssl=True, allow_redirects=False
                    ) as response:
                        body = await response.content.read(8192)
                        status = response.status

            if status != 200:
                return None, f"http_status_{status}"
            body_text = body.decode("utf-8", errors="replace")
            if CHECK_MARKER not in body_text:
                return None, "canary_marker_missing"
            latency = round((time.perf_counter() - started) * 1000, 1)
            return {
                "candidate": candidate, "latency_ms": latency, "status_code": status
            }, None
        except TimeoutError:
            return None, "timeout"
        except Exception as exc:
            # Keep failures isolated, but make their classes observable in run stats.
            return None, type(exc).__name__


async def validate_all(
    candidates: list[Candidate], concurrency: int
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    from aiohttp_socks import ProxyConnector

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 250)))
    output: list[dict[str, Any]] = []
    failures: dict[str, int] = {}
    for start in range(0, len(candidates), 300):
        batch = candidates[start:start + 300]
        outcomes = await asyncio.gather(
            *(validate_one(item, semaphore, ProxyConnector) for item in batch)
        )
        for successful, failure_reason in outcomes:
            if successful is not None:
                output.append(successful)
            else:
                reason = failure_reason or "unknown"
                failures[reason] = failures.get(reason, 0) + 1
        completed = min(start + len(batch), len(candidates))
        print(f"Validated {completed}/{len(candidates)}; {len(output)} passed")

    print("Validation failure reasons: " + json.dumps(dict(sorted(failures.items()))))
    return output, failures


def score(latency: float, streak: int, diversity: int) -> int:
    """Rank quality using speed, repeat reliability and independent attribution."""
    speed = max(0.0, 60.0 - latency / 50.0)
    stability = min(max(streak, 0), 5) * 8.0
    source_points = min(max(diversity, 0), 3) * 4.0
    return max(0, min(100, round(speed + stability + source_points + 10)))


def update_records(
    passed: list[dict[str, Any]],
    previous: dict[str, Any],
    checked_at: str,
    attempted: list[Candidate] | None = None,
    previous_records: list[dict[str, Any]] | None = None,
    retain_unattempted: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checked_dt = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    state: dict[str, Any] = {}
    records = []
    for item in passed:
        candidate: Candidate = item["candidate"]
        prior = previous.get(candidate.key, {})
        if not isinstance(prior, dict):
            prior = {}
        last = prior.get("last_seen")
        try:
            elapsed = (
                checked_dt - datetime.fromisoformat(last.replace("Z", "+00:00"))
                if last else timedelta(days=999)
            )
        except (TypeError, ValueError):
            elapsed = timedelta(days=999)
        # Ignore pre-policy streaks: older versions counted slow successes.
        old_streak = (
            int(prior.get("success_streak", 0))
            if prior.get("quality_policy") == 2 else 0
        )
        if item["latency_ms"] > 1200:
            # Slow successes do not count toward the consecutive high-quality streak.
            streak = 0
        elif timedelta(minutes=45) <= elapsed <= timedelta(hours=2, minutes=15):
            streak = old_streak + 1 if old_streak else 1
        elif elapsed < timedelta(minutes=45) and old_streak:
            streak = old_streak
        else:
            streak = 1
        diversity = len(candidate.source_groups)
        quality = score(item["latency_ms"], streak, diversity)
        strong = quality >= 75 and streak >= 3 and item["latency_ms"] <= 900
        # Public output is fail-closed: first-time, slow and low-scoring endpoints
        # remain private to the short-lived state until they qualify.
        if streak >= 2 and item["latency_ms"] <= 1200 and quality >= 60:
            records.append({
                "ip": candidate.ip, "port": candidate.port, "address": candidate.address,
                "protocol": candidate.protocol, "proxy": candidate.proxy,
                "latency_ms": item["latency_ms"], "status": "verified",
                "status_code": item["status_code"], "supports_https": True,
                "quality_score": quality,
                "quality_tier": "strong" if strong else "verified",
                "success_streak": streak, "source_count": diversity,
                "sources": list(candidate.sources), "last_checked_at": checked_at,
            })
        state[candidate.key] = {
            "quality_policy": 2,
            "first_seen": prior.get("first_seen", checked_at),
            "last_seen": checked_at,
            "last_attempt_at": checked_at,
            "success_streak": streak,
            "total_passes": int(prior.get("total_passes", 0)) + 1,
            "last_latency_ms": item["latency_ms"],
            "sources": sorted(set(prior.get("sources", [])) | set(candidate.sources)),
        }

    attempted_keys = {candidate.key for candidate in (attempted or [])}
    for candidate in attempted or []:
        if candidate.key in state:
            continue
        prior = previous.get(candidate.key, {})
        if not isinstance(prior, dict):
            prior = {}
        state[candidate.key] = {
            **prior,
            "quality_policy": 2,
            "first_seen": prior.get("first_seen", checked_at),
            "last_attempt_at": checked_at,
            "success_streak": 0,
        }

    # Preserve a previously verified entry when this run did not sample it.
    # A selected failure is deliberately not retained: attempted_keys blocks it.
    if retain_unattempted:
        current_keys = {
            f'{item["protocol"]}|{item["ip"]}|{item["port"]}' for item in records
        }
        for prior_record in previous_records or []:
            try:
                key = (
                    f'{prior_record["protocol"]}|{prior_record["ip"]}|'
                    f'{int(prior_record["port"])}'
                )
                last_checked = datetime.fromisoformat(
                    prior_record["last_checked_at"].replace("Z", "+00:00")
                )
            except (KeyError, TypeError, ValueError):
                continue

            age = checked_dt - last_checked
            if (
                key in attempted_keys
                or key in current_keys
                or prior_record.get("status") != "verified"
                or int(prior_record.get("success_streak", 0)) < 2
                or float(prior_record.get("latency_ms", 999999)) > 1200
                or int(prior_record.get("quality_score", 0)) < 60
                or age < timedelta(0)
                or age > PUBLIC_RECORD_MAX_AGE
            ):
                continue

            records.append(dict(prior_record))
            current_keys.add(key)

    records.sort(
        key=lambda row: (-row["quality_score"], row["latency_ms"], row["address"])
    )

    # Retain state for 48 hours from the latest activity, not merely the last success.
    cutoff = checked_dt - timedelta(hours=48)
    for key, value in previous.items():
        if key in state or not isinstance(value, dict):
            continue
        try:
            timestamp = value.get("last_attempt_at") or value.get("last_seen")
            old_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        if old_time >= cutoff:
            # Not selected is not the same as failed. Preserve its repeat-success
            # streak; only an explicitly attempted failure resets the streak above.
            state[key] = dict(value)
    return records, state


def publication_hold_reasons(
    passed: list[dict[str, Any]],
    records: list[dict[str, Any]],
    state: dict[str, Any],
) -> dict[str, int]:
    """Explain why successful canary checks were not published in this snapshot."""
    published_keys = {
        f'{item["protocol"]}|{item["ip"]}|{item["port"]}' for item in records
    }
    reasons: dict[str, int] = {}
    for item in passed:
        candidate: Candidate = item["candidate"]
        if candidate.key in published_keys:
            continue

        latency = float(item["latency_ms"])
        streak = int(state.get(candidate.key, {}).get("success_streak", 0))
        if latency > 1200:
            reason = "latency_over_limit"
        elif streak < 2:
            reason = "repeat_validation_pending"
        elif score(latency, streak, len(candidate.source_groups)) < 60:
            reason = "quality_score_below_threshold"
        else:
            reason = "other_publication_policy"
        reasons[reason] = reasons.get(reason, 0) + 1

    return dict(sorted(reasons.items()))


def publish(
    records: list[dict[str, Any]],
    source_results: list[dict[str, Any]],
    state: dict[str, Any],
    checked_at: str,
    run_metrics: dict[str, Any] | None = None,
) -> None:
    run_metrics = run_metrics or {}
    strong = [item for item in records if item["quality_tier"] == "strong"]
    protocols = ("http", "https", "socks4", "socks5")
    by_protocol = {
        protocol: [item for item in records if item["protocol"] == protocol]
        for protocol in protocols
    }
    latencies = sorted(item["latency_ms"] for item in records)
    if len(latencies) % 2:
        median = round(latencies[len(latencies) // 2], 1)
    elif latencies:
        mid = len(latencies) // 2
        median = round((latencies[mid - 1] + latencies[mid]) / 2, 1)
    else:
        median = None

    healthy = [source for source in source_results if source["status"] == "ok"]
    groups = {
        source.get("group", source["id"])
        for source in healthy if source.get("independent", True)
    }
    configured_groups = {
        source.get("group", source["id"])
        for source in source_results if source.get("independent", True)
    }
    validation_attempted = int(run_metrics.get("validation_attempted", 0))
    validation_passed = int(run_metrics.get("validation_passed", len(records)))
    discovered_candidates = int(run_metrics.get(
        "discovered_candidates",
        sum(int(item.get("unique_candidates", 0)) for item in source_results),
    ))
    feeds_with_candidates = sum(
        1 for item in source_results if int(item.get("unique_candidates", 0)) > 0
    )
    feeds_without_candidates = sum(
        1 for item in source_results
        if item["status"] == "ok" and int(item.get("unique_candidates", 0)) == 0
    )
    fetch_failed = sum(1 for item in source_results if item["status"] != "ok")
    validation_failure_reasons = run_metrics.get("validation_failure_reasons", {})
    newly_verified = int(run_metrics.get("newly_verified", 0))
    retained_verified = max(0, len(records) - newly_verified)

    stats = {
        "generated_at": checked_at,
        "total_verified": len(records),
        "newly_verified": newly_verified,
        "retained_verified": retained_verified,
        "public_record_max_age_minutes": int(PUBLIC_RECORD_MAX_AGE.total_seconds() / 60),
        "discovered_candidates": discovered_candidates,
        "selected_candidates": int(run_metrics.get("selected_candidates", validation_attempted)),
        "validation_attempted": validation_attempted,
        "validation_passed": validation_passed,
        "validation_failed": max(0, validation_attempted - validation_passed),
        "validation_pass_rate_pct": (
            round(validation_passed / validation_attempted * 100, 1)
            if validation_attempted else 0.0
        ),
        "publication_yield_pct": (
            round(newly_verified / validation_attempted * 100, 1)
            if validation_attempted else 0.0
        ),
        "validation_failure_reasons": dict(sorted(validation_failure_reasons.items())),
        "publication_hold_reasons": dict(sorted(
            run_metrics.get("publication_hold_reasons", {}).items()
        )),
        "source_candidate_lines": sum(
            int(item.get("candidate_lines", 0)) for item in source_results
        ),
        "source_parsed_candidate_lines": sum(
            int(item.get("parsed_candidate_lines", 0)) for item in source_results
        ),
        "source_duplicate_lines": sum(
            int(item.get("duplicate_lines", 0)) for item in source_results
        ),
        "source_rejected_lines": sum(
            int(item.get("rejected_lines", 0)) for item in source_results
        ),
        "source_unique_candidates": sum(
            int(item.get("unique_candidates", 0)) for item in source_results
        ),
        "feeds_with_candidates": feeds_with_candidates,
        "feeds_without_candidates": feeds_without_candidates,
        "feeds_fetch_failed": fetch_failed,
        "strong_count": len(strong),
        "by_protocol": {protocol: len(by_protocol[protocol]) for protocol in protocols},
        "strong_by_protocol": {
            protocol: sum(
                1 for item in by_protocol[protocol] if item["quality_tier"] == "strong"
            )
            for protocol in protocols
        },
        "median_latency_ms": median,
        "source_count": len(groups),
        "sources_configured": len(configured_groups),
        "feed_count": len(healthy),
        "feeds_configured": len(source_results),
        "quality_definition": (
            "Published entries passed two checks at least 45 minutes apart, "
            "scored at least 60/100, completed the HTTPS canary request within 1200 ms, "
            "and were last checked within 90 minutes. Entries not sampled in this run "
            "are retained only inside that recheck window; sampled failures are removed."
        ),
        "minimum_quality_score": 60,
        "maximum_latency_ms": 1200,
        "minimum_success_streak": 2,
    }
    payload = {"generated_at": checked_at, "count": len(records), "proxies": records}
    source_payload = [
        {
            "id": item["id"], "group": item.get("group", item["id"]),
            "name": item.get("name"), "url": item["url"],
            "status": item["status"], "line_count": item["line_count"],
            "candidate_lines": item.get("candidate_lines", 0),
            "parsed_candidate_lines": item.get("parsed_candidate_lines", 0),
            "unique_candidates": item.get("unique_candidates", 0),
            "duplicate_lines": item.get("duplicate_lines", 0),
            "rejected_lines": item.get("rejected_lines", 0),
            "parse_status": item.get("parse_status", "fetch_failed"),
            "error": item["error"], "independent": item.get("independent", True),
        }
        for item in source_results
    ]

    history_file = ROOT / "data" / "history.json"
    history = read_json(history_file, [])
    if not isinstance(history, list):
        history = []
    history.append({
        "generated_at": checked_at,
        "total_verified": len(records),
        "newly_verified": newly_verified,
        "retained_verified": retained_verified,
        "strong_count": len(strong),
    })
    history = history[-168:]

    for folder in (ROOT / "proxies", ROOT / "site" / "files"):
        folder.mkdir(parents=True, exist_ok=True)

        def write_list(
            name: str,
            items: list[dict[str, Any]],
            schemes: bool = False,
            output_folder: Path = folder,
        ) -> None:
            lines = [
                item["proxy"] if schemes else item["address"]
                for item in items
            ]
            write_text(output_folder / name, "\n".join(lines) + ("\n" if lines else ""))

        write_list("all.txt", records, True)
        for protocol, items in by_protocol.items():
            write_list(f"{protocol}.txt", items)
        write_list("http-connect.txt", by_protocol["http"])
        write_list("strong.txt", strong, True)
        for protocol in protocols:
            protocol_strong = [
                item for item in by_protocol[protocol]
                if item["quality_tier"] == "strong"
            ]
            write_list(f"strong/{protocol}.txt", protocol_strong)
        write_json(folder / "proxies.json", payload)
        fields = [
            "proxy", "protocol", "ip", "port", "latency_ms", "quality_score",
            "quality_tier", "success_streak", "source_count", "last_checked_at",
        ]
        with (folder / "proxies.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for item in records:
                writer.writerow({key: item.get(key) for key in fields})

    for folder in (ROOT / "data", ROOT / "site" / "data"):
        write_json(folder / "stats.json", stats)
        write_json(folder / "proxies.json", payload)
        write_json(folder / "sources.json", {
            "generated_at": checked_at, "sources": source_payload
        })
    write_json(STATE_FILE, state)
    write_json(history_file, history)


def main() -> None:
    limit = max(1, min(int(os.getenv("ATLAS_MAX_CANDIDATES", "8000")), 30000))
    concurrency = max(1, min(int(os.getenv("ATLAS_CONCURRENCY", "50")), 250))
    sources = load_sources()
    previous_state = read_json(STATE_FILE, {})
    if not isinstance(previous_state, dict):
        previous_state = {}
    previous_payload = read_json(ROOT / "data" / "proxies.json", {})
    previous_records = (
        previous_payload.get("proxies", [])
        if isinstance(previous_payload, dict)
        and isinstance(previous_payload.get("proxies", []), list)
        else []
    )

    source_results = asyncio.run(collect_sources(sources))
    healthy = sum(1 for item in source_results if item["status"] == "ok")
    discovered = merge_candidates(source_results)
    # Fail closed: if feeds are unavailable or provide no usable candidates,
    # publish an empty snapshot rather than retaining old public entries.
    candidates = select_candidates(discovered, previous_state, limit)
    feeds_with_candidates = sum(
        1 for item in source_results if int(item.get("unique_candidates", 0)) > 0
    )
    print(
        f"Feeds fetched: {healthy}/{len(sources)}; "
        f"feeds with candidates: {feeds_with_candidates}/{healthy}; "
        f"unique candidates: {len(discovered)}; selected: {len(candidates)}"
    )
    passed, failure_reasons = asyncio.run(validate_all(candidates, concurrency))
    checked_at = now_iso()
    # Retention is allowed only when at least one feed produced a usable pool.
    # Complete feed/parse outages still publish an empty snapshot fail-closed.
    retain_unattempted = healthy > 0 and bool(discovered)
    records, state = update_records(
        passed,
        previous_state,
        checked_at,
        attempted=candidates,
        previous_records=previous_records,
        retain_unattempted=retain_unattempted,
    )
    passed_keys = {item["candidate"].key for item in passed}
    newly_verified = sum(
        1 for item in records
        if f'{item["protocol"]}|{item["ip"]}|{item["port"]}' in passed_keys
    )
    retained_verified = len(records) - newly_verified
    run_metrics = {
        "discovered_candidates": len(discovered),
        "selected_candidates": len(candidates),
        "validation_attempted": len(candidates),
        "validation_passed": len(passed),
        "validation_failure_reasons": failure_reasons,
        "publication_hold_reasons": publication_hold_reasons(passed, records, state),
        "newly_verified": newly_verified,
        "retained_verified": retained_verified,
    }
    publish(records, source_results, state, checked_at, run_metrics=run_metrics)
    print(
        f"Published {len(records)} verified endpoints; "
        f"{sum(item['quality_tier'] == 'strong' for item in records)} strong"
    )


if __name__ == "__main__":
    main()
