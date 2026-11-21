"""Holt Preise und baut daraus komplette Reisen (direkt, über Drehkreuze, Gabelflüge)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from .models import Itinerary, Journey, Leg
from .sources import tp_to_legs

FEEDER_TTL_H = 36


def month_str(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def months_between(a: date, b: date) -> list[str]:
    out, cur = [], date(a.year, a.month, 1)
    while cur <= b:
        out.append(month_str(cur))
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


def month_index(m: str) -> int:
    y, mo = m.split("-")
    return int(y) * 12 + int(mo)


class Search:
    def __init__(self, cfg: dict, tp, log=print):
        self.cfg = cfg
        self.tp = tp
        self.log = log
        t = cfg["trip"]
        self.dest = t["destination"]
        self.earliest = date.fromisoformat(t["earliest_departure"])
        self.latest = date.fromisoformat(t["latest_return"])
        self.min_days, self.max_days = t["min_days"], t["max_days"]
        self.maxh = t["max_hours_per_direction"]
        self.min_tr = t["min_self_transfer_hours"]
        self.min_tr_change = t["min_self_transfer_hours_airport_change"]
        a = cfg["airports"]
        self.de_long = a["germany_longhaul"]
        self.de_feed = a["germany_feeder"]
        self.de_all = set(self.de_long) | set(self.de_feed)
        self.hubs = a["hubs"]
        self.oj = a.get("open_jaw_return", [])
        self.oj_to = a.get("open_jaw_longhaul_to", [])
        self.max_stop = a.get("max_stopover_days", 0)
        self.ground = cfg.get("ground", {})
        self.out_months = months_between(self.earliest, self.latest - timedelta(days=self.min_days))
        self.ret_months = months_between(self.earliest + timedelta(days=self.min_days), self.latest)
        self.feed_to: dict[str, dict[date, list[Leg]]] = defaultdict(lambda: defaultdict(list))
        self.feed_from: dict[str, dict[date, list[Leg]]] = defaultdict(lambda: defaultdict(list))

    # ---------- Abruf ----------
    def _ow(self, o, d, months, ttl=0) -> list[Leg]:
        legs = []
        for m in months:
            for it in self.tp.prices(o, d, m, one_way=True, ttl_h=ttl):
                leg, _, _ = tp_to_legs(it, o, d, True)
                if leg:
                    legs.append(leg)
        return legs

    def _rt(self, o, d) -> list[tuple[Leg, Leg, float]]:
        res = []
        for dm in self.out_months:
            for rm in self.ret_months:
                if not 0 <= month_index(rm) - month_index(dm) <= 2:
                    continue
                for it in self.tp.prices(o, d, dm, rm, one_way=False):
                    out, back, p = tp_to_legs(it, o, d, False)
                    if out and back:
                        res.append((out, back, p))
        return res

    def _in_window_out(self, l: Leg) -> bool:
        return self.earliest <= l.dep.date() <= self.latest - timedelta(days=self.min_days)

    def _in_window_back(self, l: Leg) -> bool:
        end = (l.arr or l.dep).date()
        return self.earliest + timedelta(days=self.min_days) <= l.dep.date() and end <= self.latest

    def fetch(self):
        origins = self.de_long + self.hubs
        self.rt = {o: [x for x in self._rt(o, self.dest)
                       if self._in_window_out(x[0]) and self._in_window_back(x[1])] for o in origins}
        self.ow_out = {o: [l for l in self._ow(o, self.dest, self.out_months) if self._in_window_out(l)]
                       for o in origins}
        self.ow_back = {o: [l for l in self._ow(self.dest, o, self.ret_months) if self._in_window_back(l)]
                        for o in origins}
        # Gabelflug: erst Santiago -> X, dann X -> Europa
        self.regional = {x: self._ow(self.dest, x, self.ret_months) for x in self.oj}
        self.oj_long = {}
        for x in self.oj:
            if not self.regional[x]:
                continue
            for d in self.de_long + self.oj_to:
                self.oj_long[(x, d)] = [l for l in self._ow(x, d, self.ret_months) if self._in_window_back(l)]
        # Zubringer nur für die vielversprechendsten Drehkreuze
        for h in self._promising_hubs():
            self._fetch_feeders(h)

    def _promising_hubs(self) -> list[str]:
        score = {}
        for h in self.hubs:
            best = [p for _, _, p in self.rt.get(h, [])]
            if self.ow_out.get(h) and self.ow_back.get(h):
                best.append(min(l.price for l in self.ow_out[h]) + min(l.price for l in self.ow_back[h]))
            if best:
                score[h] = min(best)
        ranked = sorted(score, key=score.get)[: self.cfg["airports"].get("feeder_hubs_max", 6)]
        for h in self.oj_to:
            if h not in ranked and any(self.oj_long.get((x, h)) for x in self.oj):
                ranked.append(h)
        self.log(f"Drehkreuze für Zubringer: {ranked} "
                 f"({', '.join(f'{h}≈{score[h]:.0f}€' for h in ranked if h in score)})")
        return ranked

    def _fetch_feeders(self, hub: str):
        for f in self.de_feed:
            if f == hub:
                continue
            for l in self._ow(f, hub, self.out_months, ttl=FEEDER_TTL_H):
                if l.arr:
                    self.feed_to[hub][l.arr.date()].append(l)
            for l in self._ow(hub, f, self.ret_months, ttl=FEEDER_TTL_H):
                if l.arr and l.dest in self.de_all | {f}:
                    self.feed_from[hub][l.dep.date()].append(l)

    # ---------- Zubringer anhängen ----------
    def _need(self, a: str, b: str) -> float:
        return self.min_tr if a == b else self.min_tr_change

    def _ground_leg(self, hub: str, outbound: bool) -> Leg | None:
        g = self.ground.get(hub)
        if not g:
            return None
        o, d = ("DE", hub) if outbound else (hub, "DE")
        return Leg(origin=o, dest=d, price=float(g["price"]), dep=None, arr=None,
                   mode="ground", hours=float(g["hours"]), note=g.get("note", "Zug"), source="schätzung")

    def out_feeder(self, hub: str, lh: Leg) -> tuple[list[Leg], float] | None:
        """Günstigster Weg Deutschland -> hub vor dem Langstreckenflug lh. Rückgabe (legs, stunden)."""
        if not lh.arr:
            return None
        best = None
        for day in (lh.dep.date() - timedelta(days=1), lh.dep.date()):
            for f in self.feed_to[hub].get(day, []):
                if (lh.dep - f.arr).total_seconds() / 3600 < self._need(f.dest, lh.origin):
                    continue
                hours = (lh.arr - f.dep).total_seconds() / 3600
                if hours <= self.maxh and (best is None or f.price < best[0][0].price):
                    best = ([f], hours)
        g = self._ground_leg(hub, True)
        if g:
            hours = g.hours + 2 + lh.duration_h
            if hours <= self.maxh and (best is None or g.price < best[0][0].price):
                best = ([g], hours)
        return best

    def back_feeder(self, hub: str, lh: Leg) -> tuple[list[Leg], float] | None:
        if not lh.arr:
            return None
        best = None
        for day in (lh.arr.date(), lh.arr.date() + timedelta(days=1)):
            for f in self.feed_from[hub].get(day, []):
                if (f.dep - lh.arr).total_seconds() / 3600 < self._need(lh.dest, f.origin):
                    continue
                if f.arr.date() > self.latest:
                    continue
                hours = (f.arr - lh.dep).total_seconds() / 3600
                if hours <= self.maxh and (best is None or f.price < best[0][0].price):
                    best = ([f], hours)
        g = self._ground_leg(hub, False)
        if g:
            hours = g.hours + 2 + lh.duration_h
            if hours <= self.maxh and (best is None or g.price < best[0][0].price):
                best = ([g], hours)
        return best

    # ---------- Reisen bauen ----------
    def out_journeys(self) -> list[Journey]:
        js = []
        for o in self.de_long:
            js += [Journey([l], l.duration_h) for l in self.ow_out.get(o, [])
                   if l.arr and l.duration_h <= self.maxh]
        for h in self.hubs:
            for l in self.ow_out.get(h, []):
                fb = self.out_feeder(h, l)
                if fb:
                    js.append(Journey(fb[0] + [l], fb[1]))
        return js

    def _tail(self, start: str, lh: Leg) -> tuple[list[Leg], float] | None:
        """Langstrecke nach Europa plus ggf. Zubringer nach Deutschland."""
        if not lh.arr:
            return None
        if start in self.de_long:
            return ([lh], lh.duration_h) if lh.duration_h <= self.maxh else None
        fb = self.back_feeder(start, lh)
        return ([lh] + fb[0], fb[1]) if fb else None

    def back_journeys(self) -> list[Journey]:
        js = []
        for o in self.de_long + self.hubs:
            for l in self.ow_back.get(o, []):
                t = self._tail(o, l)
                if t:
                    js.append(Journey(t[0], t[1]))
        for (x, d), legs in self.oj_long.items():
            for l in legs:
                t = self._tail(d, l)
                if not t:
                    continue
                reg = [r for r in self.regional[x] if r.arr and r.dep.date() >= self.earliest
                       and (l.dep - r.arr).total_seconds() / 3600 >= self.min_tr
                       and (l.dep - r.dep).days <= self.max_stop]
                if not reg:
                    continue
                r = min(reg, key=lambda r: r.price)
                stop = (l.dep.date() - r.arr.date()).days
                js.append(Journey([r] + t[0], t[1], stopover_days=stop, note="openjaw"))
        return js

    def _days_ok(self, out: Journey, back: Journey) -> bool:
        days = (back.start_date - out.start_date).days
        return self.min_days <= days <= self.max_days and back.end_date <= self.latest

    def itineraries(self) -> list[Itinerary]:
        its: list[Itinerary] = []
        # Rundreise-Tickets
        for o, items in self.rt.items():
            for out, back, p in items:
                if o in self.de_long:
                    if out.arr and back.arr and out.duration_h <= self.maxh and back.duration_h <= self.maxh:
                        it = Itinerary(Journey([out], out.duration_h), Journey([back], back.duration_h),
                                       "rt-de", ticket_price=p)
                    else:
                        continue
                else:
                    fo, fb = self.out_feeder(o, out), self.back_feeder(o, back)
                    if not (fo and fb):
                        continue
                    it = Itinerary(Journey(fo[0] + [out], fo[1]), Journey([back] + fb[0], fb[1]),
                                   "rt-hub", ticket_price=p)
                if self._days_ok(it.out, it.back):
                    its.append(it)
        # Einzeltickets kombinieren (je Datum die 3 günstigsten)
        def top_by_day(js):
            by = defaultdict(list)
            for j in js:
                by[j.start_date].append(j)
            return {d: sorted(v, key=lambda j: j.price)[:3] for d, v in by.items()}
        outs, backs = top_by_day(self.out_journeys()), top_by_day(self.back_journeys())
        for od, ojs in outs.items():
            for bd, bjs in backs.items():
                if not self.min_days <= (bd - od).days <= self.max_days:
                    continue
                for oj in ojs:
                    for bj in bjs:
                        if bj.end_date <= self.latest:
                            its.append(Itinerary(oj, bj, "combo"))
        return dedupe(its)

    def run(self) -> list[Itinerary]:
        self.fetch()
        return self.itineraries()


def dedupe(its: list[Itinerary]) -> list[Itinerary]:
    best: dict[str, Itinerary] = {}
    for it in its:
        if it.key not in best or it.price < best[it.key].price:
            best[it.key] = it
    return sorted(best.values(), key=lambda i: i.price)
