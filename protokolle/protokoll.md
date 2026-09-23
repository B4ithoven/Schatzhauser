# Protokoll der Änderungen

## 22. September 2026
- Verzeichnisstruktur erstellt.
- Dateien `schatzhauser.py` und `handbuch.md` kopiert.
- Protokoll angelegt.

## 23. September 2026
- Backup vor Änderungen: `backup_20260923/`.
- `--train` repariert: Beim Export war am Dateiende Code **nach** `main()` angehängt
  (`FREEDOM24_AKTIEN` mit 3 Titeln + `train_bot()`, das die nicht existierende Funktion
  `train_model_with_decay` aufrief). Der Block wurde nie ausgeführt, überschrieb aber die
  Universums-Idee. Entfernt; `--train` nutzt wieder `train_p()`.
- Universum: feste Liste (≈ 240 Aktien) ersetzt durch alle Freedom24-handelbaren Aktien
  (≈ 9.000; US, EU, HK, Athen, Istanbul). ETF-/Krypto-Liste bleibt.
- Bewertungssystem (Späher aus Grok-Webprojekt `scan.ts`) nach Python portiert und in den Scan eingebaut.
- Fehler behoben:
  - Kurs-Cache wurde nie aktualisiert (nach 20 h wurden nur fehlende Titel geladen) → tägliches Nachladen.
  - London-Kurse sind bei Yahoo in Pence: Bot rechnete AstraZeneca mit ≈ 14.900 € statt ≈ 148 € je Stück.
  - Währungen fehlten (CHF, DKK, PLN, TRY) → wurden als EUR gerechnet.
  - Stopp verglich Kurs in USD mit Einstand in EUR (TSLA im Chat-Depot).
  - Haltetage zählten pro Programmlauf statt pro Kalendertag.
  - Verkaufsbuchung: HK-/Krypto-Gebühren und Wechselgebühr korrigiert.
  - Kaufvorschläge im selben Lauf konnten zusammen mehr als die Kasse kosten.
- Indikatoren (Hoch/Tief-Fenster, Effizienz) vektorisiert, Ergebnisse identisch zur alten Version.

## 23. September 2026 (Teil 2)
- Backup: `backup_20260923_v2/`.
- Backtest `--backtest` (Monat, Jahr, mehrere Jahre, `3y`/`6m`), gleiche Regeln wie Scan,
  P-Modell nur aus Daten vor dem Start, historische Wechselkurse, CSV nach `bot/backtests/`.
  Signalberechnung vektorisiert, mit Originalregeln auf 400 Titeln abgeglichen (0 Abweichungen).
- `--help` auf Deutsch mit Beispielen, `--manual [BEGRIFF]` durchsucht das Handbuch.
- Depot von Hand: `--buy`, `--sell`, `--add`, `--remove`, `--set-cash`, `--import CSV`, `--history`,
  Optionen `--date`, `--fee`, `--ccy`, `--force`. Freedom24- und Yahoo-Kürzel werden erkannt.
- Freedom24-Lose: echte Losgröße je Titel (HK 100/500/1000/2000 …, London teils 100). Kauf nur in
  Vielfachen; falsche Stückzahl bei Handeingabe wird abgelehnt.
- ETF/Krypto ausgeschaltet (`ETF_CRYPTO = False`).
- Mindestorder 150 € (`--min-order`): Ein Backtest 2022–2024 ohne Grenze ergab −99 % –
  Hauptursache waren Mini-Käufe von wenigen Euro, bei denen die Gebühr (2 € je Richtung)
  im Median 21 % pro Trade kostete. Ergebnis mit Mindestorder muss lokal neu geprüft werden.
- Analyse Backtest 2022–2024 des Nutzers (−1,2 %; 2022 +15,6 %, 2023 −13,3 %, 2024 −1,2 %):
  Depot fast leer (Ø 1,5–2,5 Posten), 40 von ~5.400 Signalen gekauft (Mindestorder/HK-Lose),
  Marktlage 2023/24 fast nur „neutral“. Neue Test-Schalter `--size nav`, `--min-price`,
  `--regime index` (Standard unverändert), Test durch Nutzer lokal.
- Stopp-Analyse Backtest 2020–25 (541 Trades): Stopps aus unruhigen Titeln mit flachem SMA 200,
  nicht aus Seitwärtsphasen. Neue Schalter `--trend-min`, `--range-max`, `--breakeven`
  (Standard aus), Grund `EINSTAND`. Test durch Nutzer lokal.
- Wechselkurse 1 Stunde gecacht (`schatzhauser_fx.json`); Depot-Befehle bauen die Freedom24-Liste nie neu auf.
