"""PDF screening report rendered with WeasyPrint.

The report lists the subject, matches with the feature breakdown, evidence excerpts with
source links, the score components, reviewer decisions on related alerts, the disclaimer, the
timestamp, and the report ID. The renderer refuses to fetch any external resource.
"""

from __future__ import annotations

import datetime as dt
import html
import os
import sys
import uuid
from typing import Any

if sys.platform == "darwin":  # Homebrew libraries are not on the default dynamic loader path
    os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")

from atlas.api.schemas import DISCLAIMER  # noqa: E402

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm; @bottom-center {
  content: "Screening report - page " counter(page) " of " counter(pages);
  font-size: 8pt; color: #555; } }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1b1f24; }
h1 { font-size: 18pt; margin: 0 0 4pt; } h2 { font-size: 12pt; margin: 16pt 0 6pt;
  border-bottom: 1px solid #c9ced6; padding-bottom: 2pt; }
table { border-collapse: collapse; width: 100%; } th, td { border: 1px solid #c9ced6;
  padding: 3pt 5pt; text-align: left; vertical-align: top; font-size: 9pt; }
th { background: #eef1f5; } .muted { color: #555; } .level { font-weight: bold; }
.disclaimer { margin-top: 14pt; padding: 6pt; border: 1px solid #8a6d00; background: #fff8dc; }
.quote { font-style: italic; }
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def safe_link(url: str | None) -> str:
    if url and url.lower().startswith(("http://", "https://")):
        return f'<a href="{html.escape(url, quote=True)}">{esc(url)}</a>'
    return esc(url)


def _breakdown_rows(breakdown: dict[str, Any]) -> str:
    comp = breakdown.get("components", {})
    rows = []
    labels = {
        "list": ("List match", "list_weight", "match_confidence"),
        "adverse": ("Adverse media", "severity", "link_confidence"),
        "recency": ("Recency", "decay", None),
        "source": ("Source reliability", "reliability", None),
    }
    for key, (label, a, b) in labels.items():
        c = comp.get(key)
        if not c:
            continue
        inputs = f"{a} = {c.get(a)}" + (f", {b} = {c.get(b)}" if b else "")
        rows.append(
            f"<tr><td>{label}</td><td>{c['weight']}</td><td>{esc(inputs)}</td>"
            f"<td>{c['points']}</td></tr>"
        )
    return "".join(rows)


def render_html(data: dict[str, Any], report_id: uuid.UUID, generated: dt.datetime) -> str:
    s = data["screening"]
    subject = data["subject"]
    matches = data["matches"]
    breakdown = s.get("score_breakdown") or {}
    watch_rows = (
        "".join(
            f"<tr><td>{esc(m['features'].get('name'))}</td><td>{esc(m.get('source'))}</td>"
            f"<td>{m['confidence']}</td><td>{esc(m['features'].get('via'))}; country "
            f"{esc(m['features'].get('country'))}; birth year "
            f"{esc(m['features'].get('birth_year'))}"
            "</td></tr>"
            for m in matches
            if m["match_type"] == "watchlist"
        )
        or '<tr><td colspan="4" class="muted">No watchlist matches.</td></tr>'
    )
    evidence = (
        "".join(
            f"<tr><td>{safe_link(e.get('url'))}<br><span class='muted'>{esc(e.get('title'))}"
            f"</span></td><td>{esc(e.get('published_at'))}</td><td class='quote'>"
            f"{esc(e.get('evidence'))}</td><td>{esc(e.get('category'))} (severity "
            f"{esc(e.get('severity'))}), link confidence {e['confidence']}</td></tr>"
            for e in data["evidence"]
        )
        or '<tr><td colspan="4" class="muted">No adverse media evidence.</td></tr>'
    )
    decisions = (
        "".join(
            f"<tr><td>{esc(d['alert_id'])}</td><td>{esc(d['status'])}</td><td>{esc(d['reason'])}</td>"
            f"<td>{esc(d['reviewer'])}</td><td>{esc(d['at'])}</td></tr>"
            for d in data["decisions"]
        )
        or '<tr><td colspan="5" class="muted">No reviewer decision has been recorded.</td></tr>'
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Screening report {esc(report_id)}</title><style>{CSS}</style></head>
<body>
<h1>Screening report</h1>
<p class="muted">Report ID {esc(report_id)}<br>Generated {esc(generated.isoformat())}<br>
Screening {esc(s["id"])} run {esc(s["created_at"])} with {esc(s["config_version"])}</p>
<h2>Subject</h2>
<table><tr><th>Name</th><th>Type</th><th>Country</th><th>Monitored</th></tr>
<tr><td>{esc(subject["name"])}</td><td>{esc(subject["kind"])}</td>
<td>{esc(subject["country"])}</td>
<td>{esc("yes" if subject["monitored"] else "no")}</td></tr></table>
<h2>Result</h2>
<p>Risk score <span class="level">{esc(s["risk_score"])}</span>, level
<span class="level">{esc(s["risk_level"])}</span>.</p>
<h2>Score breakdown</h2>
<table><tr><th>Component</th><th>Weight</th><th>Inputs</th><th>Points</th></tr>
{_breakdown_rows(breakdown)}</table>
<p class="muted">Score = 100 x sum of weight x input. Thresholds:
{esc(breakdown.get("thresholds"))}.</p>
<h2>Watchlist matches</h2>
<table><tr><th>Entry</th><th>Source</th><th>Confidence</th><th>Features</th></tr>
{watch_rows}</table>
<h2>Adverse media evidence</h2>
<table><tr><th>Source</th><th>Published</th><th>Excerpt</th><th>Assessment</th></tr>
{evidence}</table>
<h2>Reviewer decisions</h2>
<table><tr><th>Alert</th><th>Status</th><th>Reason</th><th>Reviewer</th><th>Time</th></tr>
{decisions}</table>
<div class="disclaimer"><strong>Disclaimer.</strong> {esc(DISCLAIMER)}</div>
</body></html>"""


def _refuse_fetch(url: str, *args: Any, **kwargs: Any) -> Any:
    raise ValueError(f"external resource blocked: {url}")


def render_pdf(data: dict[str, Any], report_id: uuid.UUID, generated: dt.datetime) -> bytes:
    from weasyprint import HTML

    pdf: bytes = HTML(
        string=render_html(data, report_id, generated), url_fetcher=_refuse_fetch
    ).write_pdf()
    return pdf
