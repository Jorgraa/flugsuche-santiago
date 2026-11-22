"""Ein Suchlauf: Preise holen, kombinieren, live prüfen, Push senden, Bericht schreiben."""
from __future__ import annotations

import json
import os
import sys
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import links, notify
from .models import Itinerary, Journey, Leg
from .report import write_report
from .search import Search
from .sources import GoogleFlights, TravelpayoutsClient

ROOT = Path(__file__).resolve().parent.parent

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Berlin")
except Exception:  # Windows ohne tzdata
    TZ = timezone.utc


def load_state(p: Path) -> dict:
    if p.exists():
        return json.loads(p.read_text("utf-8"))
    return {}


def eur(x: float) -> str:
    return f"{x:,.0f} €".replace(",", ".")


def summary_line(it: Itinerary) -> str:
    o, b = links.longhaul_pair(it)
    start = it.out.legs[0]
    first = "Zug" if start.mode == "ground" else start.origin
    hin = f"{o.origin}→SCL" if first == o.origin else f"{first}→{o.origin}→SCL"
    return (f"{eur(it.price)} · {it.out.start_date:%d.%m.}–{it.back.start_date:%d.%m.%Y} ({it.days} T) · "
            f"{hin} / {b.origin}→{it.back.legs[-1].dest}")


# ---------- Google Flights ----------
def gf_budget(state: dict, cfg: dict) -> int:
    used = state.setdefault("serpapi_usage", {}).get(datetime.now(TZ).strftime("%Y-%m"), 0)
    return max(0, cfg["google"]["monthly_cap"] - used)


def gf_count(state: dict, n: int = 1):
    m = datetime.now(TZ).strftime("%Y-%m")
    state["serpapi_usage"][m] = state["serpapi_usage"].get(m, 0) + n


def gf_parse_time(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def google_sweep(gf: GoogleFlights, cfg: dict, state: dict, log) -> list[Itinerary]:
    g, t = cfg["google"], cfg["trip"]
    earliest, latest = date.fromisoformat(t["earliest_departure"]), date.fromisoformat(t["latest_return"])
    days = g["sweep_trip_days"]
    dates = []
    d = earliest
    while d + timedelta(days=days) <= latest:
        dates.append(d)
        d += timedelta(days=g["sweep_step_days"])
    if not dates:
        return []
    its = []
    idx = state.get("sweep_index", 0)
    dep_ids = ",".join(cfg["airports"]["germany_longhaul"])
    for _ in range(g["sweep_per_run"]):
        if gf_budget(state, cfg) <= 0:
            break
        d1 = dates[idx % len(dates)]
        d2 = d1 + timedelta(days=days)
        idx += 1
        res = gf.search(dep_ids, t["destination"], d1.isoformat(), d2.isoformat(),
                        t["max_hours_per_direction"] * 60, g["carry_on_bags"])
        if res is None:
            continue
        gf_count(state)
        pi = res.get("price_insights") or {}
        if pi:
            state.setdefault("google_insights", {})[d1.isoformat()] = {
                "lowest": pi.get("lowest_price"), "level": pi.get("price_level"),
                "typical": pi.get("typical_price_range"), "return": d2.isoformat(),
                "checked": datetime.now(TZ).strftime("%Y-%m-%d")}
        best = GoogleFlights.cheapest(res)
        results = state.setdefault("google_results", {})
        if not best or not best.get("flights"):
            results.pop(d1.isoformat(), None)   # kein passendes Angebot (mehr)
            log(f"[google] {d1}–{d2}: kein Angebot mit ≤{t['max_hours_per_direction']} h")
            continue
        results[d1.isoformat()] = {
            "price": float(best["price"]), "return": d2.isoformat(),
            "checked": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
            "total_duration": best.get("total_duration", 0),
            "legs": [[f["departure_airport"]["id"], f["arrival_airport"]["id"],
                      f["departure_airport"].get("time"), f["arrival_airport"].get("time"),
                      f.get("flight_number", "")] for f in best["flights"]],
        }
        log(f"[google] {d1}–{d2}: {best['price']} € ab {best['flights'][0]['departure_airport']['id']}, "
            f"Preisniveau {pi.get('price_level')}")
    state["sweep_index"] = idx


def google_from_state(state: dict, cfg: dict, now: datetime) -> list[Itinerary]:
    """Alle bisher von Google geprüften Termine als Reisen (auch aus früheren Läufen)."""
    dest = cfg["trip"]["destination"]
    results = state.get("google_results", {})
    its = []
    for d1, rec in list(results.items()):
        checked = datetime.strptime(rec["checked"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        if date.fromisoformat(d1) < now.date() or now - checked > timedelta(days=21):
            del results[d1]          # vorbei oder zu alt
            continue
        legs = [Leg(origin=o, dest=d, price=0, dep=gf_parse_time(dep), arr=gf_parse_time(arr),
                    carrier=fn, source="google") for o, d, dep, arr, fn in rec["legs"]]
        if not legs[0].dep:
            continue
        lh = Leg(origin=legs[0].origin, dest=legs[-1].dest, price=0, dep=legs[0].dep, arr=legs[-1].arr,
                 carrier=", ".join(sorted({l.carrier[:2] for l in legs if l.carrier})),
                 transfers=len(legs) - 1, source="google", qfrom=legs[0].origin, qto=dest)
        d2 = date.fromisoformat(rec["return"])
        back = Leg(origin=dest, dest=lh.origin, price=0,
                   dep=datetime(d2.year, d2.month, d2.day, 12, tzinfo=timezone.utc), arr=None,
                   source="google", qfrom=dest, qto=lh.origin)
        fresh = now - checked < timedelta(hours=1)
        note = ("Live-Preis Google Flights (inkl. Handgepäck)" if fresh
                else f"Google-Preis vom {checked:%d.%m. %H:%M} (inkl. Handgepäck)")
        its.append(Itinerary(Journey([lh], rec.get("total_duration", 0) / 60), Journey([back], None),
                             "google", ticket_price=rec["price"], fresh=fresh,
                             notes=[note + ", Rückflugzeiten nicht geprüft"]))
    return its


def google_verify(gf: GoogleFlights, it: Itinerary, cfg: dict, state: dict, log):
    """Prüft den Langstrecken-Teil eines Treffers live bei Google Flights."""
    if gf_budget(state, cfg) <= 0 or it.kind == "google":
        return
    out, back = links.longhaul_pair(it)
    maxm = cfg["trip"]["max_hours_per_direction"] * 60
    if it.kind in ("rt-de", "rt-hub"):
        res = gf.search(out.origin, "SCL", out.dep.date().isoformat(), back.dep.date().isoformat(),
                        maxm, cfg["google"]["carry_on_bags"])
        ref = it.ticket_price
    else:
        res = gf.search(out.origin, "SCL", out.dep.date().isoformat(), None, maxm, cfg["google"]["carry_on_bags"])
        ref = out.price
    if res is None:
        return
    gf_count(state)
    best = GoogleFlights.cheapest(res)
    if not best:
        it.verified = "Google Flights: kein Angebot mit ≤30 h gefunden"
        return
    p = float(best["price"])
    part = "Langstrecke hin+zurück" if it.kind != "combo" else "Langstrecke hin"
    it.verified = f"Google live ({part}): {eur(p)} statt {eur(ref)}"
    if ref and p > ref * 1.2:
        it.verified += " ⚠️ Cache-Preis evtl. veraltet"
    log(f"[verify] {it.key}: {it.verified}")


# ---------- Hauptablauf ----------
def main() -> int:
    cfg = tomllib.loads((ROOT / "config.toml").read_text("utf-8"))
    state_file = ROOT / "state" / "state.json"
    state_file.parent.mkdir(exist_ok=True)
    state = load_state(state_file)
    log = print
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    topic = os.environ.get("NTFY_TOPIC") or None
    tp_token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    serp_key = os.environ.get("SERPAPI_KEY")
    th = cfg["thresholds"]

    its: list[Itinerary] = []
    tp = None
    if tp_token:
        tp = TravelpayoutsClient(tp_token, ROOT / "cache", log=log)
        its += Search(cfg, tp, log=log).run()
        log(f"Travelpayouts: {tp.calls} Abfragen, {tp.errors} Fehler, {len(its)} Reisen")
    else:
        log("TRAVELPAYOUTS_TOKEN fehlt – nur Google Flights.")
    gf = GoogleFlights(serp_key, log=log) if serp_key else None
    if gf:
        google_sweep(gf, cfg, state, log)
    its += google_from_state(state, cfg, now)
    its.sort(key=lambda i: i.price)

    # Gute Angebote live prüfen und melden
    notified = state.setdefault("notified", {})
    deals = [i for i in its if i.fresh and i.price < th["push_below"]
             and (i.key not in notified or i.price <= notified[i.key] - th["renotify_drop_eur"])]
    deals = deals[: th["max_pushes_per_run"]]
    if gf:
        for it in deals[: cfg["google"]["verify_per_run"]]:
            google_verify(gf, it, cfg, state, log)
    pushes = 0
    for it in deals:
        lk = links.all_links(it)
        alarm = it.price < th["alarm_below"]
        lines = [summary_line(it), ""]
        lines += ["Hin: " + " → ".join(l.describe() for l in it.out.legs)]
        lines += ["Zurück: " + " → ".join(l.describe() for l in it.back.legs)]
        if it.out.hours:
            lines += [f"Reisezeit hin ~{it.out.hours:.0f} h" + (f", zurück ~{it.back.hours:.0f} h" if it.back.hours else "")]
        lines += [f"• {f}" for f in it.flags]
        if it.verified:
            lines += [it.verified]
        first_link = next(iter(lk.values()))
        acts = [{"label": k, "url": v} for k, v in list(lk.items())[:3]]
        ok = notify.send(topic, ("🚨 FEHLERPREIS? " if alarm else "✈️ Günstig nach Santiago: ") + eur(it.price),
                         "\n".join(lines), priority=5 if alarm else 4,
                         tags=["rotating_light" if alarm else "airplane"], click=first_link, actions=acts, log=log)
        if ok or not topic:
            notified[it.key] = it.price
            pushes += 1

    # Wöchentliche Zusammenfassung
    if now.weekday() == th["digest_weekday"] and state.get("last_digest") != today and its:
        body = "\n".join(f"{n+1}. {summary_line(i)}" for n, i in enumerate(its[:5]))
        hist = state.get("history", [])
        if len(hist) >= 8:
            body += f"\n\nVor einer Woche bestes Angebot: {eur(hist[-8]['best'])}"
        notify.send(topic, f"📊 Wochen-Update Santiago: ab {eur(its[0].price)}", body, priority=2,
                    tags=["bar_chart"], click=os.environ.get("REPORT_URL") or None, log=log)
        state["last_digest"] = today

    # Fehlermeldung, wenn die Datenquelle komplett ausfällt
    if tp and tp.calls and tp.errors == tp.calls and state.get("last_error_push") != today:
        notify.send(topic, "⚠️ Flugsuche: Travelpayouts antwortet nicht",
                    "Alle Abfragen sind fehlgeschlagen. Ist der Token noch gültig?", priority=3,
                    tags=["warning"], log=log)
        state["last_error_push"] = today

    # Verlauf + Bericht
    if its:
        hist = state.setdefault("history", [])
        entry = {"t": now.strftime("%Y-%m-%d %H:%M"), "best": its[0].price, "trip": summary_line(its[0])}
        hist.append(entry)
        del hist[:-400]
    # alte Benachrichtigungen aufräumen (Reisen, die schon vorbei wären)
    for k in list(notified):
        try:
            if date.fromisoformat(k.split("|")[1]) < now.date():
                del notified[k]
        except (IndexError, ValueError):
            pass
    write_report(ROOT / "REPORT.md", its, state, cfg, now)
    state_file.write_text(json.dumps(state, indent=1, ensure_ascii=False), "utf-8")
    log(f"Fertig. Bestes Angebot: {summary_line(its[0]) if its else '—'}; {pushes} Push(es).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
