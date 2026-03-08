# CLAUDE.md

## Recently Completed
- Changed the `Ops Alerts` GitHub Actions workflow so daily alert runs report alerts without failing the entire workflow.
- Added workflow summaries for both alerting and clean runs.

## Patterns
- Operational alert workflows should surface problems through issue/email/summary channels before using workflow failure as a signal.
- Scheduled notification workflows should stay green unless the workflow itself is broken.

## Gotchas
- `ops-alerts.yml` still depends on `SL_DATABASE_URL` and alert delivery secrets being present.
- Active alerts now appear in the GitHub issue trail and run summary instead of the run conclusion.
