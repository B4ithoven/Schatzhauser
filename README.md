# Schatzhauser

Vorschlags-Bot für Aktien, die bei **Freedom24** handelbar sind (USA, Europa, Hongkong, Athen, Istanbul).
Der Bot **handelt nie selbst**. Er schlägt Käufe und Verkäufe vor, geordert wird von Hand.

- Kauf: Ichimoku (9/26/52), über SMA 200, Wolke ≥ 2,5 %, P-Modell ≥ 52 %, Uniqueness, Deckel je Marktlage
- Verkauf: Ichimoku-Signal, Regel B, Tag-5-Regel, Stopp −8 %
- Bewertung (Bilanz-Punkte, Rang, Empfehlung)
- Freedom24-Lose (z. B. Hongkong 100/1000 Stück) und Gebühren werden beachtet
- Backtest über Monate oder Jahre, Depot von Hand pflegen

## Installation

```
pip install -r requirements.txt
python3 bot/schatzhauser.py
```

Der erste Lauf lädt Kurse für rund 9.000 Aktien von Yahoo Finance (45–60 Minuten).
Danach werden nur neue Tage nachgeladen.

## Wichtige Befehle

```
python3 bot/schatzhauser.py --help                   alle Optionen mit Beispielen
python3 bot/schatzhauser.py --manual BEGRIFF         Handbuch durchsuchen
python3 bot/schatzhauser.py --backtest 2021:2024     Backtest
python3 bot/schatzhauser.py --buy SAP.DE 5 182.40    Kauf eintragen
python3 bot/schatzhauser.py --status                 Depot anzeigen
```

Ausführlich: [handbuch/handbuch.md](handbuch/handbuch.md), Änderungen: [protokolle/protokoll.md](protokolle/protokoll.md)

## Hinweis

Keine Anlageberatung. Backtests zeigen die Vergangenheit und enthalten Vereinfachungen
(siehe Handbuch, Abschnitt Backtest). Kursdaten: Yahoo Finance, Wertpapierliste: öffentliches
Freedom24-Verzeichnis.
