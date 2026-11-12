# Hourly publication and recovery

Deployment status: enabled on 12 November 2026. See `VERIFICATION-2026-11-12.md` for live execution evidence and the boundary of the controlled recovery test.

The collector runs on GitHub's standard public runner. An independent watchdog on the existing Lekker Weskus Supabase project checks the production site's feed every five minutes. PostgreSQL wakes it once per minute so asynchronous checks are processed promptly.

The hourly target is minute 2 UTC (also minute 2 in South Africa). Once a new hour starts, the watchdog requests a GitHub run if no complete publication for that hour has reached the site. It logs a missed hour after minute 15, after its first feed check. Failed or missing publications are retried after 25 minutes, with at most three dispatch attempts in each UTC hour. A confirmed successful publication allows the next hour to start without waiting for the previous attempt's retry interval. The collector's timeout is 20 minutes. A row lock serializes watchdog ticks and GitHub serializes publications. The original GitHub schedule remains a fallback.

Each source is attempted up to three times with 20-second and 60-second delays. Last good data retain their original timestamps. A failed refresh advances the attempt date while retaining the last successful refresh date. A quiet weather station is reported separately from a collector failure. The watchdog measures complete publications and their actual arrival on the website.

## Deployment

1. Apply `watchdog.sql` to the Lekker Weskus Supabase project. This creates dedicated `weather_ops` and `weather_http` schemas, a disabled control row, a read-only weather status function, and the named `weather-hourly-watchdog` cron job. The HTTP extension sends authenticated dispatches directly with an eight-second timeout. The shared HTTP queue carries public feed requests.
2. Run `watchdog-tests.sql`. Fixtures run in a transaction that is rolled back. Every case suppresses authenticated dispatch: disabled, unchecked startup, fresh success or exhausted attempts. The checks cover public-role isolation, disabled behavior, startup, recovery timestamps, HTTP failure and retry caps.
3. Provision `weather_github_dispatch_token` in Supabase Vault through a secure administrative session. Prefer a fine-grained GitHub token restricted to `DawidBotha89/weskus-weather`, with Actions write permission. Keep its value out of repository files, logs and public clients. Credential expiry or revocation is an operational failure requiring rotation.
4. Enable the single control row using `update weather_ops.updater set enabled=true where id;`.
5. Verify a real cron tick, a GitHub `dispatch_accepted` event, the resulting complete publication and its subsequent detection on the production site's own data URL.

The installed HTTP extension is version 1.6. Construct headers with typed `weather_http.http_header` arrays; the `http_headers` convenience helper in newer upstream documentation is unavailable here. The installed `pg_cron` is 1.6.4 and `pg_net` is 0.20.4.

## Checking health

`select public.weather_update_status();` reports source publication and success dates, last check, last dispatch, attempts this hour, missed-hour count and health. Its public response contains only operational weather status. Detailed events are retained for 30 days in the private `weather_ops.events` table. Credentials and HTTP response bodies are excluded from events.

A healthy status requires a complete publication within 75 minutes and a watchdog check within ten minutes. A fresh attempt carrying failed sources does not satisfy this condition. Use the feed's `health.warnings` to inspect missing or old station observations separately.

To pause only this watchdog, set its control row's `enabled` field to false. The existing GitHub schedule continues to work. No paid weather APIs or additional subscription is required. The system still depends on the existing Supabase service, GitHub runner availability, provider uptime and a valid GitHub credential.

The current fine-grained token grants Actions read/write and required Metadata read access on this repository only, with no user permissions. It expires on **10 February 2027**. Rotate it in GitHub and replace the named Vault secret before then. A revoked or expired token produces a failed dispatch and an unhealthy status; this status endpoint does not send email or SMS alerts.

The security advisor reports the intentionally public `SECURITY DEFINER` status function. Its fixed SQL returns only weather timestamps and counters; it has no parameters, dynamic SQL, secrets or mutation. The scheduler and HTTP schemas are private. Their RLS-enabled tables intentionally have no public policies. Do not grant public access to those tables to silence informational advisor notices.

References

* [Supabase scheduled functions](https://supabase.com/docs/guides/functions/schedule-functions)
* [Supabase HTTP extension](https://supabase.com/docs/guides/database/extensions/http)
* [HTTP request types and connection timeouts](https://github.com/pramsey/pgsql-http)
* [GitHub schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
