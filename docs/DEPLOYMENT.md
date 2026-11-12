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

The refresh workflow runs hourly at minute 17 UTC and can also be started manually. GitHub-hosted schedules are best-effort; a delayed or missed run is possible.

Each run:

1. Fetches the configured public feeds with bounded time, size and concurrency limits.
2. Parses and deduplicates literal public-IP candidates.
3. Tests each selected candidate with a bounded HTTPS canary request.
4. Updates short-lived repeat-success state.
5. Publishes only endpoints with at least two consecutive successful checks, latency at or below 1,500 ms, and a quality score of at least 55/100.
6. Marks Strong endpoints only at score 75 or higher, at least three consecutive successes, and latency at or below 900 ms.
7. Runs production snapshot assertions before committing generated data and deploying Pages.

A failed, slow, low-scoring or stale endpoint is omitted from the next snapshot. If feeds are unavailable or no endpoint qualifies, the run publishes an empty snapshot instead of keeping old entries visible. Validation state is retained for no more than 48 hours.

## Operational verification

Review the latest run under the repository's **Actions** tab. A production refresh should complete these steps successfully:

- Unit tests
- Candidate collection and validation
- Production snapshot checks
- Generated data commit
- GitHub Pages preflight and deployment

If the Pages preflight fails, confirm that Pages source is set to **GitHub Actions**. If a snapshot is older than 90 minutes, the dashboard hides the directory and download links until a fresh snapshot is available.

A successful validation is a point-in-time connectivity measurement, not a guarantee of uptime, anonymity, privacy or operator trust.
