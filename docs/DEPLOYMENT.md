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

The refresh workflow is configured for five-minute intervals at UTC minutes 2, 7, 12, …, 57, offset from the start of each hour to reduce contention. It can also be started manually on `main`. GitHub-hosted schedules are best-effort; delayed or missed runs are possible during high load. Runs are serialized rather than cancelling an active refresh, and candidates checked within the last 45 minutes are held out of selection so frequent schedules do not reset the repeat-observation interval.

Each run:

1. Fetches the configured public feeds with bounded time, size and concurrency limits.
2. Parses and deduplicates literal public-IP candidates.
3. Tests each selected candidate with a bounded HTTPS canary request.
4. Updates short-lived repeat-success state.
5. Publishes only endpoints with at least two fast successful checks separated by at least 45 minutes, latency at or below 1,200 ms, and a quality score of at least 60/100.
6. Marks Strong endpoints only at score 75 or higher, at least three fast successful checks, each separated by at least 45 minutes, and latency at or below 900 ms.
7. Runs production snapshot assertions before committing generated data and deploying Pages.

A failed, slow, low-scoring or stale endpoint is omitted from the next snapshot. If feeds are unavailable or no endpoint qualifies, the run publishes an empty snapshot instead of keeping old entries visible. Validation state is retained for no more than 48 hours.

## Operational verification

Review the latest run under the repository's **Actions** tab. A production refresh should complete these steps successfully:

- Unit tests
- Candidate collection and validation
- Production snapshot checks
- Generated data commit
- GitHub Pages preflight and deployment

If the Pages preflight fails, confirm that Pages source is set to **GitHub Actions**. If a snapshot is older than 90 minutes, the dashboard hides the directory and download links until a fresh snapshot is available. The current `data/stats.json` records discovered candidates, attempted validations, pass/failure totals and publication yield; `data/sources.json` records per-feed fetch and parse counts. The production workflow also checks that the JSON, CSV and TXT downloads match the same generated snapshot.

A successful validation is a point-in-time connectivity measurement, not a guarantee of uptime, anonymity, privacy or operator trust.
