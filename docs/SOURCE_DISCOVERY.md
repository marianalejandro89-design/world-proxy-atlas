# Source discovery register

Last reviewed: 2026-11-12

Active catalogue: 47 public feed endpoints across 18 publisher groups. Ten groups are independent publishers; eight aggregator groups are intentionally marked independent=false because their data is drawn from public upstream proxy pools and can overlap other configured feeds. Multiple protocol shards from one publisher count once.

## Active publishers

| Publisher | Feeds | Notes |
|---|---:|---|
| monosans/proxy-list | 1 | Mixed protocol feed |
| litportnet/free-proxy-list | 1 | Live branch |
| proxy-free/free-proxy-list | 3 | HTTP, SOCKS4, SOCKS5 |
| hproxy-com/free-proxy-list + keyless API | 7 | HTTP, HTTPS proxy transport, SOCKS4, SOCKS5 and three protocol-filtered TXT API endpoints |
| maximilianfeix/free-proxy-list | 1 | Mixed protocol feed |
| ProxyScrape/free-proxy-list | 1 | Its HTTPS label is normalized to HTTP CONNECT from its documented SSL semantics |
| proxifly/free-proxy-list | 1 | Mixed protocol feed |
| proxmint/free-proxy-list | 1 | Its HTTPS subset is HTTP CONNECT |
| proxio-io/proxy-list | 4 | HTTP, CONNECT-capable HTTP, SOCKS4, SOCKS5 |
| Thordata/awesome-free-proxy-list | 4 | HTTP, CONNECT-capable HTTP, SOCKS4, SOCKS5 |
| Proxy Scraper keyless API | 4 | Non-GitHub API with known overlap, excluded from independent count |
| Stormsia proxy-list | 1 | 15-minute validator, mixed protocol feed; treated as aggregator |
| ProxyNest | 3 | Keyless text exports for HTTP, SOCKS4 and SOCKS5; fair-use limited |
| ProxyWing | 1 | Keyless live export; HTTPS category explicitly means HTTP CONNECT |\n| Relayglass | 4 | HTTP, HTTP CONNECT, SOCKS4 and SOCKS5; independently checked upstream aggregate, excluded from diversity scoring |\n| VMHeaven | 4 | HTTP, HTTP CONNECT, SOCKS4 and SOCKS5; refreshed aggregate feeds, excluded from diversity scoring |\n| dpangestuw/Free-Proxy | 3 | HTTP, SOCKS4 and SOCKS5; mixed upstream aggregate, excluded from diversity scoring |\n| VPSLab | 3 | HTTP, SOCKS4 and SOCKS5; 15-minute aggregate feeds, excluded from diversity scoring |

## Discovery languages

Searches included English, 简体中文, Русский, Español, Português, Bahasa Indonesia, Deutsch, Français, 日本語 and 한국어 terms for free proxy lists, SOCKS4/5 feeds, open proxy pools and machine-readable APIs.

Candidate feeds were not activated merely because they appeared in search results. They needed a stable public text/JSON endpoint, bounded response size, interpretable protocol semantics, recent update evidence and documented usage/attribution conditions.

## Excluded pending more evidence

- HankNovic/ProxyClean — Chinese SOCKS5-only source; ongoing refresh cadence was not confirmed.
- iplocate/free-proxy-list — last checked README timestamp was 2025-07-09; stale for a frequently refreshed catalogue.
- fyvri/fresh-proxy-list — claims hourly updates, but archive feeds could not be verified within safe size/format limits at review time.
- gproxynet/free-proxy-list — checked snapshot reported zero entries.
- TheSpeedX/SOCKS-List — raw-file metadata was not consistently retrievable via the review integration.
- ProxyNest and ProxyLister — public download pages were found, but stable documented feed URLs and schemas were not confirmed; this project avoids scraping HTML UIs.\n- roosterkid/openproxylist — current raw-list update cadence could not be established confidently enough for automated inclusion.

## Additional non-GitHub sources

HProxy documents a keyless `/api/proxy-list` endpoint supporting TXT, JSON and CSV output, with a fair-use allowance of about 120 requests per minute. The repository already consumes HProxy's GitHub mirrors; the API is assigned to the same `hproxy` publisher group so it does not inflate source diversity.


ProxyNest documents `/api/export?format=txt&protocol=...` for HTTP, SOCKS4 and SOCKS5, with a 60-requests/minute fair-use cap and caching guidance. ProxyWing documents `/api/free-proxy-list?format=txt&proxy_format=protocolipport`, also keyless and limited to 60 requests/minute; its HTTPS category is HTTP CONNECT, not TLS-to-proxy. Stormsia documents `working_proxies.txt`, protocol-specific text lists and a 15-minute validation daemon. These three publishers are useful discovery feeds but are excluded from the independent-source count to avoid overstating diversity.

## API and attribution

The keyless API at https://api.proxyscraper.com/v1/ documents HTTP and SOCKS5 feeds plus stable sublists, a 60 requests/minute limit and CC BY 4.0 data terms. Its docs identify overlap with proxio.io and proxmint, so its four feeds are requested every five minutes but excluded from source-diversity scoring. This API is distinct from api.proxyscrape.com.

Before adding a source, re-check its current URL, terms, protocol definition, update cadence, response size and upstream overlap.
\n## Validation target resilience\n\nEach candidate is tested against two HTTPS canaries (`example.com` and Cloudflare Trace). A destination-specific block or outage on the first canary can therefore be distinguished from a dead proxy. A pass still requires HTTP 200 and the exact expected marker from one canary. The public latency ceiling, two successful observations at least 45 minutes apart, strong-tier rules, and fail-closed publication behavior are unchanged. Only the successful request duration is used for the latency decision.\n\nThe VMHeaven, dpangestuw and VPSLab endpoints were checked as live raw text feeds on 2026-11-12. Their repositories describe 15-minute or frequent refresh cycles. They are explicitly treated as aggregate discovery inputs, not independent provenance, and all candidates remain subject to this project's own protocol-aware validation and repeat-success policy. Their repository metadata did not declare a license at review time; only the public feed data is consumed, no upstream source code is copied, and users should consult each publisher's current terms.\n