# World Proxy Atlas

**Global proxy discovery · Independent validation · Hourly refresh · Open data**

[![Refresh + deploy](https://github.com/marianalejandro89-design/world-proxy-atlas/actions/workflows/refresh-and-pages.yml/badge.svg)](https://github.com/marianalejandro89-design/world-proxy-atlas/actions/workflows/refresh-and-pages.yml)
[![CI](https://github.com/marianalejandro89-design/world-proxy-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/marianalejandro89-design/world-proxy-atlas/actions/workflows/ci.yml)

World Proxy Atlas collects candidates from public, machine-readable feeds, independently tests them, and publishes protocol-separated TXT lists plus JSON/CSV metadata. Public feed entries are untrusted candidates, never assumed to be alive.

- Protocols: HTTP, HTTPS-to-proxy, SOCKS4 and SOCKS5. HTTP CONNECT-capable endpoints are reported as HTTP, not mislabeled as TLS-to-proxy HTTPS.
- Quality: public-IP validation, bounded connection checks, latency scoring, repeat-success streaks and independent-source attribution.
- Automation: hourly GitHub Actions schedule, manual workflow trigger, tests, protected snapshot publishing and GitHub Pages deployment.
- No paid services or API keys required. Free-tier quotas, schedule delays and runner availability still apply.

## Website

Once the first workflow run deploys Pages: https://marianalejandro89-design.github.io/world-proxy-atlas/

## Downloads

Generated files are committed under proxies/ and mirrored under site/files/:

| File | Purpose |
|---|---|
| proxies/all.txt | All verified endpoints with protocol scheme |
| proxies/http.txt, https.txt, socks4.txt, socks5.txt | Protocol-specific lists |
| proxies/http-connect.txt | HTTP endpoints that passed an HTTPS destination test |
| proxies/strong.txt and proxies/strong/ | Repeat-tested strong tier, split by protocol |
| proxies/proxies.json and proxies/proxies.csv | Full metadata and spreadsheet export |
| data/stats.json | Latest run metrics |
| data/sources.json | Feed health and attribution |

A proxy may go offline immediately after validation. A successful check does not prove operator trustworthiness, anonymity, privacy or future uptime. Do not transmit credentials, cookies, API keys, payment data or personal information through public proxies.

## Collection catalogue

The catalogue contains 30 endpoints across 14 publisher groups: 10 independently counted publishers plus four non-independent aggregator groups. Multiple protocol files from one publisher count as one group. See config/sources.toml and docs/SOURCE_DISCOVERY.md.

Sources were discovered using English, Simplified Chinese, Russian, Spanish, Portuguese, Indonesian, German, French, Japanese and Korean search terms. Feeds are enabled only when endpoint, format, update signal and protocol meaning can be documented.

## Quality policy

1. Fetch sources over HTTPS with byte and line limits.
2. Parse literal IP endpoints only. Reject credentials, hostnames, private/reserved ranges and non-allowlisted ports.
3. Deduplicate by protocol, IP and port, preserving source attribution.
4. Validate with one bounded HTTPS request to a fixed canary URL and verify its expected response.
5. Score latency, repeat success and independent source diversity.
6. Promote an endpoint to strong only after repeat success, a score threshold and a latency ceiling.
7. Never replace a known-good snapshot when every source fetch fails or a run validates no candidates despite an existing inventory.

The score is a ranking heuristic, not a security grade. Country, ISP and anonymity are not inferred without trustworthy measurement.

## Automation

GitHub Actions runs at minute 17 of every hour (UTC) and can be started from Actions → Refresh proxy data & deploy dashboard → Run workflow. Scheduled workflows are best-effort and can be delayed by GitHub; they are not a 100% uptime guarantee.

## Local development

Python 3.11 or newer:

    python -m venv .venv
    source .venv/bin/activate
    python -m pip install -e ".[test]"
    python -m pytest -q
    python -m atlas.pipeline

## Replit

The repository includes .replit and replit.nix for previewing the static dashboard. Replit is a development preview only; GitHub Actions is the scheduled runner.

## Responsible use and source terms

Use public proxies only where permitted by applicable law, service terms and network policies. Do not use this project to evade access controls, bans or rate limits. The MIT license applies only to this project's own code; upstream data remains subject to each publisher's terms. See SECURITY.md, CONTRIBUTING.md and LICENSE.
