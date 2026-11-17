# Single screening latency

- Requests: 150 sequential `POST /api/v1/screenings` calls, one client
- Watchlist entries loaded: 19563
- p50: 5 ms, p95: 10 ms, max: 18 ms
- Query mix: exact, word-reversed, and one-character-deleted names sampled from the list
- Environment: development laptop, API, PostgreSQL, and client on one machine
