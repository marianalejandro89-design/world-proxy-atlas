# World Proxy Atlas

**Public proxy discovery · Independent validation · Quality-gated publishing**

[![Refresh + deploy](https://github.com/marianalejandro89-design/world-proxy-atlas/actions/workflows/refresh-and-pages.yml/badge.svg)](https://github.com/marianalejandro89-design/world-proxy-atlas/actions/workflows/refresh-and-pages.yml)
[![CI](https://github.com/marianalejandro89-design/world-proxy-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/marianalejandro89-design/world-proxy-atlas/actions/workflows/ci.yml)

World Proxy Atlas collects candidates from public machine-readable feeds, validates them independently, and publishes only endpoints that meet the current quality policy. Feed entries are untrusted candidates and are never treated as working proxies without a successful check.

## Public dashboard

https://marianalejandro89-design.github.io/world-proxy-atlas/

GitHub Pages must be configured once under **Settings → Pages → Build and deployment → Source → GitHub Actions**. After that, the scheduled workflow builds and deploys the dashboard.

## Publication policy

An endpoint is included in public TXT, JSON and CSV output only when all of these conditions are met:

1. It is a literal public IP address and uses an allowlisted port. Hostnames, credentials and private/reserved addresses are rejected.
2. A bounded HTTPS request succeeds against at least one of two independent canary destinations and returns that destination's expected response marker.
3. It passes at least two fast validation cycles separated by at least 45 minutes.
4. Its measured request latency is no more than 1,200 ms.

The quality score is a ranking signal based on response speed, repeat reliability and independent source attribution. The current formula places any otherwise eligible endpoint above the 60/100 defensive floor, so that floor does not act as a separate filter.

The **Strong** tier requires three or more fast successful checks, each separated by at least 45 minutes, and latency no more than 900 ms. The displayed score helps rank endpoints within those tiers.

Failed, slow, low-scoring, first-seen and stale endpoints are excluded from the published snapshot. An endpoint not selected in a particular batch remains in the snapshot only until its last successful check reaches 90 minutes; if it is selected and fails, it is removed immediately. When all feeds fail or none yields usable candidates, the current public snapshot is emptied rather than retaining old entries. The internal validation state is retained for at most 48 hours to support repeat checks.

These checks reduce stale and unreliable entries; they cannot guarantee that a proxy remains online after publication. Public proxy operators are untrusted. A successful connectivity test is not proof of anonymity, privacy, security or operator integrity.

## Downloads

All files are generated from the same quality-gated snapshot:

| File | Description |
|---|---|
| `proxies/all.txt` | All qualified endpoints with protocol schemes |
| `proxies/http.txt`, `https.txt`, `socks4.txt`, `socks5.txt` | Protocol-specific lists |
| `proxies/http-connect.txt` | HTTP proxies that passed an HTTPS destination test |
| `proxies/strong.txt` and `proxies/strong/` | Strong-tier endpoints |
| `proxies/proxies.json`, `proxies/proxies.csv` | Full metadata and spreadsheet export |
| `data/stats.json` | Current snapshot metrics |
| `data/sources.json` | Feed fetch health, parsed/unique candidates, duplicates and rejected lines per feed |

HTTP CONNECT-capable proxies are labelled HTTP, not TLS-to-proxy HTTPS. The HTTPS protocol category is reserved for endpoints where TLS is negotiated with the proxy itself.

## Automation and checks

GitHub Actions is configured for a five-minute cadence at UTC minutes 2, 7, 12, …, 57 (offset from the start of each hour to reduce scheduler contention). You can also start it manually from **Actions → Refresh proxy data and deploy dashboard → Run workflow** on `main`. Scheduled events remain best-effort: GitHub may delay or drop them during high load, so this is not a guaranteed five-minute service-level interval. The dashboard and `data/stats.json` expose the latest discovery count, validation attempts, pass rate, failure categories, publication yield and successful checks held for repeat observation or latency policy. Per-feed parse diagnostics are available in `data/sources.json`. Validation state prevents checks less than 45 minutes apart from counting as repeat successes; candidates are held out during that cooldown.

Each refresh runs unit tests, collects and validates candidates, checks the generated snapshot against the publication policy, commits the generated data, and deploys the static site. CI also runs the test suite and Ruff lint checks on changes.

## Sources and responsible use

The catalogue contains public text/API feeds across multiple publishers and languages. Sources are grouped by publisher so protocol-specific mirrors do not inflate independent-source counts. See `config/sources.toml` and `docs/SOURCE_DISCOVERY.md` for the current source register and protocol notes.

Use public proxies only where permitted by applicable law, service terms and network policies. Do not use this project to evade access controls, bans or rate limits. Never transmit passwords, cookies, API keys, payment details or personal information through public proxies. Upstream data remains subject to each publisher's terms; the MIT license applies to this repository's own code.

## Security

The collector applies response-size, line-count, timeout and concurrency limits. Validation uses two HTTPS canaries to reduce destination-specific false negatives; this does not change the latency or repeat-success publication gates. It validates literal public IP endpoints and rejects credentials, hostnames, private/reserved ranges and non-allowlisted ports. See [SECURITY.md](SECURITY.md) for the security policy and [CONTRIBUTING.md](CONTRIBUTING.md) for source catalogue changes.
