# Keyword prefilter check

Articles harvested by adverse-topic queries (English). Bodies were fetched, tested in memory, and discarded.

```
{
 "fetch_status": {
  "robots": 22,
  "ok": 200,
  "blocked": 2,
  "http_error": 37,
  "error": 3
 },
 "bodies_tested": 200,
 "pass_rate": 0.99
}
```

Title-only recall on the harvested English titles was 11 percent, so the filter must run on article text, never on titles. The body result is optimistic because the filter vocabulary overlaps the harvest queries; recall on the gold set is still to be measured.
