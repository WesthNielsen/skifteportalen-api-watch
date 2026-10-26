# Skifteportalen API specification watcher (Render)

Runs once daily on **Render Cron Jobs** to check the [Skifteportalen API integration page](https://www.domstol.dk/selvbetjening/blanketter-og-vejledninger/minskiftesagdk/api-tilslutning-til-skifteportalen/) and its linked API/integration documents. Tracks changes to extracted page text and linked PDFs, JSON, YAML, ZIP, WSDL and XSD files.

## What it detects

- Edits to the main article text (navigation, header, footer and scripts excluded).
- Added or removed relevant document links.
- Changes in downloadable documents. PDFs are compared by extracted text to reduce alerts caused only by PDF metadata; files whose PDF text cannot be extracted are compared byte-for-byte.
- Unexpected download failures (nonzero exit code and Render job failure).

It **does not automatically determine breaking API changes**. It detects document updates and shows the changed URLs. You can review the new guide and compare revisions manually. It does not authenticate to the live API.

## Deploy

1. Create a private GitHub repository and add the files from this folder to its default branch (`main`).
2. In GitHub, generate a **fine-grained personal access token** with repository access to that repository and **Contents: Read and write**. Keep the token secret. This enables the job to commit its snapshot (`monitoring/skifteportalen-state.json`) back to the repository. Do not put the token in code.
3. In Render, choose **New → Blueprint**, connect that GitHub repository, and deploy using `render.yaml`. This creates a Python Cron Job scheduled at `0 7 * * *` (07:00 UTC each day; 08:00/09:00 in Denmark depending on daylight saving time).
4. Set environment variables in Render:
   - `GITHUB_REPO` (required): `github-user/repository-name`
   - `GITHUB_TOKEN` (required): the GitHub token from step 2
   - `DISCORD_WEBHOOK_URL` (optional, recommended): Discord webhook URL for change alerts; **remove this variable** from the Blueprint if not using Discord, or leave unset in Render.
   - `GITHUB_BRANCH` (optional): defaults to `main`
   - `STATE_PATH` (optional): defaults to `monitoring/skifteportalen-state.json`
   - `EXTRA_PAGES` (optional): comma-separated additional source pages to monitor. Useful if the official links have moved to a separate page.
5. Run the Cron Job manually once in Render. The first run creates a baseline, without reporting existing documents as changes. On subsequent daily runs, changes trigger a Discord notification (when configured) and a GitHub commit with the updated hashes. Job logs always report results.

**Important:** Render Cron Job filesystems are ephemeral. The state file is intentionally stored in GitHub, not the job's local filesystem. Render runs cron schedules in UTC. Render and GitHub may have service costs/limits; review current pricing.

## Test locally

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
export GITHUB_REPO='your-account/your-repo'
export GITHUB_TOKEN='your-token'
export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...' # optional
python monitor.py
```

## Notes / limitations

- If `domstol.dk` returns HTTP 403 or otherwise blocks Render, the job fails and Render records the failure. An optional `EXTRA_PAGES` fallback can monitor a different reachable official page; the primary page is not silently treated as unchanged. If all pages fail, no new snapshot is stored.
- Only linked resources are checked; it does not guess hidden Swagger URLs or inspect undocumented authenticated endpoints. It does not follow links recursively.
- PDF/image-only scanned content without extractable text is checked using file bytes.
- If download of a previously tracked document fails, its old hash is retained for that run and a warning is emitted rather than generating a false “removed” alert.
- Because the monitor commits snapshots to the same repo, GitHub may trigger a Render redeploy when the state file changes. This does not itself schedule extra cron executions.
- Use a dedicated, least-privilege fine-grained token and configure any GitHub branch protection to permit the state update; alternatively put state in a separate repository with `GITHUB_REPO` pointing there.
