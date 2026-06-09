"""Tests for the admin dashboard: aggregations, auth, and endpoint smoke."""

from base64 import b64encode

from fastapi.testclient import TestClient

from sportsup.dashboard import data
from sportsup.dashboard.app import create_app
from sportsup.dashboard.render import render_page
from sportsup.state import StateStore
from sportsup.subscribers import ALL_TEAMS, Subscriber, SubscriberStore


def _stores(tmp_path):
    store = StateStore(tmp_path / "s.sqlite")
    return store, SubscriberStore(store)


def _seed(subs):
    subs.upsert_subscriber(Subscriber(chat_id="u1", timezone="Asia/Tokyo"))
    subs.upsert_subscriber(Subscriber(chat_id="u2", status="paused"))
    subs.add_subscription("u1", "WC", 2026, "Brazil")
    subs.add_subscription("u1", "PL", 2026, "Arsenal")
    subs.add_subscription("u2", "WC", 2026, "Brazil")
    subs.add_subscription("u2", "WC", 2026, ALL_TEAMS)


# --- aggregations ----------------------------------------------------------

def test_overview_counts(tmp_path):
    store, subs = _stores(tmp_path)
    _seed(subs)
    store.set_meta("last_delivery_utc", "2026-06-13T12:00:00+00:00")
    ov = data.build_overview(subs, store)
    assert ov.subscribers == 2 and ov.active == 1 and ov.paused == 1
    assert ov.subscriptions == 4
    assert ov.competitions == 2          # only u1 (active) -> WC + PL; u2 is paused
    assert ov.last_delivery_utc.startswith("2026-06-13")


def test_user_rows_group_tournaments(tmp_path):
    store, subs = _stores(tmp_path)
    _seed(subs)
    rows = {r.chat_id: r for r in data.build_user_rows(subs)}
    u1 = rows["u1"]
    assert u1.timezone == "Asia/Tokyo"
    names = {t.code: t for t in u1.tournaments}
    assert names["WC"].teams == ["Brazil"] and names["PL"].teams == ["Arsenal"]
    # u2 has both Brazil and ALL_TEAMS in WC -> shown as all_teams
    assert rows["u2"].tournaments[0].all_teams is True


def test_popularity(tmp_path):
    store, subs = _stores(tmp_path)
    _seed(subs)
    pop = data.build_popularity(subs)
    # WC followed by both users, PL by one.
    counts = dict(pop.tournaments)
    assert counts["FIFA World Cup 2026"] == 2
    assert counts["English Premier League"] == 1
    # ALL_TEAMS sentinel excluded from team popularity; Brazil counted for both.
    team_counts = dict(pop.teams)
    assert team_counts["Brazil"] == 2 and "*" not in team_counts


def test_render_page_escapes_and_includes(tmp_path):
    store, subs = _stores(tmp_path)
    subs.upsert_subscriber(Subscriber(chat_id="u1"))
    subs.add_subscription("u1", "PL", 2026, "Brighton & Hove Albion FC")
    html = render_page(data.build_overview(subs, store), data.build_user_rows(subs),
                       data.build_popularity(subs))
    assert "SportsUp" in html
    assert "Brighton &amp; Hove Albion FC" in html   # HTML-escaped


# --- auth + endpoints ------------------------------------------------------

def _client(tmp_path):
    store, subs = _stores(tmp_path)
    _seed(subs)
    return TestClient(create_app(subs, store, user="admin", password="s3cret"))


def _auth(user, pw):
    token = b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_healthz_is_open(tmp_path):
    assert _client(tmp_path).get("/healthz").json() == {"ok": True}


def test_index_requires_auth(tmp_path):
    c = _client(tmp_path)
    assert c.get("/").status_code == 401                      # no creds
    assert c.get("/", headers=_auth("admin", "wrong")).status_code == 401
    ok = c.get("/", headers=_auth("admin", "s3cret"))
    assert ok.status_code == 200 and "Subscribers" in ok.text


def test_api_overview_authed(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/overview").status_code == 401
    body = c.get("/api/overview", headers=_auth("admin", "s3cret")).json()
    assert body["subscribers"] == 2 and body["subscriptions"] == 4


def test_api_subscribers_authed(tmp_path):
    c = _client(tmp_path)
    body = c.get("/api/subscribers", headers=_auth("admin", "s3cret")).json()
    assert {r["chat_id"] for r in body} == {"u1", "u2"}
