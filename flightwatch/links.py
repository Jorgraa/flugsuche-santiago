"""Such-Links zu Google Flights, Skyscanner, Kiwi und Skiplagged."""
from __future__ import annotations

import urllib.parse
from datetime import date

from .models import Itinerary


def google(o: str, d: str, d1: date, d2: date | None = None) -> str:
    q = f"Flights from {o} to {d} on {d1.isoformat()}"
    q += f" through {d2.isoformat()}" if d2 else " one way"
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode({"q": q, "curr": "EUR", "hl": "de"})


def skyscanner(o: str, d: str, d1: date, d2: date | None = None) -> str:
    path = f"{o.lower()}/{d.lower()}/{d1.strftime('%y%m%d')}/"
    if d2:
        path += f"{d2.strftime('%y%m%d')}/"
    return f"https://www.skyscanner.de/transport/flights/{path}?adultsv2=1&cabinclass=economy"


def kiwi(o: str, d: str, d1: date, d2: date | None = None) -> str:
    return f"https://www.kiwi.com/de/search/results/{o}/{d}/{d1.isoformat()}/{d2.isoformat() if d2 else 'no-return'}"


def skiplagged(o: str, d: str, d1: date, d2: date | None = None) -> str:
    return f"https://skiplagged.com/flights/{o}/{d}/{d1.isoformat()}" + (f"/{d2.isoformat()}" if d2 else "")


def longhaul_pair(it: Itinerary):
    """(Abflug, Ziel, Datum hin, Rückflug-Start, Rückflug-Ziel, Datum zurück) der Langstrecke."""
    out = it.out.flights[-1]                       # letzte Strecke hin = Langstrecke nach SCL
    back_flights = it.back.flights
    lh_back = back_flights[1] if it.back.note == "openjaw" and len(back_flights) > 1 else back_flights[0]
    return out, lh_back


def all_links(it: Itinerary) -> dict[str, str]:
    out, back = longhaul_pair(it)
    o, d1 = out.origin, out.dep.date()
    b, d2 = back.dest, back.dep.date()
    links = {}
    if it.kind in ("rt-de", "rt-hub", "google") and back.origin == "SCL" and b == o:
        links["Google Flights"] = google(o, "SCL", d1, d2)
        links["Skyscanner"] = skyscanner(o, "SCL", d1, d2)
        links["Kiwi"] = kiwi(o, "SCL", d1, d2)
        links["Skiplagged"] = skiplagged(o, "SCL", d1, d2)
    else:
        links["Google hin"] = google(o, "SCL", d1)
        links["Google zurück"] = google(back.origin, b, d2)
        links["Skyscanner hin"] = skyscanner(o, "SCL", d1)
        links["Skyscanner zurück"] = skyscanner(back.origin, b, d2)
        links["Kiwi hin"] = kiwi(o, "SCL", d1)
        links["Kiwi zurück"] = kiwi(back.origin, b, d2)
    for l in it.out.legs + it.back.legs:
        if l.link and l.link not in links.values():
            links[f"Aviasales {l.origin}→{l.dest}"] = l.link
    return links
