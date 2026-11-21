# ✈️ Flugsuche Deutschland ⇄ Santiago de Chile

Sucht zweimal täglich automatisch nach günstigen Flügen (Juni bis September 2027, 3 bis 5 Wochen,
max. 30 h pro Richtung) und schickt eine Push-Nachricht aufs Handy, wenn ein sehr gutes Angebot auftaucht.

**Aktueller Stand → [REPORT.md](REPORT.md)**

## Was wird gesucht?

| Strategie | Beispiel |
|---|---|
| Rundreise direkt ab Deutschland | FRA ⇄ SCL |
| Langstrecke ab europäischem Drehkreuz + Billigflieger/Zug dorthin | BER → BCN (Ryanair) → SCL mit Level |
| Hin- und Rückflug als getrennte Einzeltickets | hin ab Madrid, zurück nach München |
| Gabelflug zurück | SCL → Buenos Aires (Billigflug, bis 4 Tage Stopover) → Europa |

Selbst kombinierte Umstiege bekommen mindestens 3 h Puffer (5 h bei Flughafenwechsel). Alle Reisen
dürfen pro Richtung höchstens 30 h dauern.

**Datenquellen (kostenlos):**
- **Travelpayouts / Aviasales Data API:** viele Strecken auf einmal. Die Preise sind zwischengespeichert (bis ca. 48 h alt).
- **Google Flights über SerpApi:** 250 Live-Suchen pro Monat. Die Suche macht damit Stichproben und prüft gute Treffer live, bevor die Push-Nachricht rausgeht.
- Jede Push-Nachricht und jeder Eintrag im Bericht enthält Links zu **Google Flights, Skyscanner, Kiwi und Skiplagged**. Damit kannst du vor dem Buchen gegenprüfen.

## Push-Regeln

- Unter **750 €** (hin und zurück inkl. Zubringer): normale Push-Nachricht ✈️
- Unter **550 €**: dringender Alarm 🚨
- Dasselbe Angebot kommt nur noch einmal, wenn es um mindestens 25 € billiger wird
- Jeden Montag eine Wochen-Zusammenfassung 📊

Alle Werte stehen in [config.toml](config.toml) und lassen sich dort ändern.

## Einrichtung (einmalig, ca. 15 Minuten)

1. **GitHub-Repository anlegen:** Konto auf github.com erstellen und ein neues Repository anlegen, z. B. `flugsuche-santiago`.
   - *Public* ist empfohlen: Dann sind die Actions-Minuten unbegrenzt. Die Preisdaten sind nicht sensibel, die Schlüssel liegen geschützt in den Secrets.
   - *Private* geht auch. Die 2.000 Freiminuten pro Monat reichen.
2. **Dateien hochladen:** Im Repository auf *Add file → Upload files* klicken und den **Inhalt** dieses Ordners hineinziehen, auch den Ordner `.github`. Danach prüfen, ob `.github/workflows/flugsuche.yml` angekommen ist.
   Falls nicht: *Add file → Create new file*, als Namen `.github/workflows/flugsuche.yml` eintippen und den Inhalt einfügen.
3. **Travelpayouts-Token:** Kostenlos auf travelpayouts.com registrieren. Den API-Token findest du im Profil unter *API token*.
4. **SerpApi-Key:** Kostenlos auf serpapi.com registrieren und im Dashboard den *API Key* kopieren.
5. **ntfy:** Die App „ntfy“ installieren (Android/iOS), auf *+* tippen und dein geheimes Topic abonnieren. Das Topic ist wie ein Passwort: Wer es kennt, liest mit.
6. **Secrets eintragen:** Im Repository *Settings → Secrets and variables → Actions → New repository secret* öffnen und diese drei anlegen:
   - `TRAVELPAYOUTS_TOKEN`
   - `SERPAPI_KEY`
   - `NTFY_TOPIC`
7. **Erster Lauf:** Den Tab *Actions* öffnen, Workflows aktivieren, dann *Flugsuche Santiago → Run workflow*.
   Nach ca. 5 bis 10 Minuten ist [REPORT.md](REPORT.md) gefüllt.

## Gut zu wissen

- **Getrennte Tickets:** Verpasst du wegen einer Verspätung den Anschluss, hilft dir keine Airline. Den Puffer also lieber großzügig wählen.
- **Hidden-City-Tickets (Skiplagged):** Nur als One-Way und nur mit Handgepäck. Airlines können Folgeflüge streichen.
- **Zugpreise** zu Drehkreuzen wie Amsterdam, Brüssel oder Paris sind Schätzwerte in `config.toml`. Bitte vor dem Buchen bei der Bahn prüfen.
- **Keine Aktivität:** GitHub pausiert zeitgesteuerte Workflows nach 60 Tagen ohne Aktivität im Repository. Die täglichen Ergebnis-Commits verhindern das normalerweise.
