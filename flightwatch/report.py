"""Schreibt REPORT.md (auf GitHub direkt lesbar, auch am Handy)."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from . import links
from .models import Itinerary


def _eur(x) -> str:
    return f"{x:,.0f} €".replace(",", ".") if isinstance(x, (int, float)) else "–"


def _route(it: Itinerary) -> str:
    hin = " → ".join(l.describe() for l in it.out.legs)
    zur = " → ".join(l.describe() for l in it.back.legs)
    return f"**Hin:** {hin}<br>**Zurück:** {zur}"


def _links(it: Itinerary) -> str:
    return " · ".join(f"[{k}]({v})" for k, v in list(links.all_links(it).items())[:6])


def write_report(path: Path, its: list[Itinerary], state: dict, cfg: dict, now: datetime):
    th = cfg["thresholds"]
    L = [f"# ✈️ Flugsuche Deutschland ⇄ Santiago de Chile",
         "",
         f"Stand: **{now:%d.%m.%Y %H:%M}** · Zeitraum {cfg['trip']['earliest_departure']} bis "
         f"{cfg['trip']['latest_return']} · {cfg['trip']['min_days']}–{cfg['trip']['max_days']} Tage · "
         f"max. {cfg['trip']['max_hours_per_direction']} h pro Richtung · Push unter {_eur(th['push_below'])}",
         ""]
    if not its:
        L += ["_Noch keine passenden Angebote gefunden._", ""]
    else:
        L += ["## Beste Angebote", "",
              "| # | Preis | Hin | Zurück | Tage | Typ | Strecke | Hinweise |",
              "|---|---|---|---|---|---|---|---|"]
        kinds = {"rt-de": "Rundreise ab DE", "rt-hub": "Rundreise ab Drehkreuz + Zubringer",
                 "combo": "Einzeltickets", "google": "Google live"}
        for n, it in enumerate(its[: cfg["report"]["top_n"]], 1):
            notes = "<br>".join(it.flags + ([it.verified] if it.verified else []))
            L.append(f"| {n} | **{_eur(it.price)}** | {it.out.start_date:%d.%m.} | {it.back.start_date:%d.%m.} | "
                     f"{it.days} | {kinds.get(it.kind, it.kind)} | {_route(it)}<br>{_links(it)} | {notes} |")
        L += ["", "## Günstigstes Angebot je Abflugmonat", ""]
        per_month: "OrderedDict[str, Itinerary]" = OrderedDict()
        for it in sorted(its, key=lambda i: (i.out.start_date, i.price)):
            m = it.out.start_date.strftime("%m/%Y")
            if m not in per_month or it.price < per_month[m].price:
                per_month[m] = it
        for m, it in per_month.items():
            L.append(f"- **{m}:** {_eur(it.price)} – {it.out.start_date:%d.%m.} bis {it.back.start_date:%d.%m.} "
                     f"({it.out.route} / {it.back.route})")
        L.append("")
    gi = state.get("google_insights") or {}
    if gi:
        L += ["## Google-Flights-Preisniveau (Stichproben ab Deutschland, Rundreise)", "",
              "| Abflug | Rückflug | Günstigster | Niveau | Üblicher Bereich | geprüft |", "|---|---|---|---|---|---|"]
        for d in sorted(gi):
            g = gi[d]
            typ = "–".join(_eur(x) for x in g.get("typical") or []) or "–"
            L.append(f"| {d} | {g.get('return', '')} | {_eur(g.get('lowest'))} | {g.get('level') or '–'} | "
                     f"{typ} | {g.get('checked', '')} |")
        L.append("")
    hist = state.get("history", [])
    if hist:
        L += ["## Preisverlauf (bestes Angebot je Lauf)", "", "| Zeitpunkt | Bestpreis | Reise |", "|---|---|---|"]
        for h in reversed(hist[-30:]):
            L.append(f"| {h['t']} | {_eur(h['best'])} | {h['trip']} |")
        L.append("")
    L += ["---", "_Preise von Travelpayouts sind zwischengespeichert (bis ca. 48 h alt) und können beim Buchen "
          "abweichen. Bei getrennten Tickets haftet keine Airline für verpasste Anschlüsse – Puffer einplanen._"]
    path.write_text("\n".join(L) + "\n", "utf-8")
