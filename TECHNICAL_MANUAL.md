# Technical Manual

## Version
- 0.1.1
- 2026-03-08

## Recent Changes
- Ops Alerts workflow no longer fails the GitHub Actions run when alerts are detected.
- The workflow now writes either an alert summary or a clean summary to `GITHUB_STEP_SUMMARY`.
- Alert delivery still uses the existing email + GitHub issue path.

## Operational Notes
- Scheduled `Ops Alerts` runs are now informational workflows.
- Alert state is communicated through:
  - `scripts/run_ops_alert_checks.py`
  - `scripts/send_alert_digest.py`
  - the `StartupLens Ops Alert` GitHub issue
  - the workflow run summary

## Build / CI
- `.github/workflows/ops-alerts.yml` now succeeds on active alerts so scheduled notifications do not generate GitHub failure emails.
