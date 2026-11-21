"""Datenquellen: Travelpayouts (Aviasales-Preiscache) und Google Flights über SerpApi."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Leg

UA = "flightwatch/1.0"


def _get_json(url: str, headers: dict | None = None, timeout: int = 40) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class TravelpayoutsClient:
    """Abfragen von /aviasales/v3/prices_for_dates mit Datei-Cache."""

    URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

    def __init__(self, token: str, cache_dir: Path, pause: float = 0.25, log=print):
        self.token = token
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.pause = pause
        self.log = log
        self.calls = 0
        self.errors = 0

    def prices(self, origin: str, dest: str, dep_month: str, ret_month: str | None = None,
               one_way: bool = True, ttl_h: float = 0) -> list[dict]:
        params = {
            "origin": origin, "destination": dest, "departure_at": dep_month,
            "one_way": "true" if one_way else "false", "direct": "false",
            "currency": "eur", "sorting": "price", "unique": "false",
            "limit": 1000, "page": 1, "token": self.token,
        }
        if ret_month and not one_way:
            params["return_at"] = ret_month
        cache_key = hashlib.sha1(json.dumps({k: v for k, v in params.items() if k != "token"},
                                            sort_keys=True).encode()).hexdigest()
        cache_file = self.cache_dir / f"{cache_key}.json"
        if ttl_h > 0 and cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text("utf-8"))
                if time.time() - cached["t"] < ttl_h * 3600:
                    return cached["data"]
            except (ValueError, KeyError):
                pass
        url = self.URL + "?" + urllib.parse.urlencode(params)
        data = None
        for attempt in range(3):
            try:
                self.calls += 1
                res = _get_json(url, {"X-Access-Token": self.token})
                if res.get("success", True) is False:
                    self.errors += 1
                    self.log(f"[travelpayouts] {origin}->{dest} {dep_month}: {res.get('error')}")
                    data = []
                else:
                    data = res.get("data") or []
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(10 * (attempt + 1))
                    continue
                self.errors += 1
                self.log(f"[travelpayouts] {origin}->{dest} {dep_month}: HTTP {e.code}")
                break
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                self.errors += 1
                self.log(f"[travelpayouts] {origin}->{dest} {dep_month}: {e}")
                time.sleep(2)
        time.sleep(self.pause)
        if data is None:
            return []
        if ttl_h > 0:
            cache_file.write_text(json.dumps({"t": time.time(), "data": data}), "utf-8")
        return data


def tp_link(item: dict) -> str:
    link = item.get("link") or ""
    return "https://www.aviasales.com" + link if link.startswith("/") else link


def tp_to_legs(item: dict, qfrom: str, qto: str, one_way: bool) -> tuple[Leg | None, Leg | None, float]:
    """Wandelt einen Travelpayouts-Eintrag in Hin- (und Rück-)Leg um."""
    price = float(item.get("price") or 0)
    dep = parse_dt(item.get("departure_at"))
    if not dep or price <= 0:
        return None, None, 0
    o = item.get("origin_airport") or item.get("origin") or qfrom
    d = item.get("destination_airport") or item.get("destination") or qto
    dur_to = item.get("duration_to") or (item.get("duration") if one_way else None)
    carrier = f"{item.get('airline', '')}{item.get('flight_number', '')}".strip()
    link = tp_link(item)
    out = Leg(origin=o, dest=d, price=price if one_way else 0, dep=dep,
              arr=dep + timedelta(minutes=dur_to) if dur_to else None,
              carrier=carrier, transfers=int(item.get("transfers") or 0), link=link,
              qfrom=qfrom, qto=qto)
    if one_way:
        return out, None, price
    rdep = parse_dt(item.get("return_at"))
    if not rdep:
        return None, None, 0
    dur_back = item.get("duration_back")
    back = Leg(origin=d, dest=o, price=0, dep=rdep,
               arr=rdep + timedelta(minutes=dur_back) if dur_back else None,
               carrier=item.get("airline", ""), transfers=int(item.get("return_transfers") or 0),
               link=link, qfrom=qto, qto=qfrom)
    return out, back, price


class GoogleFlights:
    """Google Flights über SerpApi (engine=google_flights)."""

    URL = "https://serpapi.com/search.json"

    def __init__(self, api_key: str, log=print):
        self.api_key = api_key
        self.log = log
        self.calls = 0

    def search(self, dep_ids: str, arr_ids: str, out_date: str, ret_date: str | None,
               max_minutes: int, bags: int = 1) -> dict | None:
        params = {
            "engine": "google_flights", "departure_id": dep_ids, "arrival_id": arr_ids,
            "outbound_date": out_date, "type": 1 if ret_date else 2,
            "currency": "EUR", "hl": "de", "gl": "de", "adults": 1,
            "max_duration": max_minutes, "bags": bags, "sort_by": 2,
            "api_key": self.api_key,
        }
        if ret_date:
            params["return_date"] = ret_date
        try:
            self.calls += 1
            return _get_json(self.URL + "?" + urllib.parse.urlencode(params), timeout=90)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            self.log(f"[serpapi] {dep_ids}->{arr_ids} {out_date}: {e}")
            return None

    @staticmethod
    def cheapest(res: dict | None) -> dict | None:
        if not res:
            return None
        options = (res.get("best_flights") or []) + (res.get("other_flights") or [])
        options = [o for o in options if o.get("price")]
        return min(options, key=lambda o: o["price"]) if options else None
