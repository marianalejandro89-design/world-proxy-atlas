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
# Production HTTPS canaries operated by two independent providers.
# Google robots.txt is a stable plain-text 200 response; Cloudflare Trace is
# a documented diagnostic endpoint that includes the request's egress IP.
# Either exact marker proves that the proxy completed a real TLS HTTPS request.
CHECK_TARGETS = (
    ("https://www.google.com/robots.txt", "User-agent: *"),
    ("https://www.cloudflare.com/cdn-cgi/trace", "ip="),
)
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
MAX_VALIDATION_CONCURRENCY = 160


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
        raw = path.read_text(encoding="utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Recover the legacy compact-state writer's literal "\\n" suffix.
            # This is intentionally restricted to state.json; other malformed
            # JSON remains a hard warning and falls back to the supplied value.
            if path.name == "state.json" and raw.endswith("\\n"):
                recovered = json.loads(raw[:-2])
                print(
                    "WARNING: recovered state.json from a legacy literal newline "
                    "suffix; the corrected writer will normalize it."
                )
                return recovered
            raise
    except FileNotFoundError:
        print(f"INFO: {path.name} does not exist yet; using the initial empty state.")
        return fallback
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: could not read {path.name} "
            f"({type(exc).__name__}: {str(exc)[:160]}); using fallback data."
        )
        return fallback


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(value, encoding="utf-8", newline="\n")
    tmp.replace(path)


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def load_sources() -> list[dict[str, Any]]:
    """Load and validate the production HTTPS feed catalogue before network access."""
    with SOURCE_CONFIG.open("rb") as stream:
        payload = tomllib.load(stream)
    sources = payload.get("sources", [])
    if not sources:
        raise RuntimeError("No source feeds configured")

    ids: list[str] = []
    reserved_hosts = {"example.com", "example.org", "example.net", "localhost"}
    for source in sources:
        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise RuntimeError("Every source must have a non-empty string ID")
        ids.append(source_id)

        url = source.get("url")
        if not isinstance(url, str):
            raise RuntimeError(f"Source {source_id!r} has no valid URL")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme.lower() != "https"
            or not host
            or parsed.username
            or parsed.password
            or any(
                host == reserved or host.endswith("." + reserved)
                for reserved in reserved_hosts
            )
            or host.endswith((".example", ".invalid", ".test", ".localhost"))
        ):
            raise RuntimeError(
                f"Source {source_id!r} must use a real public HTTPS feed URL"
            )

        default_protocol = source.get("default_protocol")
        if default_protocol is not None and default_protocol not in PROTOCOLS:
            raise RuntimeError(
                f"Source {source_id!r} has unsupported default protocol "
                f"{default_protocol!r}"
            )
        overrides = source.get("scheme_overrides", {})
        if not isinstance(overrides, dict) or any(
            protocol not in PROTOCOLS for protocol in overrides.values()
        ):
            raise RuntimeError(f"Source {source_id!r} has invalid scheme overrides")
        if not isinstance(source.get("independent", True), bool):
            raise RuntimeError(f"Source {source_id!r} has invalid independence flag")

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


async def read_bounded_body(content: Any, limit: int = MAX_SOURCE_BYTES) -> bytes:
    """Read the complete response in chunks without ever exceeding the byte cap."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > limit:
            raise ValueError("source exceeds byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_source(
    session: aiohttp.ClientSession,
    source: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    error = "fetch failed"
    async with semaphore:
        for attempt in range(3):
            try:
                async with session.get(source["url"], allow_redirects=False) as response:
                    response.raise_for_status()
                    if 300 <= response.status < 400:
                        raise ValueError("source redirects are disabled")
                    if response.url.scheme.lower() != "https":
                        raise ValueError("source redirected away from HTTPS")
                    raw = await read_bounded_body(response.content)
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


def pending_recheck_candidates(
    state: dict[str, Any],
    discovered: list[Candidate],
    now: datetime | None = None,
) -> list[Candidate]:
    """Recover pending successful candidates even when rotating feeds omit them."""
    now = now or datetime.now(UTC)
    discovered_keys = {item.key for item in discovered}
    recovered: list[Candidate] = []
    for key, value in state.items():
        if (
            key in discovered_keys
            or not isinstance(value, dict)
            or value.get("quality_policy") != 2
            or int(value.get("success_streak", 0)) < 1
        ):
            continue
        last_success = value.get("last_seen")
        if not isinstance(last_success, str):
            continue
        try:
            age = now - datetime.fromisoformat(last_success.replace("Z", "+00:00"))
            protocol, ip, raw_port = key.split("|", 2)
            port = int(raw_port)
        except (TypeError, ValueError):
            continue
        if age < timedelta(0) or age > timedelta(hours=2, minutes=15):
            continue
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (
            protocol not in PROTOCOLS
            or not address.is_global
            or address.is_multicast
            or address.is_reserved
            or not 1 <= port <= 65535
        ):
            continue
        sources = value.get("sources", ())
        groups = value.get("source_groups", ())
        recovered.append(Candidate(
            str(address), port, protocol,
            tuple(item for item in sources if isinstance(item, str))
                if isinstance(sources, (list, tuple)) else (),
            tuple(item for item in groups if isinstance(item, str))
                if isinstance(groups, (list, tuple)) else (),
        ))
    return recovered


def canary_response_valid(status: int, body: bytes, marker: str) -> bool:
    """Require a successful response carrying the target-specific canary marker."""
    return status == 200 and marker.encode("ascii") in body


async def validate_one(
    candidate: Candidate,
    semaphore: asyncio.Semaphore,
    proxy_connector: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Check a proxy against alternate HTTPS canaries without relaxing quality gates."""
    async with semaphore:
        last_failure = "unknown"
        for check_url, marker in CHECK_TARGETS:
            started = time.perf_counter()
            timeout = aiohttp.ClientTimeout(total=8, connect=4, sock_read=6)
            headers = {"User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.8"}
            try:
                if candidate.protocol == "https":
                    # TLS is negotiated with the proxy itself before the CONNECT tunnel.
                    async with aiohttp.ClientSession(
                        timeout=timeout, trust_env=False, headers=headers
                    ) as client:
                        async with client.get(
                            check_url, proxy=candidate.proxy, ssl=True,
                            allow_redirects=False,
                        ) as response:
                            body = await response.content.read(8192)
                            status = response.status
                else:
                    # ProxyConnector supports HTTP, SOCKS4(a) and SOCKS5(h).
                    connector = proxy_connector.from_url(candidate.proxy)
                    async with aiohttp.ClientSession(
                        connector=connector, timeout=timeout, trust_env=False,
                        headers=headers,
                    ) as client:
                        async with client.get(
                            check_url, ssl=True, allow_redirects=False
                        ) as response:
                            body = await response.content.read(8192)
                            status = response.status

                if not canary_response_valid(status, body, marker):
                    last_failure = (
                        f"http_status_{status}" if status != 200
                        else "canary_marker_missing"
                    )
                    continue

                # Measure the successful request only. A blocked first canary must
                # not inflate the latency of a successful alternate destination.
                latency = round((time.perf_counter() - started) * 1000, 1)
                return {
                    "candidate": candidate, "latency_ms": latency, "status_code": status
                }, None
            except TimeoutError:
                last_failure = "timeout"
            except aiohttp.ClientConnectorError:
                last_failure = "connect_error"
            except aiohttp.ClientError as exc:
                last_failure = type(exc).__name__
            except Exception as exc:
                # Keep failures isolated, but make their classes observable in run stats.
                last_failure = type(exc).__name__

        return None, last_failure


async def validate_all(
    candidates: list[Candidate], concurrency: int
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    from aiohttp_socks import ProxyConnector

    semaphore = asyncio.Semaphore(max(1, min(concurrency, MAX_VALIDATION_CONCURRENCY)))
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
            "last_seen": checked_at,
            "last_attempt_at": checked_at,
            "success_streak": streak,
            "sources": list(candidate.sources),
            "source_groups": list(candidate.source_groups),
        }

    attempted_keys = {candidate.key for candidate in (attempted or [])}
    for candidate in attempted or []:
        if candidate.key in state:
            continue
        prior = previous.get(candidate.key, {})
        if not isinstance(prior, dict):
            prior = {}
        state[candidate.key] = {
            "quality_policy": 2,
            "last_seen": prior.get("last_seen"),
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
            state[key] = {
                field: value[field]
                for field in (\n                    "quality_policy", "last_seen", "last_attempt_at", "success_streak",\n                    "sources", "source_groups",\n                )
                if field in value
            }
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
        for source in healthy
        if source.get("independent", True)
        and int(source.get("unique_candidates", 0)) > 0
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
        "recheck_candidates": int(run_metrics.get("recheck_candidates", 0)),
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
            "completed the HTTPS canary request within 1200 ms, and were last checked "
            "within 90 minutes. The quality score ranks endpoints using speed, repeat "
            "reliability and source diversity; the 60/100 floor is defensive, not an "
            "independent filter under the current scoring formula. Entries not sampled "
            "in this run are retained only inside that recheck window; sampled failures "
            "are removed."
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
    write_state(STATE_FILE, state)
    write_json(history_file, history)


def write_state(path: Path, state: dict[str, Any]) -> None:
    """Persist only validation-policy fields in compact, valid JSON."""
    write_text(path, json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")


def main() -> None:
    concurrency = max(
        1,
        min(
            int(os.getenv("ATLAS_CONCURRENCY", str(MAX_VALIDATION_CONCURRENCY))),
            MAX_VALIDATION_CONCURRENCY,
        ),
    )
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
    pending_rechecks = pending_recheck_candidates(previous_state, discovered)
    validation_pool = discovered + pending_rechecks
    candidates = select_candidates(validation_pool, previous_state, len(validation_pool))
    feeds_with_candidates = sum(
        1 for item in source_results if int(item.get("unique_candidates", 0)) > 0
    )
    print(
        f"Feeds fetched: {healthy}/{len(sources)}; "
        f"feeds with candidates: {feeds_with_candidates}/{healthy}; "
        f"unique candidates: {len(discovered)}; "
        f"pending rechecks recovered: {len(pending_rechecks)}; "
        f"selected: {len(candidates)}"
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
        "recheck_candidates": len(pending_rechecks),
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
