# Deployment and one-time GitHub Pages setup

The repository and Actions automation are configured. A first-time Pages site must be enabled in repository settings by an administrator; the default `GITHUB_TOKEN` cannot enable Pages for a repository that has never had Pages configured.

1. Open the repository's **Settings** tab.
2. Open **Pages** in the left navigation.
3. Under **Build and deployment**, set **Source** to **GitHub Actions** and save.
4. Open **Actions → Refresh proxy data and deploy dashboard**.
5. Choose **Run workflow** on the `main` branch.

After that first successful deployment, the hourly workflow automatically refreshes the verified proxy files and republishes the dashboard.

Expected URL:

https://marianalejandro89-design.github.io/world-proxy-atlas/

The first completed refresh found 213 verified endpoints from its initial 30-feed catalogue. No endpoint was promoted to the strong tier on its first observation; repeat-success scoring deliberately waits for a later validation run. The expanded catalogue now has 33 feed endpoints across 14 publisher groups, including new discovery sources.

If the deployment workflow fails at the Pages preflight step, it means the source setting above has not yet been saved or the account/repository policy prevents Pages publishing. Check the repository's Pages settings and the Actions log before retrying.
