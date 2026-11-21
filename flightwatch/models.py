from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Leg:
    origin: str              # Flughafen (oder "DE" bei Zug)
    dest: str
    price: float             # 0, wenn der Preis im Rundreise-Ticket steckt
    dep: datetime | None     # zeitzonenbewusst; None bei Zug/Bus
    arr: datetime | None
    carrier: str = ""
    transfers: int = 0
    link: str = ""
    source: str = "travelpayouts"
    mode: str = "flight"     # flight | ground
    hours: float | None = None
    note: str = ""
    qfrom: str = ""          # Code, mit dem gesucht wurde (z. B. Stadtcode PAR)
    qto: str = ""

    @property
    def duration_h(self) -> float:
        if self.dep and self.arr:
            return (self.arr - self.dep).total_seconds() / 3600
        return self.hours or 0.0

    def describe(self) -> str:
        if self.mode == "ground":
            return f"{self.note} nach {self.dest} (~{self.price:.0f} €, ~{self.hours:.0f} h, geschätzt)"
        when = self.dep.strftime("%d.%m. %H:%M") if self.dep else "?"
        stops = "direkt" if self.transfers == 0 else f"{self.transfers} Umstieg(e)"
        dur = f", {self.duration_h:.0f} h" if self.arr else ""
        price = f", {self.price:.0f} €" if self.price else ""
        carrier = f" {self.carrier}" if self.carrier else ""
        return f"{self.origin}→{self.dest} {when}{carrier} ({stops}{dur}{price})"


@dataclass
class Journey:
    legs: list[Leg]
    hours: float | None          # Reisezeit Deutschland <-> Santiago
    stopover_days: int = 0
    note: str = ""

    @property
    def price(self) -> float:
        return sum(l.price for l in self.legs)

    @property
    def start_date(self) -> date:
        for l in self.legs:
            if l.dep:
                return l.dep.date()
        raise ValueError("Journey ohne Flug")

    @property
    def end_date(self) -> date:
        for l in reversed(self.legs):
            if l.arr:
                return l.arr.date()
            if l.dep:
                return l.dep.date()
        raise ValueError("Journey ohne Flug")

    @property
    def route(self) -> str:
        parts = [self.legs[0].origin] + [l.dest for l in self.legs]
        return "-".join(parts)

    @property
    def flights(self) -> list[Leg]:
        return [l for l in self.legs if l.mode == "flight"]


@dataclass
class Itinerary:
    out: Journey
    back: Journey
    kind: str                     # rt-de | rt-hub | combo | google
    ticket_price: float = 0.0     # Preis eines Rundreise-Tickets
    notes: list[str] = field(default_factory=list)
    verified: str = ""

    @property
    def price(self) -> float:
        return round(self.ticket_price + self.out.price + self.back.price, 2)

    @property
    def days(self) -> int:
        return (self.back.start_date - self.out.start_date).days

    @property
    def key(self) -> str:
        return f"{self.out.route}|{self.out.start_date}|{self.back.route}|{self.back.start_date}"

    @property
    def flags(self) -> list[str]:
        f = []
        if self.kind in ("combo", "rt-hub"):
            f.append("Selbst-Umstieg (getrennte Tickets)")
        for j in (self.out, self.back):
            for a, b in zip(j.legs, j.legs[1:]):
                if a.arr and b.dep and (b.dep - a.arr).total_seconds() > 8 * 3600:
                    f.append(f"Lange Wartezeit/Nacht in {a.dest}")
        if self.back.stopover_days:
            f.append(f"Stopover {self.back.stopover_days} Tag(e) in {self.back.legs[0].dest}")
        if any(l.mode == "ground" for l in self.out.legs + self.back.legs):
            f.append("Zug-Anteil geschätzt")
        return f + self.notes
