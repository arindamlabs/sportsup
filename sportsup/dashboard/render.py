"""Server-side HTML rendering for the dashboard — a single self-contained page.

Pure functions (view model -> HTML string), so the markup is unit-testable and there's
no template-engine dependency. All dynamic values are HTML-escaped.
"""

from __future__ import annotations

from html import escape

from .data import Overview, Popularity, UserRow

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 24px;
       background: #0f1115; color: #e6e6e6; }
h1 { margin: 0 0 4px; font-size: 22px; }
.sub { color: #9aa0aa; margin-bottom: 20px; font-size: 13px; }
.cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 24px; }
.card { background: #1a1d24; border: 1px solid #2a2f3a; border-radius: 10px;
        padding: 14px 18px; min-width: 130px; }
.card .n { font-size: 26px; font-weight: 700; }
.card .l { color: #9aa0aa; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: start; }
@media (max-width: 820px) { .grid { grid-template-columns: 1fr; } }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #242832;
         vertical-align: top; font-size: 14px; }
th { color: #9aa0aa; font-weight: 600; font-size: 12px; text-transform: uppercase; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; }
.on { background: #143d2b; color: #6ee7a8; }
.off { background: #3a2330; color: #f2a9c0; }
.muted { color: #9aa0aa; }
.tourn { margin: 2px 0; }
.tourn b { color: #cbd2dc; }
h2 { font-size: 15px; margin: 0 0 8px; color: #cbd2dc; }
code { background: #242832; padding: 1px 5px; border-radius: 4px; }
"""


def _status_pill(status: str) -> str:
    cls = "on" if status == "active" else "off"
    return f'<span class="pill {cls}">{escape(status)}</span>'


def _bool_pill(on: bool, label: str) -> str:
    return f'<span class="pill {"on" if on else "off"}">{escape(label)}</span>'


def _tournaments_html(row: UserRow) -> str:
    if not row.tournaments:
        return '<span class="muted">— none —</span>'
    parts = []
    for t in row.tournaments:
        who = "all teams" if t.all_teams else ", ".join(escape(x) for x in t.teams)
        parts.append(f'<div class="tourn"><b>{escape(t.name)}</b>: {who}</div>')
    return "".join(parts)


def _user_table(rows: list[UserRow]) -> str:
    if not rows:
        return '<p class="muted">No subscribers yet.</p>'
    body = []
    for r in rows:
        alerts = " ".join([
            _bool_pill(r.reminders, "reminders"),
            _bool_pill(r.upsets, "upsets"),
            _bool_pill(r.finals, "finals"),
        ])
        body.append(
            "<tr>"
            f"<td><code>{escape(r.chat_id)}</code><br>{_status_pill(r.status)}</td>"
            f"<td>{_tournaments_html(r)}</td>"
            f"<td>{alerts}<br><span class='muted'>leads: {escape(', '.join(r.lead_times) or '—')}</span></td>"
            f"<td>{escape(r.timezone)}<br><span class='muted'>quiet: {escape(r.quiet_hours)}</span></td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>User</th><th>Tournaments &amp; teams</th>"
        "<th>Alerts</th><th>Timezone</th></tr></thead><tbody>"
        + "".join(body) + "</tbody></table>"
    )


def _popularity_table(title: str, items: list[tuple[str, int]]) -> str:
    if not items:
        return f"<h2>{escape(title)}</h2><p class='muted'>—</p>"
    rows = "".join(
        f"<tr><td>{escape(name)}</td><td class='muted'>{count}</td></tr>"
        for name, count in items
    )
    return (f"<h2>{escape(title)}</h2><table><thead><tr><th>Name</th>"
            f"<th>Followers</th></tr></thead><tbody>{rows}</tbody></table>")


def _card(n: object, label: str) -> str:
    return f'<div class="card"><div class="n">{escape(str(n))}</div><div class="l">{escape(label)}</div></div>'


def render_page(overview: Overview, rows: list[UserRow], popularity: Popularity,
                *, refresh_seconds: int = 30) -> str:
    last = overview.last_delivery_utc or "never"
    stats = overview.last_delivery_stats or "—"
    cards = "".join([
        _card(overview.subscribers, "subscribers"),
        _card(overview.active, "active"),
        _card(overview.paused, "paused"),
        _card(overview.subscriptions, "subscriptions"),
        _card(overview.competitions, "tournaments"),
    ])
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>SportsUp — Admin</title><style>{_CSS}</style></head>
<body>
<h1>SportsUp — Admin dashboard</h1>
<div class="sub">Read-only · last delivery: {escape(last)} ({escape(stats)})</div>
<div class="cards">{cards}</div>
<div class="grid">
  <div><h2>Subscribers</h2>{_user_table(rows)}</div>
  <div>
    {_popularity_table("Tournaments by followers", popularity.tournaments)}
    <div style="height:18px"></div>
    {_popularity_table("Top teams", popularity.teams)}
  </div>
</div>
</body></html>"""
