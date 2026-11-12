# Hourly update repair — 12 November 2026

The collector retry changes were released in [PR 1](https://github.com/DawidBotha89/weskus-weather/pull/1). Twenty tests passed in the hosted workflow. A successful publication reached the site's own `/weather-shadow/latest.json` route and the rendered comparison page.

## Scheduler

The approved independent scheduler runs in the existing Lekker Weskus Supabase project. It uses `pg_cron` every minute, reads the production feed about every five minutes and starts GitHub Actions when an hourly publication is due. The target is minute 2 each hour. It records a miss after minute 15 and retries an unsuccessful attempt after 25 minutes, capped at three attempts per UTC hour.

A fine-grained token restricted to this repository grants Actions read/write and required Metadata read access. It has no user permissions and expires on 10 February 2027. It was transferred directly from GitHub to the Supabase Vault form, without a local secret file or repository entry. Temporary browser clipboard and runtime token copies were cleared after storage.

## Controlled recovery evidence

Only the watchdog's internal timestamps were altered to represent an overdue publication. The public forecasts and verification ledger were not replaced by fixtures. The drill is recorded in private events as `verification_started` and `verification_retry_armed`.

* At 19:41 UTC (21:41 SAST), the real cron detected the simulated overdue hour and attempted dispatch. This exposed an HTTP extension compatibility issue, which was corrected using typed header arrays supported by installed version 1.6.
* The failed attempt's timestamp was moved back 26 minutes to exercise retry eligibility without waiting 25 minutes. At 19:44 UTC, the real cron recorded `reason=retry`, attempt 2, and GitHub accepted it with HTTP 204.
* [Run 43293169011](https://github.com/DawidBotha89/weskus-weather/actions/runs/43293169011) succeeded and published at **19:44:54 UTC / 21:44:54 SAST**. The production JSON returned all 15 atmospheric towns with complete health. Chrome rendered “Published 12 Nov 2026, 21:44 SAST” with station, humidity, forecast, marine and coastal-tide sections.
* At **19:50 UTC / 21:50 SAST**, a subsequent real cron check read that publication from the production site and returned `healthy=true`. No manual acknowledgement or success timestamp was supplied. The cumulative missed-hour counter includes this one labelled drill.
* At **20:02:00 UTC / 22:02:00 SAST**, with no forced timestamps or manual dispatch, cron recorded `reason=hour_due`, attempt 1, and HTTP 204. [Run 43296199936](https://github.com/DawidBotha89/weskus-weather/actions/runs/43296199936) started at 20:02:01 and succeeded at 20:02:21 UTC. Its complete publication is dated **20:02:17 UTC / 22:02:17 SAST**. The unchanged production URL returned it by 20:05 UTC, after its normal `max-age=300` cache propagation. Chrome's live comparison displayed “Published 12 Nov 2026, 22:02 SAST”.
* At **20:06 UTC / 22:06 SAST**, the unattended watchdog recognised that exact publication and reported `healthy=true`, `feed_status=complete`, `dispatch_status=confirmed`, and one attempt for the hour. All 31 cron executions from 19:36 through 20:06 UTC succeeded. The earlier application-level HTTP compatibility failure is recorded separately and was corrected; a successful cron invocation alone is not proof of successful publication.

## Safeguards

Rollback-based SQL tests passed for disabled operation, startup without an observation, success suppression, degraded-publication timestamps, failed HTTP reads and the retry cap. Fixtures cannot dispatch authenticated requests. Public-role access to the private scheduler and HTTP schemas is denied. No authenticated GitHub request enters the shared HTTP queue and no token appears in weather event details. The public REST status endpoint was tested with the project's publishable key.

Independent review identified two edge cases that were corrected before sign-off: dispatch acceptance hiding a feed failure, and a public HTTP enqueue failure rolling back already accepted dispatch bookkeeping. Feed and dispatch statuses are now independent, enqueue failures are caught before the external dispatch, and new SQL regressions prove that recent-but-old successful data cannot hide either failure. Only a complete publication after the last dispatch confirms its recovery. The expanded rollback tests passed on the live database.

The security advisor notices for the fixed, read-only public status function and private RLS tables are intentional; see [operation notes](README.md). Shared application permissions were not changed.

## Limits

This verifies the configured trigger and live recovery path, not months of availability. GitHub, Supabase, upstream feeds and the valid token remain dependencies. Source attempts use 20-second and 60-second retry waits. A quiet station can still have no new reading, even when the collector succeeds. Missing or stale reports are labelled in `health.warnings` rather than silently assigned a new observation time. No new paid API or subscription was added.
