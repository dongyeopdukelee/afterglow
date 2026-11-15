# Automated daily project health

GitHub-hosted runners run this independently of the owner's computer or Codex.
The workflow checks pytest (including HTTP E2E), Ruff lint/format, strict
Pyright, and uv package build. It makes no AI API calls, downloads no models,
and adds no service or package dependency. Docker integration remains in CI.

Reports live in `reports/health/YYYY-MM-DD.md`, dated in
`America/Los_Angeles`. Logs stay in Actions. Measurements are test counts and
uncompressed wheel bytes, not noisy timing benchmarks. Every new day records
real observations even if unchanged. Same-day reruns ignore the run URL when
comparing observations; changes in source, verdicts, or measurements update
that day's report. The retained URL identifies the run that supplied the
retained observation. Report-only commits do not change the source identifier.

The read-only observation job publishes an artifact even on check failure.
The separate writer has only `contents: write` and uses `GITHUB_TOKEN`.
Failed checks stay red even when publication succeeds. A dependency setup
failure is recorded as “could not run,” not a failed test. Runner cancellation
or failure before report generation may prevent publication; inspect Actions.

The writer builds a tree changing only the dated report and updates the actual
default branch without force. Concurrent pushes cause bounded retries against
the new parent; unrelated changes are preserved. GitHub tokens cannot be
path-scoped: report-only writes are enforced by the implementation, not a
GitHub permission boundary. No repository settings or protections are changed.
If branch rules later require PRs, direct publication fails visibly; reports
must then be delivered through PRs and merged before contribution credit.
Unattended contributions would require an allowed automatic merge process.

## Attribution

The authorized automated commit author is Duke, `dongyeopdukelee@gmail.com`.
Existing commit `534fd93161d092e95a97ab21aada494165de741c` resolves this email
to GitHub login `dongyeopdukelee` through the commits API. Local git configuration
agrees. The account email API is unavailable with the existing token scopes;
no exact GitHub noreply email was found, so none was guessed. The push token
and committer are independent of this explicitly configured author. Reports
and commit subjects clearly identify automation, not manual development.

## Operate

- Manually: Actions → Daily project health → Run workflow (default branch),
  or `gh workflow run daily-health.yml --ref main`.
- Schedule: edit `cron` in `.github/workflows/daily-health.yml`. It currently
  runs at 17:37 UTC daily (09:37 PST / 10:37 PDT), away from the hour boundary.
  GitHub scheduling is best-effort, not an exact-time guarantee.
- Disable: Actions → Daily project health → menu → Disable workflow,
  or `gh workflow disable daily-health.yml`. Re-enable with `gh workflow enable`.
- Inspect: select a run for verbose logs, check its artifact and dated report.
  A report commit made with `GITHUB_TOKEN` does not start recursive push CI.
- Profile: open `https://github.com/dongyeopdukelee`, select the report date's
  contribution square, and inspect activity for this repository. Linked author
  attribution and default-branch ancestry can be verified immediately; graph
  display is separate and may take up to 24 hours. Automated reports are genuine
  check observations, not proof of manual coding activity.

GitHub can disable scheduled workflows in public repos after extended
inactivity (60 days); check Actions if reports stop. There is no paid service
or PAT requirement; existing GitHub Actions usage/billing policies still apply.
