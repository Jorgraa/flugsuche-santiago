"""Offline-Test der Kombinationslogik mit erfundenen Travelpayouts-Daten.
Start: python -m unittest discover -s tests   (im Projektordner)
"""
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flightwatch import links  # noqa: E402
from flightwatch.search import Search  # noqa: E402


def item(o, d, dep, dur_min, price, ret=None, dur_back=None, transfers=1, airline="XX"):
    it = {"origin": o, "destination": d, "origin_airport": o, "destination_airport": d,
          "departure_at": dep, "price": price, "airline": airline, "flight_number": "123",
          "transfers": transfers, "link": f"/search/{o}{d}"}
    if ret:
        it.update(return_at=ret, duration_to=dur_min, duration_back=dur_back, return_transfers=transfers)
    else:
        it.update(duration=dur_min, duration_to=dur_min)
    return it


class FakeTP:
    """Liefert feste Antworten je (origin, dest, one_way, Monat)."""

    def __init__(self, data):
        self.data = data
        self.calls = 0
        self.errors = 0

    def prices(self, origin, dest, dep_month, ret_month=None, one_way=True, ttl_h=0):
        self.calls += 1
        out = []
        for it in self.data.get((origin, dest, one_way), []):
            if not it["departure_at"].startswith(dep_month):
                continue
            if not one_way and ret_month and not it["return_at"].startswith(ret_month):
                continue
            out.append(it)
        return out


DATA = {
    # Rundreise ab Frankfurt: 1100 €, 15 h
    ("FRA", "SCL", False): [item("FRA", "SCL", "2027-06-10T20:00:00+02:00", 900, 1100,
                                 ret="2027-07-08T22:00:00-04:00", dur_back=960)],
    # Rundreise Madrid: 520 €
    ("MAD", "SCL", False): [item("MAD", "SCL", "2027-06-12T23:55:00+02:00", 840, 520,
                                 ret="2027-07-10T13:00:00-04:00", dur_back=780, transfers=0)],
    # Zubringer BER->MAD am selben Tag, Ankunft 12:30 (genug Puffer)
    ("BER", "MAD", True): [item("BER", "MAD", "2027-06-12T09:30:00+02:00", 180, 35, transfers=0),
                           # zu knapp: Ankunft 22:00, Abflug 23:55 -> verworfen
                           item("BER", "MAD", "2027-06-12T19:00:00+02:00", 180, 15, transfers=0)],
    # Rückzubringer MAD->BER am Folgetag (Langstrecke landet 08:00 MEZ)
    ("MAD", "BER", True): [item("MAD", "BER", "2027-07-11T12:00:00+02:00", 180, 40, transfers=0),
                           # zu knapp (1 h nach Landung) -> verworfen
                           item("MAD", "BER", "2027-07-11T09:00:00+02:00", 180, 10, transfers=0)],
    # Gabelflug-Teile
    ("SCL", "EZE", True): [item("SCL", "EZE", "2027-07-05T08:00:00-04:00", 130, 60, transfers=0)],
    ("EZE", "MUC", True): [item("EZE", "MUC", "2027-07-08T14:00:00-03:00", 1000, 380)],
    # Einzelticket hin ab München
    ("MUC", "SCL", True): [item("MUC", "SCL", "2027-06-14T21:00:00+02:00", 1080, 360),
                           # zu lang (35 h) -> verworfen
                           item("MUC", "SCL", "2027-06-15T21:00:00+02:00", 2100, 200)],
}


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.cfg = tomllib.loads((ROOT / "config.toml").read_text("utf-8"))
        self.its = Search(self.cfg, FakeTP(DATA), log=lambda *_: None).run()

    def test_hub_roundtrip_with_feeders(self):
        hub = [i for i in self.its if i.kind == "rt-hub"]
        self.assertTrue(hub, "Madrid-Rundreise mit Zubringer fehlt")
        best = hub[0]
        # 520 + 35 (nicht der zu knappe 15-€-Flug) + 40
        self.assertEqual(best.price, 595)
        self.assertEqual(best.out.legs[0].price, 35)
        self.assertLessEqual(best.out.hours, 30)

    def test_direct_roundtrip(self):
        self.assertTrue(any(i.kind == "rt-de" and i.price == 1100 for i in self.its))

    def test_open_jaw_combo(self):
        oj = [i for i in self.its if i.back.note == "openjaw"]
        self.assertTrue(oj, "Gabelflug über Buenos Aires fehlt")
        it = min(oj, key=lambda i: i.price)
        # hin München 360 + SCL->EZE 60 + EZE->MUC 380
        self.assertEqual(it.price, 800)
        self.assertEqual(it.back.stopover_days, 3)
        out, back = links.longhaul_pair(it)
        self.assertEqual((back.origin, back.dest), ("EZE", "MUC"))

    def test_too_long_flight_rejected(self):
        self.assertFalse(any(l.price == 200 for i in self.its for l in i.out.legs))

    def test_window_and_days(self):
        for i in self.its:
            self.assertTrue(21 <= i.days <= 35)
            self.assertLessEqual(i.back.end_date.isoformat(), "2027-09-30")

    def test_links_and_report(self):
        from datetime import datetime
        from flightwatch.report import write_report
        for i in self.its:
            self.assertTrue(links.all_links(i))
        out = ROOT / "tests" / "_report_test.md"
        write_report(out, self.its, {}, self.cfg, datetime(2026, 11, 21, 8, 0))
        self.assertIn("595", out.read_text("utf-8"))
        out.unlink()


if __name__ == "__main__":
    unittest.main()
