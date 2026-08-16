# nm-sports-feed

Aggregated sports schedules and live status for the NM Sports app.

`feed/events.json` is rebuilt on a schedule and read directly by set-top boxes as a static file. Boxes never
call a sports API themselves: free tiers survive one central poller and die instantly against a fleet.

- `feed/events.json` — every fixture in the window, normalised to one schema
- `feed/coverage.json` — per-competition counts, date ranges and status/artwork coverage; the instrument for
  spotting a source that has gone quiet

Source code for the aggregator lives in `sportsdata/`. See the main project for the adapter notes.
