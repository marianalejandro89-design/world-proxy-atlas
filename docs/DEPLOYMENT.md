# Deployment and operations

## GitHub Pages

A repository administrator must enable Pages once:

1. Open repository **Settings → Pages**.
2. Under **Build and deployment**, choose **GitHub Actions** as the source.
3. Save the setting.
4. Open **Actions → Refresh proxy data and deploy dashboard** and run it on `main`.

The public dashboard is published at:

https://marianalejandro89-design.github.io/world-proxy-atlas/

## Scheduled refresh

The refresh workflow is configured for an hourly run at UTC minute 2. It can also be started manually on `main`. Each run selects the entire deduplicated candidate pool returned by all healthy feeds rather than applying a small sample cap. Validation is processed in batches with a maximum of 160 concurrent checks, and the job timeout is 60 minutes. GitHub-hosted schedules are best-effort; delayed or missed runs are possible during high load. Runs are serialized rather than cancelling an active refresh, and candidates checked within the last 45 minutes are held out of selection so repeat observations remain meaningfully separated.

Each run:

1. Validates the source catalogue (real public HTTPS URLs, supported protocols and unique IDs), then fetches feeds with bounded time, streamed byte-size, line-count and concurrency limits. HTTP redirects are disabled entirely so a feed cannot downgrade transport or redirect the collector to an unintended host.
2. Parses and deduplicates literal public-IP candidates.
3. Tests each selected candidate against Google's live `robots.txt` HTTPS endpoint and Cloudflare Trace as fallback. TLS certificates are verified; one target must return HTTP 200 and its expected plain-text marker.
4. Updates short-lived repeat-success state.
5. Publishes endpoints with at least two fast successful checks separated by at least 45 minutes and latency at or below 1,200 ms. The quality score ranks candidates using speed, repeat reliability and independent source attribution; its current 60/100 floor is a defensive invariant rather than a separate selection gate.
6. Marks Strong endpoints after at least three fast successful checks, each separated by at least 45 minutes, and latency at or below 900 ms. The score remains a ranking signal within the published set.
7. Runs production snapshot assertions before committing generated data and deploying Pages.

A failed, slow or low-scoring endpoint is omitted immediately when it is sampled. A previously qualified endpoint that is not selected in the current batch remains available only until its last successful check reaches 90 minutes; an explicit failed recheck removes it immediately. If feeds are unavailable or none yields usable candidates, the run publishes an empty snapshot instead of keeping old entries visible. Validation state is retained for no more than 48 hours.

## Operational verification

Review the latest run under the repository's **Actions** tab. A production refresh should complete these steps successfully:

- Unit tests
- Candidate collection and validation
- Production snapshot checks
- Generated data commit
- GitHub Pages preflight and deployment

If the Pages preflight fails, confirm that Pages source is set to **GitHub Actions**. If a snapshot is older than 90 minutes, the dashboard hides the directory and download links until a fresh snapshot is available. The current `data/stats.json` records discovered candidates, attempted validations, pass/failure totals, publication yield and reasons successful checks were held out of publication; `data/sources.json` records per-feed fetch and parse counts. The production workflow also checks that the JSON, CSV and TXT downloads match the same generated snapshot.

A successful validation is a point-in-time connectivity measurement, not a guarantee of uptime, anonymity, privacy or operator trust.
