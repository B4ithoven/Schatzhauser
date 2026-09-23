# Schatzhauser Tradebot Handbuch (Version 0.4)

## Indikatoren

Der Tradebot verwendet folgende Indikatoren für seine Entscheidungen:

1. **Ichimoku Cloud**:
   - **Tenkan-sen**: Ein kurzfristiger Durchschnitt, der Trendrichtung und Dynamik anzeigt.
   - **Kijun-sen**: Ein mittelfristiger Durchschnitt, der als Unterstützungs- oder Widerstandsniveau dient.
   - **Senkou Span A & B**: Diese Linien bilden die Wolke (Kumo), die die Unterstützungs- und Widerstandsniveaus für die Zukunft vorhersagt.
   - **Wolke (Kumo)**: Die Wolke wird verwendet, um die Marktrichtung und Trendstärke zu bestimmen.

2. **SMA 200**:
   - Ein gleitender Durchschnitt über 200 Tage, der dazu dient, langfristige Trends zu identifizieren.

3. **MFE (Maximum Favorable Excursion)**:
   - MFE ist eine Art Verlustschutz, der die maximale positive Bewegung nach einem Einstieg misst und dazu dient, Gewinne zu sichern.

4. **Stopp-Loss**:
   - Ein Stopp-Loss wird bei einer Verlusttoleranz von -8% festgelegt, um Verluste zu begrenzen.

## Abhängigkeiten

Der Tradebot verwendet die folgenden Python-Bibliotheken:

1. **yfinance**: Zum Abrufen von Finanzdaten von Yahoo Finance.
2. **pandas**: Für die Datenmanipulation und -analyse.
3. **numpy**: Für numerische Berechnungen.

## Modelltraining

Das Modell wird durch die Funktion `train` trainiert, die historische Daten verwendet, um die Wahrscheinlichkeiten für bestimmte Handelsentscheidungen zu berechnen. Die Ergebnisse werden in der Datei `schatzhauser_p.json` gespeichert, die für die Entscheidungsfindung verwendet wird.

## Handelsregeln

### Kaufregeln:
- **Ichimoku Cloud**: Die Preise müssen über der Wolke liegen (Wolke ≥ 2,5%).
- **SMA 200**: Die Preise müssen über dem SMA 200 liegen.
- **P**: Die Wahrscheinlichkeit (P) muss ≥ 52% betragen.
- **Uniqueness**: ≥ 0,20.
- **Deckel**: Beschränkungen für Bullen (33%), Neutrale (20%) und Bären (10%).

### Verkaufsregeln:
- **TK runter**: Die Preise fallen unter die TK-Linie.
- **Unter der Wolke**: Die Preise fallen unter die Wolke.
- **Regel B**: Ab Tag 10 mit MFE ≥ 4% und noch im Plus.
- **Tag-5-Kratzer**: Verkauf nach 5 Tagen mit einem bestimmten Schwellenwert.
- **Stopp-Loss**: Verkauf bei einem Verlust von -8%.

### Gebühren:
- **Grundgebühr**: 2 EUR.
- **Pro Stück**: 0,02 EUR.
- **Hongkong**: 0,25% + 10 HKD.
- **Krypto**: 0,2%.
- **Wechselgebühr**: 0,15%.

## Universum: alle Freedom24-Aktien

Der Bot prüft alle Aktien, die bei Freedom24 handelbar sind (ca. 9.000):

| Markt | Quelle der Liste | Yahoo-Kürzel |
|---|---|---|
| USA (NYSE/NASDAQ/AMEX) | NASDAQ-Symbolliste, geprüft über Freedom24 `/securities/export` | `AAPL`, `BRK-B` |
| Europa (XETRA, LSE, Euronext, Mailand, Madrid, SIX, Wien, Helsinki, Stockholm, Oslo) | Freedom24-Verzeichnis `EU` | `SAP.DE`, `AZN.L`, `MC.PA` … |
| Hongkong | Freedom24-Verzeichnis `HKEX` (letzter öffentlicher Stand 19.02.2026) | `0700.HK` |
| Athen | Freedom24-Verzeichnis `ATHEX` | `SBLK.AT` |
| Istanbul | Freedom24-Verzeichnis `BIST` | `THYAO.IS` |

Die Liste wird wöchentlich neu gebaut (`schatzhauser_f24.json`). ETFs und Krypto stehen
weiter fest im Code (`UNIVERSE`). Nicht enthalten: Kasachstan (KASE/AIX), Russland, Anleihen, Optionen.

## Bewertungssystem (Späher)

Aus dem Grok-Webprojekt (`src/lib/scan.ts`) nach Python übernommen:

- **Bilanz-Punkte** (7 Checks, Yahoo-Daten): Marge > 0, ROE ≥ 8 %, Liquidität ≥ 1,
  Verschuldung (D/E) < 250, operativer Cashflow > 0, Umsatz wächst, Gewinn höher als Vorjahr.
- **Rang**: LANG, TREND, TERMIN, ÜBERDEHNT (> 25 % über SMA 200), BILANZ SCHWACH (< 60 % der Punkte),
  UNTER SCHNITT (unter SMA 200 oder 12-Monats-Momentum negativ), KEINE DATEN.
- **Empfehlung**: NEIGUNG JA, NUR TREND, WARTEN, MEIDEN, UNSICHER.
- Ein Ichimoku-Kaufsignal wird **gesperrt**, wenn die Empfehlung MEIDEN ist oder in ≤ 5 Tagen Bilanztermin ist.
- Bilanzen werden nur für die besten 60 Kaufkandidaten und das Depot geladen (Cache 3 Tage).

## Hongkong-Lose und Mindeststückzahl

Freedom24 handelt viele Titel nur in festen Paketen („Lot“ / Brettlos). Der Bot liest das Los
jedes Titels aus dem Freedom24-Verzeichnis (`x_lot`) und rechnet nur mit ganzen Vielfachen:

- Hongkong: fast alle Titel, z. B. Tencent 100, Bank of China 1000, andere 500, 2000, 10000 …
- London: einige Titel 100 (z. B. Unilever, Vodafone), manche XETRA-Titel 10.
- USA und die meisten EU-Titel: 1 Stück.

Folgen:
- Kaufvorschläge zeigen z. B. „(2 Los à 100)“.
- Ist schon das kleinste Los zu teuer für das Budget je Posten, steht der Titel unter „übersprungen“.
- `--buy`/`--sell`/`--import` **lehnen** falsche Stückzahlen ab (z. B. 150 × Tencent). `--force` erlaubt es trotzdem.
- `--status` zeigt die Losgröße je Posten.

Mindestorder: Käufe unter 150 € schlägt der Bot nicht vor (2 € Gebühr je Richtung fressen sonst
den Gewinn). Ändern mit `--min-order 300`.

## Depot von Hand pflegen

So weiß der Bot, was du bei Freedom24 wirklich gekauft und verkauft hast.
Preise in der Handelswährung (London in Pfund, nicht Pence; Hongkong in HKD).
Als Ticker gehen Yahoo-Kürzel (`SAP.DE`, `0700.HK`, `AAPL`) oder Freedom24-Kürzel (`SAP.EU`, `TENC.700.AS`, `AAPL.US`).

| Befehl | Wirkung |
|---|---|
| `--buy TICKER STÜCK PREIS` | Kauf buchen, Kasse sinkt um Kurswert + Gebühr |
| `--sell TICKER STÜCK PREIS` | Verkauf buchen, Kasse steigt, Ergebnis wird angezeigt |
| `--sell TICKER` | ganzen Posten zum letzten bekannten Kurs verkaufen |
| `--add TICKER STÜCK EINSTAND` | Altbestand eintragen, Kasse bleibt gleich |
| `--remove TICKER` | Posten ohne Buchung löschen (Tippfehler) |
| `--set-cash EUR` | Kontostand setzen |
| `--import datei.csv` | ganzes Aktiendepot aus CSV übernehmen (ersetzt Aktien, ETF/Krypto bleiben) |
| `--status` | Depot mit Los, Kaufdatum, Freedom24-Kürzel |
| `--history [N]` | letzte Buchungen |
| `--reset` | zurück auf das Chat-Depot |

Zusatzoptionen: `--date 2026-09-20` (Buchungsdatum, wichtig für Tag-5-Regel und Regel B),
`--fee 3.10` (echte Gebühr aus der Freedom24-Abrechnung), `--ccy USD`, `--force`.

CSV-Format (Komma oder Semikolon, Dezimalkomma erlaubt, `#` = Kommentar):

    ticker;qty;cost;ccy;date
    TENC.700.AS;200;420;HKD;2026-08-01
    SAP.EU;10;175,5;EUR;2026-07-15

Nach einem Verkauf mit Verlust setzt der Bot eine „Narbe“ (5 Tage kein Neukauf), nach jedem Verkauf 2 Tage Pause.

## Backtest

Spielt die Regeln Tag für Tag über die Vergangenheit ab, mit denselben Kauf- und Verkaufsregeln,
Deckel (Stier/Neutral/Bär), Uniqueness, max. 20 Posten, Freedom24-Losen, Gebühren und Mindestorder wie der Scan.

    --backtest 2024-03          ein Monat
    --backtest 2023             ein Jahr
    --backtest 2021:2024        mehrere Jahre
    --backtest 2023-06:2024-02  Monat bis Monat
    --backtest 3y   /   6m      letzte 3 Jahre / 6 Monate
    --cash 5000                 Startkapital
    --by month | year | none    Aufstellung (Standard: bis 13 Monate je Monat, sonst je Jahr)
    --markets US,EU             nur bestimmte Märkte
    --min-order 300             kleinster Kauf in €
    --no-retrain                gespeichertes P-Modell nehmen
    --no-save                   keine CSV-Dateien

Test-Schalter (Standard = bisheriges Verhalten; gelten auch für den Scan):

    --size cash | nav           Deckel in % der freien Kasse (cash) oder des ganzen Depotwerts (nav)
    --min-price 1               keine Aktien unter 1 € Kurs (Pennystocks)
    --regime breite | index     Stier/Bär aus allen Aktien (breite) oder aus dem MSCI World (index)

Warum diese Schalter (Analyse Backtest 2022–2024, −1,2 %): Das Depot war fast nie investiert
(Ø 1,5–2,5 Posten statt bis zu 20). Von ~5.400 Kaufsignalen wurden nur 40 gekauft:
4.118 scheiterten an der Mindestorder, 1.284 an zu teuren Hongkong-Losen. Grund: Der Deckel
(20 %) wird von der **freien Kasse** gerechnet – 20 % von 1.000 € = 200 €, nach wenigen Käufen
fällt das Budget unter 150 €. Die Marktbreite (alle 9.000 Aktien, viele Nebenwerte) meldete
2023/24 fast nur „neutral“, obwohl der MSCI World stieg; „Stier“ gab es nur an 22 Tagen.
Pro Trade war die Strategie auch 2023/24 im Plus (Median +1,2 % / +1,5 % Kurs), das Geld lag
aber brach, und wenige Pennystock-Stopps (z. B. −67 %) fraßen die Gewinne.

Stopp-Filter (Standard aus):

    --trend-min 0.013           Kauf nur, wenn SMA 200 in 20 Tagen um ≥ 1,3 % gestiegen ist
    --range-max 0.18            Kauf nur, wenn die Spanne Hoch/Tief der letzten 60 Tage ≤ 18 % ist
    --breakeven 0.04            war der Posten +4 % im Plus, raus sobald er auf Einstand fällt (Grund EINSTAND)

Analyse Stopps 2020–2025 (541 Trades, 165 Stopps = −8.317 €, Verkäufe nach Regel +11.092 €):
- Stopps kommen **nicht** aus flachen Seitwärtsphasen, sondern aus **unruhigen** Titeln mit
  flachem Langfristtrend: Der Kaufzeitpunkt war bei Stopps typisch nach großen Schwankungen
  (Spanne 60 Tage > 18 %) und bei kaum steigendem SMA 200.
- Stopp-Quote nach Filter (je Hälfte 2020–22 / 2023–25 geprüft):
  SMA 200 steigt: 34→30 % / 29→25 %; Spanne ≤ 18 %: 34→26 % / 29→27 %;
  beide + ATR < 1,2 %: 34→22 % / 29→18 %, Gewinnquote 43→55 % / 42→49 %,
  aber nur noch ca. ⅓ der Käufe.
- Die Marktlage (Stier/Bär) änderte die Stopp-Quote kaum (30–34 %).
- RSI, Wolkendicke und Abstand zum SMA 200 trennen Stopps nicht von Gewinnern.
- 75 von 165 Stopp-Trades waren vorher ≥ 2 % im Plus, 39 ≥ 4 % – daher `--breakeven`.

Ausgabe: Endwert, Rendite, CAGR, max. Rückgang, Vergleich mit MSCI World (EUNL.DE), Trefferquote,
Verkaufsgründe, beste/schlechteste Trades, Aufstellung je Monat/Jahr.
Ergebnisse als CSV in `bot/backtests/` (Trades und tägliche Depotkurve) – z. B. in LibreOffice öffnen.

Ehrlichkeit des Tests:
- Das P-Modell lernt standardmäßig **nur aus Trades vor dem Teststart** (kein Blick in die Zukunft).
- Wechselkurse sind historisch (Tageskurse von Yahoo).
- Gekauft und verkauft wird zum Schlusskurs des Signaltags (in echt eher am nächsten Morgen).

Grenzen:
- Die Aktienliste ist die **heutige** Freedom24-Liste. Pleitefirmen und Delistings fehlen (Survivorship-Bias), der Test ist dadurch eher zu gut.
- Die Bilanz-Bewertung (Späher) fehlt im Backtest, weil Yahoo keine historischen Bilanzen liefert.
- Kurse liegen im Cache erst ab ca. Herbst 2019. SMA 200 und Ichimoku brauchen ~300 Handelstage Vorlauf, sinnvolle Tests beginnen daher ab 2021.
- Laufzeit: 1 Monat ~30 s, mehrere Jahre über alle Märkte einige Minuten.

## Befehlszeilenargumente

Der Bot **handelt nie selbst**. Er zeigt Vorschläge; ordern musst du in Freedom24.
Alle Optionen mit Beispielen: `python3 schatzhauser.py --help`.
Handbuch durchsuchen: `python3 schatzhauser.py --manual hongkong` (zeigt nur Abschnitte mit dem Begriff).

- `python3 schatzhauser.py`: Scan aller Freedom24-Aktien → Depot, Verkaufs-/Kaufvorschläge, Beobachtungsliste.
- `--apply`: Vorschläge ins **Papier-Depot** buchen (kein echter Handel).
- `--train`: P-Modell neu lernen, danach scannen.
- `--universe`: Freedom24-Aktienliste sofort neu aufbauen.
- `--markets US,EU`: nur bestimmte Märkte (US, EU, HK, GR, TR).
- `--top 30`: längere Beobachtungsliste.
- `--backtest ZEITRAUM`: siehe Abschnitt Backtest.
- `--buy`, `--sell`, `--add`, `--remove`, `--set-cash`, `--import`, `--status`, `--history`, `--reset`: siehe „Depot von Hand pflegen“.
- `--manual [BEGRIFF]`: dieses Handbuch anzeigen oder durchsuchen.

ETFs und Krypto sind vorerst ausgeschaltet (`ETF_CRYPTO = False` im Code): kein Scan, keine Signale.
Vorhandene Posten (z. B. Bitcoin) bleiben im Depot und werden in `--status` angezeigt.

Der erste Lauf lädt ca. 9.000 Kursreihen von Yahoo (45–60 Minuten, schon erledigt). Danach werden nur neue Tage nachgeladen (1–3 Minuten).
