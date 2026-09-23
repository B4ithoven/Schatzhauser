#!/usr/bin/env python3
"""
Schatzhauser 0.4 — Papier-Bot für Freedom24 (eine Datei).

Regeln (Stand 18.09.2026, nach CPCV):
  Kauf: Ichimoku 9/26/52, Whip (nicht am Kreuz-Tag), Wolke ≥ 2,5 %, über SMA 200,
        P ≥ 52 %, Uniqueness ≥ 0,20, Deckel Stier 33 % / Neutral 20 % / Bär 10 %.
  Verkauf: TK runter oder unter der Wolke; Regel B ab Tag 10 (MFE ≥ 4 %, noch Plus);
           Tag-5-Kratzer; Stopp −8 %. Hongkong nur 100/1000 Stück. Kein Kredit.
  Gebühr: 2 € + 0,02 €/Stück, HK 0,25 % + 10 HK$, Krypto 0,2 %, Wechsel 0,15 %.

Der Bot handelt NIE selbst. Er schlägt Käufe/Verkäufe vor; ordern musst du in Freedom24.

Universum: alle bei Freedom24 handelbaren Aktien (US, EU, Hongkong, Athen, Istanbul),
  aus dem öffentlichen Freedom24-Verzeichnis + /securities/export, wöchentlich neu
  (schatzhauser_f24.json). Dazu die ETF-/Krypto-Liste unten. Kurse: Yahoo.
Bewertung (Späher, aus dem Grok-Webprojekt): Bilanz-Punkte (7 Checks), Rang, Empfehlung.
  Kaufsignale mit Empfehlung MEIDEN oder Bilanztermin ≤ 5 Tage werden gesperrt.

Abhängigkeiten:  pip install yfinance pandas numpy requests

Aufruf (alle Optionen mit Beispielen: --help, Handbuch durchsuchen: --manual BEGRIFF):
  python3 schatzhauser.py                    scannen, nur Vorschläge anzeigen
  python3 schatzhauser.py --backtest 2023    Backtest (2024-03 | 2021:2024 | 3y …)
  python3 schatzhauser.py --buy SAP.DE 5 182.4   echten Kauf eintragen (--sell, --add, --import)
  python3 schatzhauser.py --status           Depot / Kasse / Posten
  python3 schatzhauser.py --train            P-Tor neu lernen, danach scannen
  (Erster Lauf lädt ~9000 Kursreihen: ca. 45–60 min. Danach ca. 1–3 min.)
ETFs/Krypto vorerst ausgeschaltet (ETF_CRYPTO). Freedom24-Lose (HK 100/1000 …) werden beachtet.

Zustand liegt neben dieser Datei: schatzhauser_state.json
Kurs-Cache: schatzhauser_cache.pkl   P-Modell: schatzhauser_p.json
Freedom24-Liste: schatzhauser_f24.json   Bilanzen: schatzhauser_fund.json
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import math
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import requests
    import yfinance as yf
except ImportError:
    print("Bitte: pip install yfinance pandas numpy requests", file=sys.stderr)
    sys.exit(1)

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

HERE = Path(__file__).resolve().parent
STATE = HERE / "schatzhauser_state.json"
CACHE = HERE / "schatzhauser_cache.pkl"
PFILE = HERE / "schatzhauser_p.json"
F24FILE = HERE / "schatzhauser_f24.json"  # Freedom24-Universum (Wochen-Cache)

VER = "0.4"
FEE, FEE_PS, FX = 2.0, 0.02, 0.0015
STOP, P_MIN, U_MIN, TYP_HOLD = 0.08, 0.52, 0.20, 50
MAXPOS, COOL, SCAR = 20, 2, 5
WHIP = 0.025
D5, D5_MAE, D5_MFE = 5, -0.03, 0.02
T_MAE, T_STOP = -0.025, 0.02
B_HOLD, B_MFE = 10, 0.04
DECAY = 0.999
ETF_CRYPTO = False  # ETFs/Krypto vorerst außen vor (kein Scan, keine Signale)
MIN_ORDER = 150.0  # € – kleinere Käufe lohnen nicht (2 € Gebühr je Richtung = ≥ 2,7 % Kosten)
# Test-Schalter (Standard = bisheriges Verhalten), per --size / --min-price / --regime änderbar:
SIZE_BASE = "cash"   # "cash": Deckel % der freien Kasse | "nav": % des ganzen Depotwerts
MIN_PRICE_EUR = 0.0  # Aktien unter diesem Kurs (in €) nicht kaufen (Pennystocks), 0 = aus
REGIME = "breite"    # "breite": Stier/Bär aus allen Aktien | "index": aus MSCI World (EUNL.DE)
REGIME_INDEX = "EUNL.DE"
# Stopp-Filter (Analyse 2020–25), 0 = aus:
TREND_MIN = 0.0   # SMA 200 muss in 20 Tagen um mind. so viel steigen (z. B. 0.013 = 1,3 %)
RANGE_MAX = 0.0   # Spanne Hoch/Tief der letzten 60 Tage höchstens (z. B. 0.18 = 18 %)
BREAKEVEN = 0.0   # war der Posten so weit im Plus (z. B. 0.04), Stopp auf Einstand nachziehen

# Chat-Depot (Start laut Grok-Projekt depot.ts: Do 17.09.2026)
START_DAY = "2026-09-17"
CASH0 = 1000.0
START = [
    {"ticker": "BTC-USD", "qty": 0.044, "cost": 78105.0, "ccy": "EUR", "kind": "CRYPTO"},
    {"ticker": "TSLA", "qty": 6.0, "cost": 307.0, "ccy": "EUR", "kind": "AKTIE"},
    {"ticker": "WEN", "qty": 35.0, "cost": 18.44, "ccy": "USD", "kind": "AKTIE"},
    {"ticker": "EVT.DE", "qty": 25.0, "cost": 10.96, "ccy": "EUR", "kind": "AKTIE"},
    {"ticker": "9991.HK", "qty": 100.0, "cost": 8.23, "ccy": "HKD", "kind": "AKTIE"},
    {"ticker": "3988.HK", "qty": 1000.0, "cost": 2.95, "ccy": "HKD", "kind": "AKTIE"},
]

UNIVERSE = [
    ("VWCE.DE", "FTSE All-World", "ETF"),
    ("SXR8.DE", "S&P 500", "ETF"),
    ("EUNL.DE", "MSCI World", "ETF"),
    ("IWDA.AS", "MSCI World Acc", "ETF"),
    ("QQQ", "Nasdaq-100", "ETF"),
    ("SPY", "S&P 500 SPDR", "ETF"),
    ("VUAA.DE", "S&P 500 Acc", "ETF"),
    ("SXRV.DE", "Nasdaq-100 Acc", "ETF"),
    ("IUSN.DE", "MSCI World Small", "ETF"),
    ("IS3N.DE", "MSCI EM", "ETF"),
    ("VUSA.L", "S&P 500 Vanguard", "ETF"),
    ("AGGH.L", "Global Aggregate Bond", "ETF"),
    ("BTC-USD", "Bitcoin", "CRYPTO"),
    ("ETH-USD", "Ethereum", "CRYPTO"),
    ("SOL-USD", "Solana", "CRYPTO"),
    ("XRP-USD", "XRP", "CRYPTO"),
    ("BNB-USD", "BNB", "CRYPTO"),
    ("ADA-USD", "Cardano", "CRYPTO"),
    ("DOGE-USD", "Dogecoin", "CRYPTO"),
    ("AVAX-USD", "Avalanche", "CRYPTO"),
    ("DOT-USD", "Polkadot", "CRYPTO"),
    ("LINK-USD", "Chainlink", "CRYPTO"),
    ("LTC-USD", "Litecoin", "CRYPTO"),
    ("ATOM-USD", "Cosmos", "CRYPTO"),
    ("NEAR-USD", "NEAR", "CRYPTO"),
    ("UNI-USD", "Uniswap", "CRYPTO"),
    ("AAVE-USD", "Aave", "CRYPTO"),
    ("TON-USD", "Toncoin", "CRYPTO"),
    ("BCH-USD", "Bitcoin Cash", "CRYPTO"),
    ("APT-USD", "Aptos", "CRYPTO"),
    ("SUI-USD", "Sui", "CRYPTO"),
    ("TRX-USD", "TRON", "CRYPTO"),
    ("XLM-USD", "Stellar", "CRYPTO"),
]

NAME = {t: n for t, n, _b in UNIVERSE}
BOOK = {t: b for t, _n, b in UNIVERSE}
TICKERS = [t for t, _n, _b in UNIVERSE]
F24 = {}  # Yahoo-Ticker -> Freedom24-Ticker
LOT = {}  # Yahoo-Ticker -> Freedom24-Losgröße
CCY = {}  # Yahoo-Ticker -> Handelswährung laut Freedom24
LTP = {}  # Yahoo-Ticker -> letzter Freedom24-Kurs (nur für Pence-Prüfung)
PENCE = {}  # London: True, wenn Yahoo in Pence (GBp) liefert


# --- Freedom24-Universum -----------------------------------------------------
# Quelle: öffentliches Wertpapier-Verzeichnis https://freedom24.com/refbooks
# (EU, HKEX, ATHEX, BIST) und für die USA die NASDAQ-Symbolliste, geprüft über
# https://freedom24.com/securities/export (nur was Freedom24 kennt und handelt).

F24_URL = "https://freedom24.com"
UA = {"User-Agent": "Mozilla/5.0 (Schatzhauser)"}
F24_MAX_AGE = 7 * 86400
MARKETS = ("US", "EU", "HK", "GR", "TR")
STOCK_KINDS = (1, 2, 10)  # Stammaktie, Vorzug, Depositary Receipt

EU_SUFFIX = {
    "XETRA": ".DE", "IBIS": ".DE", "FWB": ".F", "MIL": ".MI", "BM": ".MC", "SIX": ".SW",
    "LSE": ".L", "LSEAIM": ".L", "LSEIOB": ".L", "LSEETF": ".L", "VSE": ".VI",
    "OMXH": ".HE", "SOMX": ".ST", "OSE": ".OL", "TMX": ".TO", "ATH": ".AT",
}
MIC_SUFFIX = {"XPAR": ".PA", "XAMS": ".AS", "XBRU": ".BR", "XLIS": ".LS", "XDUB": ".IR"}
ISIN_SUFFIX = {"FR": ".PA", "NL": ".AS", "BE": ".BR", "PT": ".LS", "IE": ".IR"}


def _rb_dates():
    import re

    r = requests.get(f"{F24_URL}/refbooks/", headers=UA, timeout=60)
    r.raise_for_status()
    return sorted(set(re.findall(r'href="(\d{4}-\d{2}-\d{2})/"', r.text)), reverse=True)


def _rb_newest(name, dates):
    """Jüngster Tag, an dem Freedom24 die Datei <name>.json.zip veröffentlicht hat."""

    def ok(d):
        try:
            u = f"{F24_URL}/refbooks/{d}/{name}.json.zip"
            return requests.head(u, headers=UA, timeout=20).status_code == 200
        except Exception:
            return False

    for k in range(0, min(len(dates), 500), 32):
        chunk = dates[k : k + 32]
        with ThreadPoolExecutor(16) as ex:
            res = list(ex.map(ok, chunk))
        for d, good in zip(chunk, res):
            if good:
                return d
    return None


def _rb_load(name, d):
    r = requests.get(f"{F24_URL}/refbooks/{d}/{name}.json.zip", headers=UA, timeout=300)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return json.loads(z.read(z.namelist()[0]))


def _is_stock(x):
    return x.get("instr_type_c") == 1 and x.get("istrade") == 1 and x.get("instr_kind_c") in STOCK_KINDS


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) and f > 0 else None
    except (TypeError, ValueError):
        return None


def _yf_code(code):
    return (code or "").strip().rstrip(".").replace("/", "-").replace(" ", "-")


def _row(yf_t, f24, name, book, lot, ccy, ltp=None):
    return {"yf": yf_t, "f24": f24, "name": name or f24, "book": book,
            "lot": int(_num(lot) or 1), "ccy": ccy, "ltp": _num(ltp)}


def _attrs(x):
    a = x.get("attributes")
    if isinstance(a, dict):
        return a
    try:
        return json.loads(a or "{}")
    except (TypeError, ValueError):
        return {}


def _map_yahoo(m, x):
    """Freedom24-Verzeichniszeile -> Yahoo-Ticker (oder None)."""
    if m in ("TR", "GR"):
        code = _yf_code(str(x.get("ticker", "")).split(".")[0])  # THYAO.TR / SBLK.GR
    else:
        code = _yf_code(x.get("exchange_ticker") or x.get("code_nm") or str(x.get("ticker", "")).split(".")[0])
    if not code or "." in code:
        return None  # Bezugsrechte, Sonderlinien (…RTS, …XTR)
    if m == "EU":
        sub = x.get("codesub_nm") or ""
        if sub == "EURONEXT":
            suf = MIC_SUFFIX.get(_attrs(x).get("GTN_ExDestination")) or ISIN_SUFFIX.get(str(x.get("issue_nb") or "")[:2])
        else:
            suf = EU_SUFFIX.get(sub)
        return code + suf if suf else None
    if m == "HK":
        return code.zfill(4) + ".HK" if code.isdigit() and len(code) <= 4 else None
    if m == "GR":
        return code + ".AT"
    if m == "TR":
        return code + ".IS"
    return None


def _cand_us():
    """Alle US-Aktien (NASDAQ/NYSE/AMEX) ohne ETFs und Test-Titel."""
    out = []
    lists = [
        ("https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt", 6, 3),
        ("https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt", 4, 6),
    ]
    for url, etf_i, test_i in lists:
        txt = requests.get(url, headers=UA, timeout=60).text.splitlines()
        for line in txt[1:]:
            p = line.split("|")
            if len(p) < 7 or line.startswith("File Creation"):
                continue
            s = p[0].strip()
            if not s or "$" in s or p[etf_i] == "Y" or p[test_i] == "Y":
                continue
            out.append((s + ".US", s.replace(".", "-"), p[1].strip(), "US", None))
    return out


def _export(tickers, fields="c ltr x_curr x_lot ltp vol name kind type x_istrade issue_nb"):
    """Freedom24 /securities/export: nur Ticker, die Freedom24 kennt, kommen zurück."""
    chunks = [tickers[i : i + 100] for i in range(0, len(tickers), 100)]

    def one(ch):
        for _ in range(3):
            try:
                r = requests.get(f"{F24_URL}/securities/export", params={"tickers": " ".join(ch), "params": fields},
                                 headers=UA, timeout=60)
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass
            time.sleep(2)
        return []

    out = {}
    with ThreadPoolExecutor(6) as ex:
        for res in ex.map(one, chunks):
            for x in res if isinstance(res, list) else []:
                if isinstance(x, dict) and x.get("c"):
                    out[x["c"]] = x
    return out


def build_f24(markets=MARKETS):
    """Liste aller bei Freedom24 handelbaren Aktien bauen und speichern."""
    print("Freedom24-Universum wird aufgebaut …", flush=True)
    rb_name = {"EU": "EU", "HK": "HKEX", "GR": "ATHEX", "TR": "BIST"}
    cand, dates = [], None
    for m in markets:
        try:
            if m == "US":
                part = _cand_us()
                print(f"  US: {len(part)} Kandidaten (NASDAQ-Symbolliste)")
            else:
                dates = dates or _rb_dates()
                d = _rb_newest(rb_name[m], dates)
                if not d:
                    print(f"  {m}: kein öffentliches Verzeichnis gefunden")
                    continue
                part = []
                for x in _rb_load(rb_name[m], d):
                    y = _map_yahoo(m, x) if _is_stock(x) else None
                    if y:
                        part.append((x["ticker"], y, x.get("name"), m, x.get("issue_nb")))
                print(f"  {m}: {len(part)} Kandidaten (Verzeichnis vom {d})")
            cand += part
        except Exception as e:
            print(f"  {m}: Fehler {e}")
    if not cand:
        return None
    print(f"  prüfe {len(cand)} Titel bei Freedom24 …", flush=True)
    ex = _export(list(dict.fromkeys(c[0] for c in cand)))
    rows = {}
    for f24, y, name, m, isin in cand:
        e = ex.get(f24)
        if not e or int(e.get("x_istrade", 1) or 0) != 1 or e.get("kind") not in STOCK_KINDS:
            continue
        if m == "HK" and e.get("x_curr") != "HKD":
            continue
        if y in rows:
            continue
        r = _row(y, f24, e.get("name") or name, m, e.get("x_lot"), e.get("x_curr"), e.get("ltp"))
        r["isin"] = e.get("issue_nb") or isin
        rows[y] = r
    data = {"at": time.time(), "markets": list(markets), "rows": list(rows.values())}
    F24FILE.write_text(json.dumps(data, ensure_ascii=False))
    cnt = {}
    for r in rows.values():
        cnt[r["book"]] = cnt.get(r["book"], 0) + 1
    print(f"  Freedom24 handelbar: {len(rows)} Aktien {cnt} -> {F24FILE.name}")
    return data


def load_universe(rebuild=False, markets=MARKETS, quick=False):
    """Freedom24-Aktien laden (höchstens 7 Tage alt), Tabellen füllen, Yahoo-Ticker zurückgeben.

    quick=True (Depot-Befehle): vorhandene Liste nehmen, egal wie alt – nie neu aufbauen.
    """
    data = None
    if F24FILE.exists() and not rebuild:
        try:
            data = json.loads(F24FILE.read_text())
            old = time.time() - data.get("at", 0) > F24_MAX_AGE
            if not quick and (old or not set(markets) <= set(data.get("markets", []))):
                data = None
        except Exception:
            data = None
    if data is None and quick:
        print("  (noch keine Freedom24-Liste – einmal normal scannen, dann werden Lose/Kürzel erkannt)")
        return []
    if data is None:
        data = build_f24(markets)
    if data is None and F24FILE.exists():
        print("  Neuaufbau fehlgeschlagen, nehme alte Liste.")
        data = json.loads(F24FILE.read_text())
    out = []
    for r in (data or {}).get("rows", []):
        y = r["yf"]
        if y in BOOK and BOOK[y] in ("ETF", "CRYPTO"):  # ETF-/Krypto-Liste hat Vorrang
            continue
        NAME[y], BOOK[y], F24[y] = r["name"], r["book"], r["f24"]
        LOT[y], CCY[y] = int(r.get("lot") or 1), r.get("ccy")
        if r.get("ltp"):
            LTP[y] = r["ltp"]
        if r["book"] in markets:
            out.append(y)
    return out


# --- Zahlen -----------------------------------------------------------------

def sma(c, n):
    out = np.full(len(c), np.nan)
    if len(c) < n:
        return out
    s = np.cumsum(c)
    out[n - 1 :] = (s[n - 1 :] - np.concatenate(([0], s[:-n]))) / n
    return out


def rsi_arr(c, n=14):
    out = np.full(len(c), np.nan)
    if len(c) < n + 1:
        return out
    d = np.diff(c, prepend=c[0])
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    au, ad = up[1 : n + 1].mean(), dn[1 : n + 1].mean()
    out[n] = 100 if ad == 0 else 100 - 100 / (1 + au / ad)
    for i in range(n + 1, len(c)):
        au = (au * (n - 1) + up[i]) / n
        ad = (ad * (n - 1) + dn[i]) / n
        out[i] = 100 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


def atr_arr(c, n=14):
    d = np.abs(np.diff(c, prepend=c[0]))
    out = np.full(len(c), np.nan)
    if len(c) < n:
        return out
    s = d[1 : n + 1].sum()
    out[n] = s / n
    for i in range(n + 1, len(c)):
        s = s - s / n + d[i]
        out[i] = s / n
    return out


def er_arr(c, n=10):
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    ch = np.abs(c[n:] - c[:-n])
    d = np.abs(np.diff(c))
    cs = np.concatenate(([0.0], np.cumsum(d)))
    vol = cs[n:] - cs[:-n]
    out[n:] = np.where(vol > 0, ch / np.where(vol > 0, vol, 1.0), 0.0)
    return out


def hl(c, n):
    s = pd.Series(c)
    return s.rolling(n).max().to_numpy(), s.rolling(n).min().to_numpy()


def attach_ichi(p):
    c = p.c
    h9, l9 = hl(c, 9)
    h26, l26 = hl(c, 26)
    h52, l52 = hl(c, 52)
    p.tenkan = (h9 + l9) / 2
    p.kijun = (h26 + l26) / 2
    span_a = (p.tenkan + p.kijun) / 2
    span_b = (h52 + l52) / 2
    n = len(c)
    p.cloud_hi = np.full(n, np.nan)
    p.cloud_lo = np.full(n, np.nan)
    for i in range(26, n):
        a, b = span_a[i - 26], span_b[i - 26]
        if np.isfinite(a) and np.isfinite(b):
            p.cloud_hi[i] = max(a, b)
            p.cloud_lo[i] = min(a, b)
    return p


class Pack:
    def __init__(self, name, s: pd.Series):
        s = s.sort_index().dropna()
        s = s[s > 0]  # Nullkurse -> sonst Division durch 0 (inf) im P-Modell
        self.name = name
        self.dates = s.index.values.astype("datetime64[D]")
        self.c = s.values.astype(float)
        self.s200 = sma(self.c, 200)
        self.atr14 = atr_arr(self.c, 14)
        self.rsi = rsi_arr(self.c)
        self.e = er_arr(self.c, 10)
        attach_ichi(self)
        self.imap = {np.datetime64(d, "D"): i for i, d in enumerate(self.dates)}

    def i_on(self, day):
        return self.imap.get(np.datetime64(day, "D"))

    @property
    def last_i(self):
        return len(self.c) - 1


def ichi_buy(p, i):
    if i < 210:
        return False
    if not np.isfinite(p.s200[i]) or p.c[i] <= p.s200[i]:
        return False
    if not (np.isfinite(p.cloud_hi[i]) and np.isfinite(p.cloud_lo[i]) and p.c[i] > 0):
        return False
    if p.c[i] <= p.cloud_hi[i]:
        return False
    if (p.cloud_hi[i] - p.cloud_lo[i]) / p.c[i] < WHIP:
        return False
    crossed = p.tenkan[i - 2] <= p.kijun[i - 2] and p.tenkan[i - 1] > p.kijun[i - 1]
    holds = p.tenkan[i] > p.kijun[i] and p.tenkan[i - 1] > p.kijun[i - 1] and p.c[i - 1] > p.cloud_hi[i - 1]
    return bool(crossed and holds)


def ichi_sell(p, i):
    tk = p.tenkan[i - 1] >= p.kijun[i - 1] and p.tenkan[i] < p.kijun[i]
    under = np.isfinite(p.cloud_lo[i]) and p.c[i] < p.cloud_lo[i]
    return bool(tk or under)


def regime(e):
    if not np.isfinite(e):
        return "range"
    if e >= 0.32:
        return "trend"
    if e < 0.15:
        return "squeeze"
    return "range"


def feat(p, i):
    if i < 20 or not np.isfinite(p.s200[i]):
        return None
    look = min(252, i)
    mom = p.c[i] / p.c[i - look] - 1 if look >= 60 else 0.0
    reg = regime(p.e[i])
    return np.array(
        [
            1.0,
            1.0 if reg == "trend" else 0.0,
            1.0 if reg == "range" else 0.0,
            0.0,
            0.0,
            1.0,
            p.c[i] / p.s200[i] - 1,
            mom,
            p.rsi[i] if np.isfinite(p.rsi[i]) else 50.0,
            p.atr14[i] / p.c[i] if np.isfinite(p.atr14[i]) and p.c[i] > 0 else 0.02,
            p.c[i] / p.c[i - 5] - 1 if i >= 5 else 0.0,
            p.e[i] if np.isfinite(p.e[i]) else 0.0,
        ],
        dtype=float,
    )


def fit_logit(X, y, w, steps=80, l2=0.03):
    mu = X.mean(0)
    sd = X.std(0)
    sd[sd < 1e-8] = 1.0
    Z = (X - mu) / sd
    th = np.zeros(Z.shape[1])
    wsum = max(float(w.sum()), 1e-9)
    lr = 0.35
    for _ in range(steps):
        z = Z @ th
        p = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        e = (p - y) * w
        g = (Z.T @ e) / wsum + l2 * th
        th -= lr * g
    return th, mu, sd


def predict(th, mu, sd, x):
    z = float(((x - mu) / sd) @ th)
    return float(1 / (1 + math.exp(-max(-30, min(30, z)))))


def uniq_new(pos, ages_days, typ=TYP_HOLD):
    remain = [max(0, typ - a) for a in ages_days]
    s = 0.0
    for d in range(typ):
        conc = 1 + sum(1 for r in remain if r > d)
        s += 1 / conc
    return s / typ


def lot_of(t):
    """Handelslos laut Freedom24 (x_lot). Hongkong z. B. 100, 500, 1000, 2000 …; London teils 100.

    Kauf/Verkauf nur in ganzen Vielfachen davon. Unbekanntes HK-Los: 100 (Warnung beim Eintragen).
    """
    lot = LOT.get(t)
    if lot and lot >= 1:
        return int(lot)
    return 100 if t.upper().endswith(".HK") else 1


def hk_lot(t):
    """Kompatibilität: Hongkong-Los als Liste, sonst None."""
    return [lot_of(t)] if t.upper().endswith(".HK") else None


def kind_of(t):
    if t.endswith("-USD") or BOOK.get(t) == "CRYPTO":
        return "CRYPTO"
    if BOOK.get(t) == "ETF":
        return "ETF"
    return "AKTIE"


def quote_ccy(t):
    """Währung des Yahoo-Kurses. London notiert bei Yahoo meist in Pence (GBp)."""
    u = t.upper()
    c = CCY.get(t)
    if c and u.endswith(".L") and c == "GBP":
        return "GBX" if PENCE.get(t, True) else "GBP"
    if c:
        return c
    if u.endswith(".HK"):
        return "HKD"
    if u.endswith("-USD") or "." not in u:
        return "USD"
    if u.endswith(".T"):
        return "JPY"
    if u.endswith(".ST"):
        return "SEK"
    if u.endswith(".OL"):
        return "NOK"
    if u.endswith(".TA"):
        return "ILS"
    if u.endswith(".L"):
        return "GBX"
    if u.endswith(".IS"):
        return "TRY"
    if u.endswith(".SW"):
        return "CHF"
    if u.endswith(".CO"):
        return "DKK"
    if u.endswith(".WA"):
        return "PLN"
    return "EUR"


def f24_ccy(t):
    """Währung, in der Freedom24 abrechnet (GBX -> GBP)."""
    c = quote_ccy(t)
    return "GBP" if c == "GBX" else c


def fee_buy(t, qty, px):
    k = kind_of(t)
    if k == "CRYPTO":
        return qty * px * 0.002
    if t.upper().endswith(".HK"):
        return qty * px * 0.0025 + 10.0  # HKD, caller converts
    return FEE + FEE_PS * qty


def fees_eur(t, qty, px, usd):
    """Freedom24-Kosten einer Order in EUR: Gebühr + Wechselgebühr (0,15 %, nicht bei EUR).

    Aktien: 2 € + 0,02 €/Stück. Hongkong: 0,25 % + 10 HKD. Krypto: 0,2 %.
    """
    gross = (to_eur(qty * px, quote_ccy(t), usd) or qty * px)
    if kind_of(t) == "CRYPTO":
        return gross * 0.002
    if t.upper().endswith(".HK"):
        fee = gross * 0.0025 + (to_eur(10.0, "HKD", usd) or 1.2)
    else:
        fee = FEE + FEE_PS * qty
    return fee + (gross * FX if f24_ccy(t) != "EUR" else 0.0)


def buy_cost_eur(t, qty, px, usd):
    """Was ein Kauf in EUR kostet (Kurswert + alle Gebühren)."""
    return (to_eur(qty * px, quote_ccy(t), usd) or qty * px) + fees_eur(t, qty, px, usd)


def size_buy(t, px, cap, cash, usd):
    """Stückzahl für einen Kauf: größtes Vielfaches des Freedom24-Loses, das in cap und Kasse passt.

    Hongkong-Titel mit Los 1000 kosten mindestens 1000 × Kurs – passt das nicht, gibt es 0.
    """
    budget = min(cap, cash)
    if px <= 0 or budget <= 0:
        return 0, 0.0
    if kind_of(t) == "CRYPTO":
        one_e = to_eur(1.0, quote_ccy(t), usd) or 1.0
        q = math.floor(budget / (px * one_e * 1.002) * 1e6) / 1e6
        return (q, buy_cost_eur(t, q, px, usd)) if q > 0 else (0, 0.0)
    lot = lot_of(t)
    one_e = to_eur(1.0, quote_ccy(t), usd) or 1.0
    n = int(max(0.0, budget - FEE) / (lot * px * one_e * 1.01)) + 1  # grobe Obergrenze
    while n > 0 and buy_cost_eur(t, n * lot, px, usd) > budget:
        n -= 1
    if n <= 0:
        return 0, 0.0
    need = buy_cost_eur(t, n * lot, px, usd)
    if need < MIN_ORDER:
        return 0, 0.0  # zu kleiner Posten: Gebühren fressen den Gewinn
    return n * lot, need


def roundtrip_eur(qty, px):
    return 2 * (FEE + FEE_PS * qty + FX * qty * px)


def pays10(qty, px, cost):
    return (px - cost) * qty - roundtrip_eur(qty, px) >= 10


# --- Kurse / FX --------------------------------------------------------------

def _closes(df, chunk):
    """yfinance-Tabelle -> {Ticker: Schlusskurse}."""
    out = {}
    if df is None or df.empty:
        return out
    cols = df.columns
    if not isinstance(cols, pd.MultiIndex):
        if len(chunk) == 1 and "Close" in cols:
            out[chunk[0]] = df["Close"].dropna().astype(float)
        return out
    for t in cols.get_level_values(0).unique():
        if (t, "Close") in cols:
            s = df[(t, "Close")].dropna()
            if len(s):
                out[str(t)] = s.astype(float)
    return out


def _yf_batch(chunk, **kw):
    for k in range(3):
        try:
            return _closes(yf.download(chunk, auto_adjust=True, progress=False, threads=True,
                                       group_by="ticker", **kw), chunk)
        except Exception as e:
            print("  Yahoo-Fehler", e, "– neuer Versuch", flush=True)
            time.sleep(3 * (k + 1))
    return {}


def download(syms, years=5):
    """Kurse von Yahoo. Cache wird täglich nur um die fehlenden Tage ergänzt.

    Titel ohne brauchbare Historie (< 200 Tage) werden 7 Tage lang nicht erneut
    versucht (Liste in __dead__ im Cache).
    """
    raw, dead = {}, {}
    if CACHE.exists():
        try:
            cached = pd.read_pickle(CACHE)
            if isinstance(cached, dict):
                dead = dict(cached.pop("__dead__", {}) or {})
                raw = cached
        except Exception:
            raw = {}
    now = time.time()
    fresh = CACHE.exists() and now - CACHE.stat().st_mtime < 20 * 3600
    dead = {t: at for t, at in dead.items() if now - at < 7 * 86400}
    start = (pd.Timestamp.today() - pd.DateOffset(years=years + 1)).strftime("%Y-%m-%d")
    need = [s for s in syms if (s not in raw or len(raw[s]) < 200) and s not in dead]
    old = [] if fresh else [s for s in syms if s in raw and len(raw[s]) >= 200]
    print(f"Kurs-Cache {len(raw)} Titel · neu laden {len(need)} · auffrischen {len(old)}", flush=True)

    step = 100
    for i in range(0, len(need), step):
        chunk = need[i : i + step]
        print(f"  lade {i + len(chunk)}/{len(need)} …", flush=True)
        got = _yf_batch(chunk, start=start)
        for t in chunk:
            s = got.get(t)
            if s is not None and len(s) > 200:
                raw[t] = s
            else:
                dead[t] = now
        time.sleep(0.5)

    if old:
        age_d = int((now - CACHE.stat().st_mtime) // 86400)
        since = (pd.Timestamp.today() - pd.Timedelta(days=age_d + 10)).strftime("%Y-%m-%d")
        for i in range(0, len(old), 200):
            chunk = old[i : i + 200]
            print(f"  frische auf {i + len(chunk)}/{len(old)} …", flush=True)
            got = _yf_batch(chunk, start=since)
            for t, s in got.items():
                if t in raw and len(s):
                    raw[t] = pd.concat([raw[t][raw[t].index < s.index[0]], s]).sort_index()
            time.sleep(0.5)
    try:
        pd.to_pickle({**raw, "__dead__": dead}, CACHE)
    except Exception:
        pass
    return raw


FXFILE = HERE / "schatzhauser_fx.json"


def fx_usd():
    """USD je 1 Einheit (EUR = USD für 1 EUR). Cache 1 Stunde; sonst Yahoo, notfalls feste Werte."""
    try:
        c = json.loads(FXFILE.read_text())
        if time.time() - c.get("at", 0) < 3600:
            return c["fx"]
    except Exception:
        pass
    out = _fx_live()
    if out.get("_live"):
        try:
            FXFILE.write_text(json.dumps({"at": time.time(), "fx": out}))
        except Exception:
            pass
    else:
        print("  Achtung: keine aktuellen Wechselkurse von Yahoo – nehme feste Näherungswerte.")
    return out


def _fx_live():
    """USD je 1 Einheit. EUR=wie viele USD für 1 EUR."""
    pairs = {"EUR": "EURUSD=X", "HKD": "HKDUSD=X", "GBP": "GBPUSD=X", "JPY": "JPYUSD=X", "SEK": "SEKUSD=X",
             "NOK": "NOKUSD=X", "ILS": "ILSUSD=X", "CHF": "CHFUSD=X", "DKK": "DKKUSD=X", "PLN": "PLNUSD=X",
             "TRY": "TRYUSD=X", "CAD": "CADUSD=X", "CNY": "CNYUSD=X"}
    out = {"USD": 1.0, "EUR": 1.08, "HKD": 0.128, "GBP": 1.27, "JPY": 0.0067, "SEK": 0.095, "NOK": 0.095,
           "ILS": 0.27, "CHF": 1.13, "DKK": 0.145, "PLN": 0.25, "TRY": 0.025, "CAD": 0.73, "CNY": 0.14}
    try:
        df = yf.download(list(pairs.values()), period="5d", auto_adjust=True, progress=False, threads=True, group_by="ticker")
        for ccy, pair in pairs.items():
            try:
                if isinstance(df.columns, pd.MultiIndex):
                    s = df[(pair, "Close")].dropna()
                else:
                    s = df["Close"].dropna()
                if len(s):
                    out[ccy] = float(s.iloc[-1])
                    if ccy == "EUR":
                        out["_live"] = True
            except Exception:
                continue
    except Exception:
        pass
    out["GBX"] = out["GBP"] / 100  # Pence
    return out


def to_eur(amount, ccy, usd):
    if ccy == "EUR":
        return amount
    u = usd.get(ccy)
    e = usd.get("EUR")
    if not u or not e:
        return None
    return amount * u / e


# --- Bewertung (Späher) ------------------------------------------------------
# Portiert aus Grok-Webprojekt src/lib/scan.ts (grade/scout). Bilanz-Punkte aus
# Yahoo; nur für Kaufkandidaten und Depotposten geladen (sonst zu langsam).

SIG_STRETCH, SIG_PB_LO, SIG_PB_HI = 0.25, 0.0, 0.12
SIG_NEAR_HIGH, SIG_NEAR_STRETCH = 0.90, 0.18
SIG_EARN_DAYS, SIG_Q_MIN, SIG_Q_NEED = 5, 0.6, 4
FUND_TTL = 3 * 86400
FUNDFILE = HERE / "schatzhauser_fund.json"


def _fund_one(t):
    try:
        tk = yf.Ticker(t)
        i = tk.info or {}
        ni_up = None
        try:
            inc = tk.income_stmt
            if inc is not None and "Net Income" in inc.index:
                v = inc.loc["Net Income"].dropna().tolist()
                if len(v) >= 2:
                    ni_up = bool(v[0] > v[1])
        except Exception:
            pass
        earn = i.get("earningsTimestampStart") or i.get("earningsTimestamp")
        return {"roe": i.get("returnOnEquity"), "margins": i.get("profitMargins"),
                "revGrowth": i.get("revenueGrowth"), "current": i.get("currentRatio"),
                "de": i.get("debtToEquity"), "ocf": i.get("operatingCashflow"),
                "niUp": ni_up, "earningsAt": earn, "ccy": i.get("currency"), "at": time.time()}
    except Exception as e:
        return {"error": str(e)[:80], "at": time.time()}


def fundamentals(tickers):
    """Bilanzzahlen (Cache 3 Tage) für die angegebenen Titel."""
    try:
        cache = json.loads(FUNDFILE.read_text()) if FUNDFILE.exists() else {}
    except Exception:
        cache = {}
    def stale(t):
        c = cache.get(t, {})
        ttl = 3600 if c.get("error") else FUND_TTL  # Fehler (z. B. Yahoo-Sperre) nur 1 h merken
        return time.time() - c.get("at", 0) > ttl

    need = [t for t in tickers if kind_of(t) == "AKTIE" and stale(t)]
    if need:
        print(f"Bilanzen laden: {len(need)} …", flush=True)
        for t in need:
            f = _fund_one(t)
            if "Too Many Requests" in f.get("error", ""):
                time.sleep(20)  # Yahoo drosselt: kurz warten, einmal neu
                f = _fund_one(t)
            cache[t] = f
            time.sleep(0.4)
        FUNDFILE.write_text(json.dumps(cache))
        bad = sum(1 for t in need if cache[t].get("error"))
        if bad:
            print(f"  {bad} Bilanzen nicht verfügbar (Yahoo) – Bewertung dort ohne Punkte.")
    return {t: cache.get(t) for t in tickers}


def grade(fund, is_etf):
    checks, pts, mx = [], 0, 0
    if is_etf or not fund or fund.get("error"):
        return 0, 0, ["ETF: keine Firmenbilanz"] if is_etf else ["Keine Bilanzzahlen"]

    def mark(ok, label):
        nonlocal pts, mx
        if ok is None:
            return
        mx += 1
        pts += 1 if ok else 0
        checks.append(label if ok else "nicht: " + label)

    g = fund.get
    mark(None if g("margins") is None else g("margins") > 0, "Marge positiv")
    mark(None if g("roe") is None else g("roe") >= 0.08, "ROE ≥ 8 %")
    mark(None if g("current") is None else g("current") >= 1, "Liquidität ≥ 1")
    mark(None if g("de") is None else g("de") < 250, "Verschuldung maßvoll")
    mark(None if g("ocf") is None else g("ocf") > 0, "operativer Cashflow positiv")
    mark(None if g("revGrowth") is None else g("revGrowth") > 0, "Umsatz wächst")
    mark(g("niUp"), "Jahresüberschuss höher als im Vorjahr")
    return pts, mx, checks


def scout(p, fund, book):
    """Rang + Empfehlung wie im Web-Späher. Liefert dict."""
    is_etf = book == "ETF" or kind_of(p.name) != "AKTIE"
    pts, mx, checks = grade(fund, is_etf)
    out = {"pts": pts, "max": mx, "checks": checks, "rank": "KEINE DATEN", "advise": "UNSICHER", "buy": "KEIN", "note": ""}
    c, i = p.c, p.last_i
    if len(c) < 200 or not np.isfinite(p.s200[i]):
        out["note"] = "Zu wenig Kursgeschichte."
        return out
    px, s200 = c[i], p.s200[i]
    s50 = float(np.mean(c[-50:]))
    look = min(252, i)
    mom12 = px / c[i - look] - 1
    ext = px / s200 - 1
    hi52 = px / max(c[-min(252, len(c)):])
    earn = (fund or {}).get("earningsAt")
    days_earn = round((earn - time.time()) / 86400) if earn else None
    out.update(mom12=mom12, ext=ext, days_earn=days_earn)
    q_ok = mx >= SIG_Q_NEED and pts / mx >= SIG_Q_MIN

    def r(rank, advise, buy, note):
        out.update(rank=rank, advise=advise, buy=buy, note=note)
        return out

    if px < s200 or mom12 < 0:
        return r("UNTER SCHNITT", "MEIDEN", "KEIN", "Unter SMA 200 oder Jahresmomentum negativ.")
    if ext > SIG_STRETCH:
        return r("ÜBERDEHNT", "WARTEN", "RÜCKSETZER", f"Mehr als {SIG_STRETCH:.0%} über SMA 200. Nicht jagen.")
    if days_earn is not None and 0 <= days_earn <= SIG_EARN_DAYS:
        return r("TERMIN", "WARTEN", "KEIN", f"Bilanztermin in {days_earn} Tag(en). Erst Zahlen abwarten.")
    if not is_etf and mx >= SIG_Q_NEED and not q_ok:
        return r("BILANZ SCHWACH", "MEIDEN", "KEIN", f"Trend steht, Bilanz nicht: {pts} von {mx} Punkten.")
    zone = s50 > s200 and (SIG_PB_LO <= ext <= SIG_PB_HI or (hi52 >= SIG_NEAR_HIGH and ext <= SIG_NEAR_STRETCH))
    if q_ok or is_etf:
        if zone:
            return r("LANG", "NEIGUNG JA", "KAUFZONE", f"Trend + Bilanz {pts}/{mx}, Einstieg in Kaufzone.")
        return r("LANG", "WARTEN", "RÜCKSETZER", "Trend und Bilanz tragen, Einstieg zu teuer.")
    return r("TREND", "NUR TREND", "KEIN", "Trend ja, Bilanz unvollständig — vorsichtig werten.")


# --- Markt / Buch ------------------------------------------------------------

def market(packs):
    if REGIME == "index":
        p = index_pack()
        if p is not None:
            return _regime_index(p, p.last_i)
    n = a = m = 0
    for p in packs.values():
        i = p.last_i
        if i < 210:
            continue
        n += 1
        if np.isfinite(p.s200[i]) and p.c[i] > p.s200[i]:
            a += 1
        if i >= 252 and p.c[i] > p.c[i - 252]:
            m += 1
    if n < 30:
        return 0.2, "neutral"
    above, mom = a / n, m / n
    if above >= 0.55 and mom >= 0.55:
        return 0.33, "bull"
    if above <= 0.4 and mom <= 0.45:
        return 0.1, "bear"
    return 0.2, "neutral"


def empty_state():
    return {
        "ver": VER,
        "cash": CASH0,
        "holdings": [dict(h) for h in START],
        "fills": [],
        "cool": {},
        "scars": {},
        "open_meta": {},
    }


def load_state():
    if not STATE.exists():
        return empty_state()
    try:
        s = json.loads(STATE.read_text())
        s.setdefault("holdings", [])
        s.setdefault("fills", [])
        s.setdefault("cool", {})
        s.setdefault("scars", {})
        s.setdefault("open_meta", {})
        s.setdefault("cash", CASH0)
        return s
    except Exception:
        return empty_state()


def save_state(s):
    STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False, default=str))


def train_p(packs):
    raw = []
    today = None
    for p in packs.values():
        i0 = 210
        on = False
        cost = peak = 0.0
        entry = 0
        for i in range(i0, len(p.c) - 1):
            px = p.c[i]
            if on:
                peak = max(peak, px)
                hold = i - entry
                mfe = peak / cost - 1
                trough = min(getattr(p, "_tr", cost), px)
                p._tr = trough
                mae = trough / cost - 1
                hit = None
                if hold >= D5 and mae <= D5_MAE and mfe < D5_MFE:
                    hit = "KRATZER"
                elif px <= cost * (1 - STOP):
                    hit = "STOP"
                elif ichi_sell(p, i):
                    if pays10(1, px, cost) or (hold >= B_HOLD and mfe >= B_MFE and px > cost):
                        hit = "REGEL"
                if not hit:
                    continue
                y = 1 if (px / cost - 1 - 0.004) > 0 and hit != "STOP" else 0
                x = feat(p, entry)
                if x is not None and np.all(np.isfinite(x)):
                    d0 = int(p.dates[entry].astype("datetime64[D]").astype(int))
                    d1 = int(p.dates[i].astype("datetime64[D]").astype(int))
                    raw.append({"x": x, "y": y, "d0": d0, "d1": d1})
                    today = max(today or d1, d1)
                on = False
                p._tr = None
            elif ichi_buy(p, i):
                on, cost, peak, entry = True, px, px, i
                p._tr = px
    if len(raw) < 80:
        print("P-Tor: zu wenig Trades", len(raw))
        return None
    from collections import defaultdict

    load = defaultdict(int)
    weeks = []
    for t in raw:
        w0, w1 = t["d0"] // 7, t["d1"] // 7
        weeks.append((w0, w1))
        for w in range(w0, w1 + 1):
            load[w] += 1
    wts = np.ones(len(raw))
    for i, t in enumerate(raw):
        w0, w1 = weeks[i]
        span = max(1, w1 - w0 + 1)
        u = sum(1 / load[w] for w in range(w0, w1 + 1)) / span
        age = max(0, (today or t["d1"]) - t["d1"])
        wts[i] = u * (DECAY ** age)
    X = np.vstack([t["x"] for t in raw])
    y = np.array([t["y"] for t in raw], dtype=float)
    th, mu, sd = fit_logit(X, y, wts)
    hit = 0
    for i in range(len(y)):
        pr = predict(th, mu, sd, X[i])
        if (pr >= 0.5 and y[i] == 1) or (pr < 0.5 and y[i] == 0):
            hit += 1
    if not all(np.all(np.isfinite(a)) for a in (th, mu, sd)):
        print("P-Tor: Training ergab ungültige Werte (NaN/inf) – altes Modell bleibt.")
        return None
    model = {"th": th.tolist(), "mu": mu.tolist(), "sd": sd.tolist(), "n": len(y), "hit": hit / len(y)}
    PFILE.write_text(json.dumps(model))
    print(f"P-Tor {len(y)} Trades, Treffer {model['hit']*100:.0f} %")
    return model


def load_p():
    if not PFILE.exists():
        return None
    try:
        m = json.loads(PFILE.read_text())
        out = {
            "th": np.array(m["th"], dtype=float),
            "mu": np.array(m["mu"], dtype=float),
            "sd": np.array(m["sd"], dtype=float),
            "n": m.get("n"),
            "hit": m.get("hit"),
        }
        if not all(np.all(np.isfinite(out[k])) for k in ("th", "mu", "sd")):
            print("P-Modell defekt (NaN) – wird neu gelernt.")
            return None
        return out
    except Exception:
        return None


# --- Backtest ----------------------------------------------------------------
# Spielt die Regeln Tag für Tag über die Vergangenheit ab – mit denselben Kauf-/
# Verkaufsregeln, Deckel, Uniqueness, Freedom24-Losen und Gebühren wie der Scan.
# Grenzen (bewusst): Kurse = Schlusskurs des Signaltags, Wechselkurse = heute,
# keine Bilanz-Bewertung (Yahoo liefert keine historischen Bilanzen), und das
# Universum ist die heutige Freedom24-Liste (Survivorship-Bias: Pleitefirmen fehlen).


def parse_period(spec):
    """'2024' | '2024-03' | '2022:2024' | '2023-06:2024-02' | '3y' | '6m' -> (start, ende) als date."""
    import calendar
    from datetime import date

    spec = (spec or "1y").strip().lower()
    today = datetime.now().date()

    def first(s):
        p = [int(x) for x in s.split("-")]
        return date(p[0], p[1] if len(p) > 1 else 1, 1)

    def last(s):
        p = [int(x) for x in s.split("-")]
        if len(p) == 1:
            return date(p[0], 12, 31)
        return date(p[0], p[1], calendar.monthrange(p[0], p[1])[1])

    if spec[-1] in "ym" and spec[:-1].isdigit():
        n = int(spec[:-1])
        off = pd.DateOffset(years=n) if spec[-1] == "y" else pd.DateOffset(months=n)
        return (pd.Timestamp(today) - off).date(), today
    if ":" in spec:
        a, b = spec.split(":", 1)
        return first(a), min(last(b), today)
    return first(spec), min(last(spec), today)


def _bt_train(packs, until):
    """P-Modell nur mit Trades, die vor dem Teststart abgeschlossen waren (kein Blick in die Zukunft)."""
    cut = np.datetime64(until, "D")
    sub = {}
    for t, p in packs.items():
        n = int(np.searchsorted(p.dates, cut))
        if n > 260:
            q = Pack.__new__(Pack)
            q.name, q.dates, q.c = p.name, p.dates[:n], p.c[:n]
            for a in ("s200", "atr14", "rsi", "e", "tenkan", "kijun", "cloud_hi", "cloud_lo"):
                setattr(q, a, getattr(p, a)[:n])
            sub[t] = q
    keep = PFILE.read_text() if PFILE.exists() else None
    try:
        m = train_p(sub)
    finally:  # das echte Modell nicht überschreiben
        if keep is not None:
            PFILE.write_text(keep)
        elif PFILE.exists():
            PFILE.unlink()
    if not m:
        return None
    return {k: np.array(m[k], dtype=float) for k in ("th", "mu", "sd")}


def _shift(a, k):
    out = np.full(len(a), np.nan)
    if k < len(a):
        out[k:] = a[: len(a) - k]
    return out


def buy_arr(p):
    """ichi_buy für alle Tage auf einmal (gleiche Regel, vektorisiert)."""
    c, tk, kj, hi, lo = p.c, p.tenkan, p.kijun, p.cloud_hi, p.cloud_lo
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(p.s200) & (c > p.s200) & np.isfinite(hi) & np.isfinite(lo) & (c > 0)
        ok &= (c > hi) & ((hi - lo) / c >= WHIP)
        crossed = (_shift(tk, 2) <= _shift(kj, 2)) & (_shift(tk, 1) > _shift(kj, 1))
        holds = (tk > kj) & (_shift(tk, 1) > _shift(kj, 1)) & (_shift(c, 1) > _shift(hi, 1))
    out = ok & crossed & holds
    out[: min(210, len(out))] = False
    return out


def sell_arr(p):
    """ichi_sell für alle Tage (TK kreuzt runter oder Kurs unter der Wolke)."""
    with np.errstate(invalid="ignore"):
        tk = (_shift(p.tenkan, 1) >= _shift(p.kijun, 1)) & (p.tenkan < p.kijun)
        under = np.isfinite(p.cloud_lo) & (p.c < p.cloud_lo)
    out = tk | under
    out[:1] = False
    return out


def index_pack():
    """Pack des Marktindex (MSCI World) aus dem Kurs-Cache – für REGIME = 'index'."""
    try:
        raw = pd.read_pickle(CACHE)
        s = raw.get(REGIME_INDEX)
        if s is None or not len(s):
            s = _yf_batch([REGIME_INDEX], period="10y").get(REGIME_INDEX)
        return Pack(REGIME_INDEX, s) if s is not None and len(s) > 260 else None
    except Exception:
        return None


def _regime_index(p, i):
    """Stier: Index über SMA 200 und 12 Monate im Plus. Bär: beides negativ. Sonst neutral."""
    if p is None or i is None or i < 252 or not np.isfinite(p.s200[i]):
        return 0.2, "neutral"
    up, mom = p.c[i] > p.s200[i], p.c[i] > p.c[i - 252]
    if up and mom:
        return 0.33, "bull"
    if not up and not mom:
        return 0.1, "bear"
    return 0.2, "neutral"


def eur_price(t, px, usd):
    return to_eur(px, quote_ccy(t), usd) or px


def entry_ok(p, i):
    """Zusatzfilter beim Kauf: langfristiger Trend steigt, Kurs nicht zu wild (TREND_MIN/RANGE_MAX)."""
    if TREND_MIN and (i < 20 or not np.isfinite(p.s200[i - 20]) or p.s200[i] / p.s200[i - 20] - 1 < TREND_MIN):
        return False
    if RANGE_MAX and i >= 60:
        w = p.c[i - 60 : i + 1]
        if w.max() / w.min() - 1 > RANGE_MAX:
            return False
    return True


def market_series(packs, days):
    """Stier/Neutral/Bär je Tag, wie market(): Anteil über SMA 200 und mit 12-Monats-Plus."""
    D = np.array(days, dtype="datetime64[D]")
    if REGIME == "index":
        p = index_pack()
        if p is not None:
            pos = np.searchsorted(p.dates, D, side="right") - 1
            return [_regime_index(p, int(k) if k >= 0 else None) for k in pos]
        print("  Index-Kurse fehlen – nehme Marktbreite.")
    n, a, m = (np.zeros(len(D)) for _ in range(3))
    for p in packs.values():
        pos = np.searchsorted(p.dates, D, side="right") - 1  # letzter bekannter Kurs
        ok = pos >= 210
        if not ok.any():
            continue
        i = pos[ok]
        n[ok] += 1
        with np.errstate(invalid="ignore"):
            a[ok] += np.isfinite(p.s200[i]) & (p.c[i] > p.s200[i])
            j = i >= 252
            mm = np.zeros(len(i))
            mm[j] = p.c[i[j]] > p.c[i[j] - 252]
        m[ok] += mm
    out = []
    for k in range(len(D)):
        if n[k] < 30:
            out.append((0.2, "neutral"))
            continue
        above, mom = a[k] / n[k], m[k] / n[k]
        out.append((0.33, "bull") if above >= 0.55 and mom >= 0.55 else
                   (0.1, "bear") if above <= 0.4 and mom <= 0.45 else (0.2, "neutral"))
    return out


def fx_history(start):
    """Tägliche Wechselkurse (USD je Einheit) ab start; Lücken werden aufgefüllt."""
    pairs = {"EUR": "EURUSD=X", "HKD": "HKDUSD=X", "GBP": "GBPUSD=X", "SEK": "SEKUSD=X", "NOK": "NOKUSD=X",
             "CHF": "CHFUSD=X", "DKK": "DKKUSD=X", "PLN": "PLNUSD=X", "TRY": "TRYUSD=X", "CAD": "CADUSD=X"}
    now = fx_usd()
    got = _yf_batch(list(pairs.values()), start=(pd.Timestamp(start) - pd.Timedelta(days=10)).strftime("%Y-%m-%d"))
    df = pd.DataFrame({c: got[p] for c, p in pairs.items() if p in got})
    if df.empty:
        print("  historische Wechselkurse nicht verfügbar – nehme heutige.")
        return lambda day: now
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df.sort_index().ffill()

    def at(day):
        k = df.index.searchsorted(pd.Timestamp(day), side="right") - 1
        d = dict(now)
        if k >= 0:
            d.update({c: float(v) for c, v in df.iloc[k].items() if np.isfinite(v)})
        d["GBX"] = d["GBP"] / 100
        return d

    return at


def _bt_sell_reason(o, p, i, hold, px, usd, t):
    """Dieselbe Verkaufslogik wie im Scan (Kratzer, enger Stopp, Stopp −8 %, Ichimoku + Regel B)."""
    cost = o["cost"]
    o["peak"], o["trough"] = max(o["peak"], px), min(o["trough"], px)
    mfe, mae = o["peak"] / cost - 1, o["trough"] / cost - 1
    if hold >= D5 and not o["d5"]:
        o["d5"] = True
        under = np.isfinite(p.kijun[i]) and px < p.kijun[i]
        if mae <= D5_MAE and mfe < D5_MFE:
            o["scratch"] = True
        elif mae <= T_MAE and under:
            o["tight"] = True
    if o.get("scratch"):
        return "KRATZER"
    if o.get("tight") and px <= cost * (1 - T_STOP):
        return "ENGER"
    if px <= cost * (1 - STOP):
        return "STOP"
    if BREAKEVEN and mfe >= BREAKEVEN and px <= cost:
        return "EINSTAND"
    if o["sell"][i]:
        qc = quote_ccy(t)
        if pays10(o["qty"], to_eur(px, qc, usd) or px, to_eur(cost, qc, usd) or cost) or (
            hold >= B_HOLD and mfe >= B_MFE and px > cost
        ):
            return "REGEL"
    return None


def simulate(packs, model, start, end, cash0, fx_at, log=True):
    """Tagesweise Simulation. Gibt dict mit Kurve, Trades, Kennzahlen zurück."""
    S, E = np.datetime64(start, "D"), np.datetime64(end, "D")
    days = sorted({d for p in packs.values() for d in p.dates[(p.dates >= S) & (p.dates <= E)]})
    if not days:
        return None
    mkt = market_series(packs, days)
    sig = {}  # Kaufsignal + P je Titel, nur Tage im Zeitraum
    for t, p in packs.items():
        b = buy_arr(p)
        idx = np.nonzero(b & (p.dates >= S) & (p.dates <= E))[0]
        for i in idx:
            x = feat(p, i)
            if x is None or not np.all(np.isfinite(x)):
                continue
            pr = predict(model["th"], model["mu"], model["sd"], x) if model else 0.55
            if pr >= P_MIN and entry_ok(p, i):
                sig.setdefault(p.dates[i], []).append((pr, t, i))
    cash, pos, trades, curve = cash0, {}, [], []
    cool, scar, lot_skips = {}, {}, 0
    for k, day in enumerate(days):
        dd = pd.Timestamp(day).date()
        usd = fx_at(dd)
        # 1) Verkäufe am Schlusskurs
        for t in list(pos):
            o, p = pos[t], packs[t]
            i = p.imap.get(day)
            if i is None:
                continue
            px = float(p.c[i])
            hold = (dd - o["day"]).days
            why = _bt_sell_reason(o, p, i, hold, px, usd, t)
            if not why:
                continue
            gross = to_eur(o["qty"] * px, quote_ccy(t), usd) or 0.0
            fee = fees_eur(t, o["qty"], px, usd)
            cash += gross - fee
            pnl = gross - fee - o["paid"]
            trades.append({"ticker": t, "buy": str(o["day"]), "sell": str(dd), "qty": o["qty"], "in": o["cost"],
                           "out": px, "pnl": pnl, "ret": pnl / o["paid"], "why": why, "days": hold})
            cool[t] = dd
            if why in ("STOP", "KRATZER", "ENGER"):
                scar[t] = dd
            del pos[t]
        # 2) Käufe (Deckel je Marktlage, Uniqueness, max. 20 Posten, Freedom24-Lose)
        cap_pct = mkt[k][0]
        cands = sorted(sig.get(day, []), reverse=True)
        bought = 0
        for pr, t, i in cands:
            if t in pos or len(pos) >= MAXPOS:
                continue
            if t in cool and (dd - cool[t]).days < COOL:
                continue
            if t in scar and (dd - scar[t]).days < SCAR:
                continue
            ages = [(dd - o["day"]).days for o in pos.values()]
            if uniq_new({}, ages) < U_MIN:
                break
            px = float(packs[t].c[i])
            if MIN_PRICE_EUR and eur_price(t, px, usd) < MIN_PRICE_EUR:
                continue
            left = max(1, min(3, MAXPOS - len(pos)))
            base = _bt_nav(cash, pos, packs, day, usd) if SIZE_BASE == "nav" else cash
            qty, need = size_buy(t, px, min(base * cap_pct, cash / left), cash, usd)
            if qty <= 0:
                lot_skips += lot_of(t) > 1 and buy_cost_eur(t, lot_of(t), px, usd) > cash
                continue
            cash -= need
            pos[t] = {"qty": qty, "cost": px, "paid": need, "day": dd, "peak": px, "trough": px, "d5": False,
                      "sell": sell_arr(packs[t])}
            bought += 1
        curve.append((dd, _bt_nav(cash, pos, packs, day, usd), len(pos)))
        if log and (k % 60 == 0 or k == len(days) - 1):
            print(f"  {dd}  Depot {curve[-1][1]:9.2f} €  Posten {len(pos):2d}  Trades {len(trades)}", flush=True)
    return {"curve": curve, "trades": trades, "open": pos, "cash": cash, "cash0": cash0,
            "lot_skips": lot_skips, "signals": sum(len(v) for v in sig.values())}


BTDIR = HERE / "backtests"
BENCH = ("EUNL.DE", "MSCI World (iShares, EUR)")


def _bt_stats(res):
    eq = np.array([v for _d, v, _n in res["curve"]], dtype=float)
    peak = np.maximum.accumulate(eq)
    dd = float(((eq - peak) / peak).min()) if len(eq) else 0.0
    tr = res["trades"]
    wins = [x for x in tr if x["pnl"] > 0]
    d0, d1 = res["curve"][0][0], res["curve"][-1][0]
    yrs = max((d1 - d0).days / 365.25, 1 / 365.25)
    end = eq[-1]
    return {"end": end, "ret": end / res["cash0"] - 1, "cagr": (end / res["cash0"]) ** (1 / yrs) - 1 if yrs >= 1 else None,
            "maxdd": dd, "n": len(tr), "hit": len(wins) / len(tr) if tr else 0.0,
            "avg": float(np.mean([x["ret"] for x in tr])) if tr else 0.0,
            "fees": None, "d0": d0, "d1": d1}


def _bt_periods(res, by):
    """Rendite je Monat ('M') oder Jahr ('Y') aus der Depotkurve."""
    s = pd.Series({pd.Timestamp(d): v for d, v, _n in res["curve"]})
    key = s.index.to_period(by)
    out, prev = [], res["cash0"]
    for per, grp in s.groupby(key):
        out.append((str(per), grp.iloc[-1] / prev - 1, grp.iloc[-1]))
        prev = grp.iloc[-1]
    return out


def _bench(start, end, fx_at):
    try:
        raw = pd.read_pickle(CACHE)
        s = raw.get(BENCH[0])
    except Exception:
        s = None
    if s is None or not len(s):
        got = _yf_batch([BENCH[0]], start=str(start))
        s = got.get(BENCH[0])
    if s is None or not len(s):
        return None
    s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
    return float(s.iloc[-1] / s.iloc[0] - 1) if len(s) > 1 else None


def print_backtest(res, st, bench, spec, by):
    print(f"\n== Backtest {spec}: {st['d0']} bis {st['d1']} ==")
    print(f"  Startkapital       {res['cash0']:10.2f} €")
    print(f"  Endwert            {st['end']:10.2f} €   ({st['ret']*100:+.1f} %)")
    if st["cagr"] is not None:
        print(f"  pro Jahr (CAGR)    {st['cagr']*100:+10.1f} %")
    print(f"  max. Rückgang      {st['maxdd']*100:10.1f} %")
    if bench is not None:
        print(f"  Vergleich {BENCH[1]}: {bench*100:+.1f} % (kaufen & halten)")
    print(f"  Trades             {st['n']:10d}   Treffer {st['hit']*100:.0f} %   Ø je Trade {st['avg']*100:+.1f} %")
    print(f"  Kaufsignale (P≥{P_MIN:.0%}) {res['signals']:6d}   wegen Freedom24-Los zu teuer übersprungen: {res['lot_skips']}")
    print(f"  noch offen am Ende {len(res['open']):10d}   (im Endwert zum Schlusskurs bewertet)")
    if by:
        rows = _bt_periods(res, by)
        print(f"\n  {'Monat' if by == 'M' else 'Jahr':8} {'Rendite':>8} {'Depot':>10}")
        for per, r, v in rows:
            print(f"  {per:8} {r*100:+7.1f}% {v:10.2f} €")
    why = {}
    for x in res["trades"]:
        w = why.setdefault(x["why"], [0, 0.0])
        w[0] += 1
        w[1] += x["pnl"]
    if why:
        print("\n  Verkaufsgrund   Anzahl   Ergebnis")
        for k, (n, v) in sorted(why.items(), key=lambda kv: -kv[1][0]):
            print(f"  {k:14} {n:7d} {v:+10.2f} €")
    best = sorted(res["trades"], key=lambda x: x["pnl"], reverse=True)
    if best:
        show = best if len(best) <= 6 else best[:3] + best[-3:]
        print("\n  Beste / schlechteste Trades:")
        for x in show:
            print(f"  {x['buy']} → {x['sell']}  {NAME.get(x['ticker'], x['ticker'])[:22]:22} ({x['ticker']})  "
                  f"{x['ret']*100:+6.1f} %  {x['pnl']:+8.2f} €  {x['why']}")


def save_backtest(res, spec):
    BTDIR.mkdir(exist_ok=True)
    tag = spec.replace(":", "_bis_")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    tp = BTDIR / f"bt_{tag}_{stamp}_trades.csv"
    cp = BTDIR / f"bt_{tag}_{stamp}_depot.csv"
    pd.DataFrame(res["trades"]).to_csv(tp, index=False)
    pd.DataFrame(res["curve"], columns=["tag", "depot_eur", "posten"]).to_csv(cp, index=False)
    print(f"\n  gespeichert: {tp.relative_to(HERE)} und {cp.name}")


def backtest(spec, cash0=CASH0, markets=MARKETS, by=None, retrain=True, save=True):
    """Backtest über einen Monat, ein Jahr oder mehrere Jahre."""
    start, end = parse_period(spec)
    print(f"Backtest {spec}: {start} bis {end} · Start {cash0:.0f} € · Märkte {','.join(markets)}")
    print(f"  Einstellungen: Größe={SIZE_BASE} · Mindestorder {MIN_ORDER:.0f} € · Mindestkurs {MIN_PRICE_EUR:g} € · Marktlage={REGIME}"
          f" · Trend≥{TREND_MIN:g} · Spanne≤{RANGE_MAX:g} · Einstand ab {BREAKEVEN:g}")
    if by is None:
        by = "M" if (end - start).days <= 400 else "Y"
    packs = load_packs(False, markets)
    packs = {t: p for t, p in packs.items() if kind_of(t) == "AKTIE"}
    first = min((p.dates[0] for p in packs.values()), default=None)
    if first is None or np.datetime64(start, "D") < first + np.timedelta64(300, "D"):
        print(f"  Hinweis: Kurse im Cache erst ab {first}; die ersten ~300 Tage braucht der Bot für SMA 200/Ichimoku.")
    model = _bt_train(packs, start) if retrain else load_p()
    if model is None:
        print("  P-Modell: zu wenig Trades vor dem Start – nehme gespeichertes Modell (Achtung: kennt die Zukunft).")
        model = load_p()
    fx_at = fx_history(start)
    res = simulate(packs, model, start, end, cash0, fx_at)
    if not res:
        print("Keine Kursdaten im Zeitraum.")
        return None
    st = _bt_stats(res)
    print_backtest(res, st, _bench(start, end, fx_at), spec, by)
    if save:
        save_backtest(res, spec)
    return res


def _bt_nav(cash, pos, packs, day, usd):
    D = np.datetime64(day, "D")
    v = cash
    for t, o in pos.items():
        p = packs[t]
        k = int(np.searchsorted(p.dates, D, side="right")) - 1
        px = p.c[k] if k >= 0 else o["cost"]
        v += to_eur(o["qty"] * px, quote_ccy(t), usd) or 0.0
    return v

# Echte Freedom24-Orders eintragen, damit der Bot mit deinem wirklichen Depot rechnet.


def resolve_ticker(q):
    """Freedom24- oder Yahoo-Kürzel -> Yahoo-Ticker (z. B. TENC.700.AS, 700.HK, SAP.EU -> SAP.DE)."""
    q = q.strip()
    if not F24:
        load_universe(quick=True)
    up = q.upper()
    for y in F24:
        if y.upper() == up:
            return y
    for y, f in F24.items():
        if f.upper() == up:
            return y
    if up.endswith(".US") and up[:-3].replace(".", "-") in F24:
        return up[:-3].replace(".", "-")
    if up.endswith(".HK") and up[:-3].isdigit():
        z = up[:-3].zfill(4) + ".HK"
        if z in F24:
            return z
    return q  # unbekannt: so übernehmen (Warnung beim Aufrufer)


def _parse_day(d):
    if not d:
        return str(datetime.now().date())
    return str(datetime.fromisoformat(d).date())


FORCE = False  # --force: Los-Prüfung nur warnen statt ablehnen


def _check_lot(t, qty):
    """True, wenn die Stückzahl bei Freedom24 handelbar ist (Vielfaches des Loses)."""
    if t not in F24 and kind_of(t) == "AKTIE":
        print(f"  WARNUNG: {t} steht nicht in der Freedom24-Liste (Tippfehler? Yahoo-Kürzel nutzen, z. B. SAP.DE, 0700.HK).")
    lot = lot_of(t)
    if lot > 1 and qty % lot:
        msg = f"{t} wird bei Freedom24 nur in Losen zu {lot} Stück gehandelt – {qty:g} ist kein Vielfaches."
        if FORCE:
            print("  WARNUNG: " + msg + " (--force: trotzdem gebucht)")
            return True
        print("  ABGELEHNT: " + msg + " Mit --force trotzdem eintragen.")
        return False
    return True


def _ccy_for(t, ccy):
    """Handelswährung wie bei Freedom24 (London: GBP, nicht Pence)."""
    return (ccy or "").upper() or f24_ccy(t)


def _fee_for(t, qty, price, ccy, usd):
    px_q = price * 100 if quote_ccy(t) == "GBX" and ccy == "GBP" else price
    return fees_eur(t, qty, px_q, usd)


def manual_buy(t, qty, price, ccy=None, day=None, fee=None, cash=True):
    """Kauf eintragen. price in Handelswährung (London: Pfund, nicht Pence)."""
    s = load_state()
    t = resolve_ticker(t)
    if not _check_lot(t, qty):
        return
    ccy, day, usd = _ccy_for(t, ccy), _parse_day(day), fx_usd()
    gross_e = to_eur(qty * price, ccy, usd) or qty * price
    fee = _fee_for(t, qty, price, ccy, usd) if fee is None else fee
    h = next((x for x in s["holdings"] if x["ticker"] == t), None)
    if h:  # Nachkauf: Durchschnittspreis in der neuen Währung
        old_e = to_eur(h["qty"] * h["cost"], h.get("ccy") or ccy, usd) or h["qty"] * h["cost"]
        tot = h["qty"] + qty
        h["cost"] = round((old_e + gross_e) / tot / (to_eur(1.0, ccy, usd) or 1.0), 6)
        h["qty"], h["ccy"] = tot, ccy
    else:
        s["holdings"].append({"ticker": t, "qty": qty, "cost": price, "ccy": ccy, "kind": kind_of(t)})
        s["open_meta"][t] = {"since": day, "d5": False}
    if cash:
        s["cash"] = round(s["cash"] - gross_e - fee, 2)
    s["fills"].append({"side": "KAUF", "ticker": t, "qty": qty, "price": price, "ccy": ccy,
                       "fee_eur": round(fee, 2), "at": day, "manual": True})
    save_state(s)
    print(f"Kauf eingetragen: {qty:g} × {t} @ {price} {ccy} am {day}, Gebühr ≈ {fee:.2f} € · Kasse {s['cash']:.2f} €")
    if s["cash"] < 0:
        print("  Hinweis: Kasse ist negativ (Freedom24 gibt keinen Kredit). Mit --set-cash den echten Kontostand setzen.")


def manual_sell(t, qty, price, ccy=None, day=None, fee=None):
    s = load_state()
    t = resolve_ticker(t)
    h = next((x for x in s["holdings"] if x["ticker"] == t), None)
    if not h:
        print(f"{t} ist nicht im Depot. Bestand: {', '.join(x['ticker'] for x in s['holdings']) or 'leer'}")
        return
    qty = h["qty"] if qty is None else qty
    if qty > h["qty"] + 1e-9:
        print(f"Nur {h['qty']:g} Stück im Depot.")
        return
    if qty < h["qty"] - 1e-9 and not _check_lot(t, qty):
        return
    ccy, day, usd = _ccy_for(t, ccy or h.get("ccy")), _parse_day(day), fx_usd()
    gross_e = to_eur(qty * price, ccy, usd) or qty * price
    fee = _fee_for(t, qty, price, ccy, usd) if fee is None else fee
    cost_e = to_eur(qty * h["cost"], h.get("ccy") or ccy, usd) or qty * h["cost"]
    pnl = gross_e - fee - cost_e
    h["qty"] = round(h["qty"] - qty, 6)
    if h["qty"] <= 1e-9:
        s["holdings"] = [x for x in s["holdings"] if x["ticker"] != t]
        s["open_meta"].pop(t, None)
    s["cash"] = round(s["cash"] + gross_e - fee, 2)
    s["cool"][t] = day
    if pnl < 0:
        s["scars"][t] = day  # Narbe: 5 Tage kein Neukauf nach Verlust
    s["fills"].append({"side": "VERKAUF", "why": "HAND", "ticker": t, "qty": qty, "price": price, "ccy": ccy,
                       "fee_eur": round(fee, 2), "pnl_eur": round(pnl, 2), "at": day, "manual": True})
    save_state(s)
    print(f"Verkauf eingetragen: {qty:g} × {t} @ {price} {ccy} am {day} · Ergebnis ≈ {pnl:+.2f} € · Kasse {s['cash']:.2f} €")


def manual_remove(t):
    s = load_state()
    t = resolve_ticker(t)
    n = len(s["holdings"])
    s["holdings"] = [x for x in s["holdings"] if x["ticker"] != t]
    s["open_meta"].pop(t, None)
    save_state(s)
    print(f"{t} entfernt (ohne Buchung)." if len(s["holdings"]) < n else f"{t} war nicht im Depot.")


def set_cash(v):
    s = load_state()
    s["cash"] = float(v)
    save_state(s)
    print(f"Kasse auf {float(v):.2f} € gesetzt.")


def import_depot(path, cash=None):
    """CSV mit Kopfzeile ticker,qty,cost[,ccy][,date]. Ersetzt den Aktienbestand (ETF/Krypto bleiben)."""
    import csv

    s = load_state()
    keep = [h for h in s["holdings"] if kind_of(h["ticker"]) != "AKTIE"]
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    for r in csv.DictReader(lines, delimiter=";" if ";" in lines[0] else ","):
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        if not r.get("ticker"):
            continue
        t = resolve_ticker(r["ticker"])
        qty = float(r["qty"].replace(",", "."))
        if not _check_lot(t, qty):
            print(f"  Zeile {t} übersprungen.")
            continue
        rows.append({"ticker": t, "qty": qty, "cost": float(r["cost"].replace(",", ".")),
                     "ccy": _ccy_for(t, r.get("ccy")), "kind": kind_of(t)})
        s["open_meta"][t] = {"since": _parse_day(r.get("date")), "d5": False}
    s["holdings"] = keep + rows
    if cash is not None:
        s["cash"] = float(cash)
    save_state(s)
    print(f"{len(rows)} Aktien-Posten importiert (ETF/Krypto unverändert: {len(keep)}). Kasse {s['cash']:.2f} €")


def history(n=30):
    s = load_state()
    f = s.get("fills") or []
    print(f"Letzte {min(n, len(f))} von {len(f)} Buchungen:")
    for x in f[-n:]:
        extra = f"  G/V {x['pnl_eur']:+.2f} €" if "pnl_eur" in x else ""
        src = "Hand" if x.get("manual") else "Bot"
        print(f"  {str(x.get('at'))[:10]}  {x['side']:8} {x['qty']:g} × {x['ticker']} @ {x['price']:.4g} "
              f"{x.get('ccy', '')}  [{src}]{extra}")


# --- Lauf -------------------------------------------------------------------

def check_pence(raw):
    """London: Yahoo liefert meist Pence. Mit Freedom24-Kurs (GBP) abgleichen."""
    for t, s in raw.items():
        if t.endswith(".L") and len(s):
            f = LTP.get(t)
            PENCE[t] = True if not f else abs(math.log(float(s.iloc[-1]) / (f * 100))) < abs(math.log(float(s.iloc[-1]) / f))


def hold_days(meta, today):
    since = meta.get("since")
    if since:
        return max(0, (today - datetime.fromisoformat(since).date()).days)
    return int(meta.get("hold_days", 0))


def load_packs(rebuild_universe=False, markets=MARKETS, extra=()):
    stocks = load_universe(rebuild_universe, markets)
    base = TICKERS if ETF_CRYPTO else []
    want = list(dict.fromkeys(base + stocks + list(extra)))
    print(f"Universum: {len(stocks)} Freedom24-Aktien" + (f" + {len(base)} ETF/Krypto" if base else " (ETF/Krypto ausgeschaltet)"))
    raw = download(want, years=6)
    check_pence(raw)
    packs, wanted = {}, set(want)
    for t, s in raw.items():
        if t not in wanted:
            continue
        try:
            packs[t] = Pack(t, s)
        except Exception:
            continue
    print("Pakete", len(packs))
    return packs


def scan(apply: bool, force_train: bool, rebuild_universe=False, markets=MARKETS, top=15):
    state = load_state()
    print(f"Schatzhauser {VER} · Kasse {state['cash']:.2f} € · {len(state['holdings'])} Posten")
    held = [h["ticker"] for h in state["holdings"] if ETF_CRYPTO or kind_of(h["ticker"]) == "AKTIE"]
    packs = load_packs(rebuild_universe, markets, held)
    model = load_p()
    if force_train or model is None:
        trained = train_p(packs)
        if trained:
            model = {
                "th": np.array(trained["th"]),
                "mu": np.array(trained["mu"]),
                "sd": np.array(trained["sd"]),
            }
    usd = fx_usd()
    cap_pct, mkt = market(packs)
    print("Markt", {"bull": "Stier", "bear": "Bär", "neutral": "neutral"}[mkt], f"Deckel {cap_pct*100:.0f} %")

    now = datetime.now(timezone.utc)
    today = str(now.date())
    offers = []
    watch = []  # Depot-Übersicht mit Bewertung

    # Verkäufe
    for h in list(state["holdings"]):
        t = h["ticker"]
        if not ETF_CRYPTO and kind_of(t) != "AKTIE":
            continue  # ETF/Krypto vorerst außen vor: bleibt im Depot, kein Signal
        p = packs.get(t)
        if not p:
            print("  kein Kurs", t)
            continue
        i = p.last_i
        px = float(p.c[i])
        meta = state["open_meta"].get(t, {})
        qty = float(h["qty"])
        qccy = quote_ccy(t)
        # Einstand in Kurswährung (Chat-Depot hat z. B. TSLA in EUR, Kurs in USD)
        cost_raw, cost_ccy = float(h["cost"]), h.get("ccy") or qccy
        cost = cost_raw
        if cost_ccy != qccy:
            ce, one_e = to_eur(cost_raw, cost_ccy, usd), to_eur(1.0, qccy, usd)
            cost = ce / one_e if ce and one_e else cost_raw
        meta.setdefault("since", START_DAY)
        peak = max(float(meta.get("peak", cost)), px)
        trough = min(float(meta.get("trough", cost)), px)
        hold = hold_days(meta, now.date())
        mfe = peak / cost - 1 if cost else 0
        mae = trough / cost - 1 if cost else 0
        d5 = bool(meta.get("d5"))
        scratch = bool(meta.get("scratch"))
        tight = bool(meta.get("tight"))
        if hold >= D5 and not d5:
            d5 = True
            under = np.isfinite(p.kijun[i]) and px < p.kijun[i]
            if mae <= D5_MAE and mfe < D5_MFE:
                scratch = True
            elif mae <= T_MAE and under:
                tight = True
        meta.update(peak=peak, trough=trough, hold_days=hold, d5=d5, scratch=scratch, tight=tight)
        state["open_meta"][t] = meta

        reason = None
        if scratch:
            reason = "KRATZER"
        elif tight and px <= cost * (1 - T_STOP):
            reason = "ENGER"
        elif cost > 0 and px <= cost * (1 - STOP):
            reason = "STOP"
        elif BREAKEVEN and mfe >= BREAKEVEN and px <= cost:
            reason = "EINSTAND"
        elif ichi_sell(p, i):
            if pays10(qty, to_eur(px, qccy, usd) or px, to_eur(cost, qccy, usd) or cost) or (
                hold >= B_HOLD and mfe >= B_MFE and px > cost
            ):
                reason = "REGEL"
        pnl = px / cost - 1 if cost else 0
        sc = scout(p, None, BOOK.get(t, ""))
        watch.append((t, px, qccy, pnl, hold, reason, sc))
        if not reason:
            continue
        offers.append({"side": reason if reason == "STOP" else "VERKAUF", "why": reason, "ticker": t, "qty": qty, "price": px, "ccy": quote_ccy(t)})

    # Käufe
    ranked = []
    for t, p in packs.items():
        if any(h["ticker"] == t for h in state["holdings"]):
            continue
        if t in {o["ticker"] for o in offers}:
            continue
        cool_d = state["cool"].get(t)
        if cool_d and (now.date() - datetime.fromisoformat(cool_d).date()).days < COOL:
            continue
        scar_d = state["scars"].get(t)
        if scar_d and (now.date() - datetime.fromisoformat(scar_d).date()).days < SCAR:
            continue
        i = p.last_i
        if not ichi_buy(p, i) or not entry_ok(p, i):
            continue
        x = feat(p, i)
        pr = 0.55
        if model is not None and x is not None:
            pr = predict(model["th"], model["mu"], model["sd"], x)
        if pr < P_MIN:
            continue
        ranked.append((pr, t, i, p))
    ranked.sort(key=lambda r: r[0], reverse=True)
    print(f"Ichimoku-Kaufsignale mit P ≥ {P_MIN:.0%}: {len(ranked)}")

    # Bewertung (Späher) für Kandidaten: Bilanz-Punkte, Rang, Empfehlung.
    # Titel mit Empfehlung MEIDEN oder Bilanztermin (≤ 5 Tage) werden nicht vorgeschlagen.
    funds = fundamentals([r[1] for r in ranked[:60]] + [h["ticker"] for h in state["holdings"]])
    rated = []
    for pr, t, i, p in ranked[:60]:
        sc = scout(p, funds.get(t), BOOK.get(t, ""))
        rated.append((pr, t, i, p, sc))
    blocked = [r for r in rated if r[4]["advise"] == "MEIDEN" or r[4]["rank"] == "TERMIN"]
    ranked = [r for r in rated if r not in blocked]
    watch = [(t, px, c, pnl, hold, why, scout(packs[t], funds.get(t), BOOK.get(t, "")))
             for t, px, c, pnl, hold, why, _ in watch]

    ages = []
    for h in state["holdings"]:
        meta = state["open_meta"].get(h["ticker"], {})
        meta.setdefault("since", START_DAY)
        ages.append(float(hold_days(meta, now.date())))
    cash = float(state["cash"])
    n_open = len(state["holdings"])
    accepted_buy = 0
    uniq_stop = None
    lot_skip = []  # Kaufsignal, aber kleinstes Freedom24-Los zu teuer
    for pr, t, i, p, sc in ranked:
        if n_open + accepted_buy >= MAXPOS:
            break
        trial_ages = ages + [0] * accepted_buy
        u = uniq_new({}, trial_ages)
        if u < U_MIN:
            uniq_stop = u
            break
        px = float(p.c[i])
        if px <= 0:
            continue
        ccy = quote_ccy(t)
        if MIN_PRICE_EUR and eur_price(t, px, usd) < MIN_PRICE_EUR:
            continue
        left = max(1, min(3, MAXPOS - n_open - accepted_buy))
        base = cash
        if SIZE_BASE == "nav":  # Depotwert = Kasse + Aktienposten zum letzten Kurs
            base = cash + sum(eur_price(h["ticker"], float(packs[h["ticker"]].c[-1]), usd) * float(h["qty"])
                              for h in state["holdings"] if h["ticker"] in packs)
        cap = min(base * cap_pct, cash / left)
        qty, need = size_buy(t, px, cap, cash, usd)
        if qty <= 0:
            if lot_of(t) > 1 and buy_cost_eur(t, lot_of(t), px, usd) > min(cap, cash):
                lot_skip.append((t, lot_of(t), buy_cost_eur(t, lot_of(t), px, usd), cap))
            continue
        offers.append(
            {
                "side": "KAUF",
                "why": f"Ichimoku P={pr:.0%}",
                "ticker": t,
                "f24": F24.get(t, t),
                "qty": qty,
                "price": px,
                "ccy": ccy,
                "need": round(need, 2),
                "p": round(pr, 3),
                "score": f"{sc['pts']}/{sc['max']}" if sc["max"] else "–",
                "rank": sc["rank"],
                "advise": sc["advise"],
            }
        )
        cash -= need
        accepted_buy += 1

    for o in offers:
        o.setdefault("f24", F24.get(o["ticker"], o["ticker"]))
    print_report(offers, watch, blocked, ranked, uniq_stop, top, lot_skip)

    if apply and offers:
        for o in offers:
            t = o["ticker"]
            if o["side"] == "KAUF":
                need = float(o.get("need") or 0)
                if need > state["cash"]:
                    continue
                state["cash"] -= need
                state["holdings"].append(
                    {"ticker": t, "qty": o["qty"], "cost": o["price"], "ccy": o["ccy"], "kind": kind_of(t)}
                )
                state["open_meta"][t] = {"peak": o["price"], "trough": o["price"], "hold_days": 0,
                                         "d5": False, "since": today}
            else:
                h = next((x for x in state["holdings"] if x["ticker"] == t), None)
                if not h:
                    continue
                gross = to_eur(o["price"] * o["qty"], o["ccy"], usd) or o["price"] * o["qty"]
                state["cash"] += gross - fees_eur(t, o["qty"], o["price"], usd)
                state["holdings"] = [x for x in state["holdings"] if x["ticker"] != t]
                state["open_meta"].pop(t, None)
                state["cool"][t] = today
                if o["why"] in ("STOP", "KRATZER", "ENGER"):
                    state["scars"][t] = today
            state["fills"].append({**o, "at": now.isoformat()})
        print(f"gebucht (Papier) · Kasse {state['cash']:.2f} € · {len(state['holdings'])} Posten · {STATE.name}")
    elif offers:
        print("\nNur Vorschläge – es wird NICHTS gehandelt. In Freedom24 selbst ordern.")
        print("Mit --apply trägst du die Vorschläge ins Papier-Depot ein.")
    if apply:
        save_state(state)  # Hochstand/Tiefstand/Tag-5-Merker fortschreiben
    return offers


def _pct(v):
    return "   –  " if v is None else f"{v * 100:+6.1f}%"


def print_report(offers, watch, blocked, ranked, uniq_stop, top, lot_skip=()):
    print("\n== Depot ==")
    print(f"  {'Titel':24} {'Kurs':>10} {'G/V':>7} {'Tage':>4}  {'Bilanz':6} {'Rang':14} Signal")
    for t, px, c, pnl, hold, why, sc in watch:
        b = f"{sc['pts']}/{sc['max']}" if sc["max"] else "–"
        nm = f"{NAME.get(t, t)[:14]} ({t})"[:24]
        print(f"  {nm:24} {px:>9.4g} {c:3} {_pct(pnl)} {hold:>4}  {b:6} {sc['rank']:14} {why or 'halten'}")

    sells = [o for o in offers if o["side"] != "KAUF"]
    buys = [o for o in offers if o["side"] == "KAUF"]
    print("\n== Verkaufsvorschläge ==")
    if not sells:
        print("  keine")
    for o in sells:
        print(f"  {o['side']:8} {o['qty']:g} × {NAME.get(o['ticker'], o['ticker'])} "
              f"[Freedom24: {o['f24']}] @ {o['price']:.4g} {o['ccy']}  Grund: {o['why']}")

    print("\n== Kaufvorschläge ==")
    if not buys:
        print("  keine" + (f" – Uniqueness {uniq_stop:.2f} < {U_MIN} (Depot zu voll mit jungen Posten)" if uniq_stop else ""))
    for o in buys:
        lot = lot_of(o["ticker"])
        lot_s = f"  ({o['qty'] // lot:g} Los à {lot})" if lot > 1 else ""
        print(f"  KAUF {o['qty']:g} × {NAME.get(o['ticker'], o['ticker'])} [Freedom24: {o['f24']}] "
              f"@ {o['price']:.4g} {o['ccy']} ≈ {o['need']:.0f} €  P={o['p']:.0%}  Bilanz {o['score']}  {o['advise']}{lot_s}")
    for t, lot, one, cap in lot_skip:
        print(f"  übersprungen: {NAME.get(t, t)} ({F24.get(t, t)}) – kleinstes Los {lot} Stück "
              f"kostet ≈ {one:.0f} €, Budget je Posten nur {cap:.0f} €")

    if ranked or blocked:
        print(f"\n== Beobachtungsliste (Ichimoku-Kauf + P-Tor, beste {top}) ==")
        print(f"  {'Titel':28} {'P':>4}  {'Bilanz':6} {'Rang':14} {'Empfehlung':11} Notiz")
        rows = sorted(ranked + blocked, key=lambda r: r[0], reverse=True)[:top]
        for pr, t, i, p, sc in rows:
            b = f"{sc['pts']}/{sc['max']}" if sc["max"] else "–"
            nm = f"{NAME.get(t, t)[:16]} ({F24.get(t, t)})"[:28]
            flag = " (gesperrt)" if (pr, t, i, p, sc) in blocked else ""
            print(f"  {nm:28} {pr:4.0%}  {b:6} {sc['rank']:14} {sc['advise']:11} {sc['note']}{flag}")


def status():
    s = load_state()
    if not F24:
        load_universe(quick=True)
    print(f"Schatzhauser {s.get('ver', VER)} · Kasse {s['cash']:.2f} € · {len(s['holdings'])} Posten")
    print(f"  {'Titel':30} {'Stück':>8} {'Einstand':>10}     {'seit':10} Los  Freedom24")
    for h in s["holdings"]:
        t = h["ticker"]
        meta = s["open_meta"].get(t, {})
        off = "" if ETF_CRYPTO or kind_of(t) == "AKTIE" else "  (ETF/Krypto: ohne Signal)"
        print(f"  {(NAME.get(t, t)[:18] + ' (' + t + ')')[:30]:30} {h['qty']:>8g} {h['cost']:>10.4g} {h['ccy']:4}"
              f" {meta.get('since', START_DAY):10} {lot_of(t):>4}  {F24.get(t, '–')}{off}")
    print(f"Buchungen: {len(s.get('fills') or [])} (mit --history anzeigen)")


# --- Hilfe / Handbuch ----------------------------------------------------------

MANUAL = next((p for p in (HERE.parent / "handbuch" / "handbuch.md", HERE / "handbuch.md") if p.exists()),
              HERE.parent / "handbuch" / "handbuch.md")

EXAMPLES = """
Beispiele:
  Vorschläge (handelt nie selbst):
    python3 schatzhauser.py
    python3 schatzhauser.py --markets US,EU --top 30

  Backtest:
    python3 schatzhauser.py --backtest 2024-03          ein Monat
    python3 schatzhauser.py --backtest 2023             ein Jahr
    python3 schatzhauser.py --backtest 2021:2024        mehrere Jahre
    python3 schatzhauser.py --backtest 2023-06:2024-02  Monat bis Monat
    python3 schatzhauser.py --backtest 3y --cash 5000   letzte 3 Jahre, 5000 € Start
    python3 schatzhauser.py --backtest 2022:2024 --by month   monatliche Aufstellung

  Eigenes Depot eintragen (Preis in Handelswährung, London in Pfund):
    python3 schatzhauser.py --buy SAP.DE 5 182.40 --date 2026-09-20
    python3 schatzhauser.py --buy TENC.700.AS 100 440 --fee 12.80
    python3 schatzhauser.py --sell 0700.HK 100 452.2
    python3 schatzhauser.py --sell WEN            (alles zum aktuellen Kurs)
    python3 schatzhauser.py --add AAPL 10 190 --ccy USD --date 2025-01-10   ohne Kasse
    python3 schatzhauser.py --remove EVT.DE
    python3 schatzhauser.py --set-cash 2450.00
    python3 schatzhauser.py --import depot.csv --set-cash 2450
    python3 schatzhauser.py --status | --history | --reset

  Handbuch:
    python3 schatzhauser.py --manual                 ganzes Handbuch
    python3 schatzhauser.py --manual hongkong        nur Abschnitte mit "hongkong"
    python3 schatzhauser.py --manual "regel b"
"""


def show_manual(term=None):
    """Handbuch anzeigen oder nach einem Begriff durchsuchen (Abschnitte mit Treffer)."""
    if not MANUAL.exists():
        print(f"Handbuch nicht gefunden: {MANUAL}")
        return
    text = MANUAL.read_text(encoding="utf-8")
    if not term:
        print(text)
        return
    import re

    parts = re.split(r"(?m)^(?=#{1,3} )", text)
    q = term.lower()
    hits = [p for p in parts if q in p.lower()]
    if not hits:
        heads = [p.splitlines()[0] for p in parts if p.startswith("#")]
        print(f'Kein Treffer für "{term}". Abschnitte:\n  ' + "\n  ".join(heads))
        return
    pat = re.compile(re.escape(term), re.I)
    for p in hits:
        print(pat.sub(lambda m: f"\033[1;33m{m.group(0)}\033[0m" if sys.stdout.isatty() else f"»{m.group(0)}«", p.rstrip()))
        print("-" * 60)
    print(f'{len(hits)} Abschnitt(e) mit "{term}".')


def _last_price(t):
    try:
        raw = pd.read_pickle(CACHE)
        s = raw.get(t)
        if s is not None and len(s):
            check_pence({t: s})
            px = float(s.iloc[-1])
            return px / 100 if quote_ccy(t) == "GBX" else px
    except Exception:
        pass
    return None


def build_parser():
    ap = argparse.ArgumentParser(
        prog="schatzhauser.py",
        description=f"Schatzhauser {VER} – Vorschlags-Bot für Freedom24-Aktien. Handelt NIE selbst.",
        epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)
    ap.add_argument("-h", "--help", action="help", help="diese Hilfe anzeigen")
    g = ap.add_argument_group("Scan (Standard, ohne Option)")
    g.add_argument("--apply", action="store_true", help="Vorschläge ins Papier-Depot buchen (kein echter Handel)")
    g.add_argument("--train", action="store_true", help="P-Modell neu lernen, danach scannen")
    g.add_argument("--universe", action="store_true", help="Freedom24-Aktienliste sofort neu aufbauen")
    g.add_argument("--markets", default=",".join(MARKETS), metavar="LISTE",
                   help="Börsen: US,EU,HK,GR,TR (Standard: alle)")
    g.add_argument("--top", type=int, default=15, metavar="N", help="Länge der Beobachtungsliste (15)")

    b = ap.add_argument_group("Backtest")
    b.add_argument("--backtest", metavar="ZEITRAUM",
                   help="2024-03 | 2023 | 2021:2024 | 2023-06:2024-02 | 3y | 6m")
    b.add_argument("--cash", type=float, default=CASH0, metavar="EUR", help=f"Startkapital ({CASH0:.0f} €)")
    b.add_argument("--by", choices=["month", "year", "none"], help="Aufstellung je Monat/Jahr (Standard: automatisch)")
    b.add_argument("--no-retrain", action="store_true",
                   help="gespeichertes P-Modell nehmen statt nur mit Daten vor dem Start zu lernen")
    b.add_argument("--min-order", type=float, metavar="EUR", help=f"kleinster Kauf in € ({MIN_ORDER:.0f})")
    b.add_argument("--size", choices=["cash", "nav"], help="Deckel %% von freier Kasse (cash, Standard) oder Depotwert (nav)")
    b.add_argument("--min-price", type=float, metavar="EUR", help="keine Aktien unter diesem Kurs in € (z. B. 1)")
    b.add_argument("--regime", choices=["breite", "index"], help="Stier/Bär aus Marktbreite (Standard) oder MSCI World")
    b.add_argument("--trend-min", type=float, metavar="ANTEIL", help="SMA 200 muss in 20 Tagen steigen, z. B. 0.013")
    b.add_argument("--range-max", type=float, metavar="ANTEIL", help="max. Spanne der letzten 60 Tage, z. B. 0.18")
    b.add_argument("--breakeven", type=float, metavar="ANTEIL", help="nach so viel Plus Stopp auf Einstand, z. B. 0.04")
    b.add_argument("--no-save", action="store_true", help="Ergebnis nicht als CSV speichern")

    d = ap.add_argument_group("Depot von Hand")
    d.add_argument("--buy", nargs=3, metavar=("TICKER", "STÜCK", "PREIS"), help="Kauf eintragen (zieht Kasse ab)")
    d.add_argument("--sell", nargs="+", metavar="ARG",
                   help="TICKER STÜCK PREIS – Verkauf eintragen; nur TICKER: alles zum letzten Kurs")
    d.add_argument("--add", nargs=3, metavar=("TICKER", "STÜCK", "EINSTAND"),
                   help="Bestand eintragen, ohne Kasse zu ändern (Altbestand)")
    d.add_argument("--remove", metavar="TICKER", help="Posten ohne Buchung löschen")
    d.add_argument("--set-cash", type=float, metavar="EUR", help="Kasse setzen")
    d.add_argument("--import", dest="import_csv", metavar="CSV", help="Depot aus CSV (ticker,qty,cost,ccy,date)")
    d.add_argument("--date", metavar="JJJJ-MM-TT", help="Datum für --buy/--sell/--add (Standard: heute)")
    d.add_argument("--fee", type=float, metavar="EUR", help="echte Gebühr laut Freedom24 statt Schätzung")
    d.add_argument("--ccy", metavar="WÄHRUNG", help="Währung des Preises (Standard: Handelswährung)")
    d.add_argument("--status", action="store_true", help="Depot anzeigen")
    d.add_argument("--history", nargs="?", const=30, type=int, metavar="N", help="letzte N Buchungen")
    d.add_argument("--reset", action="store_true", help="Depot auf Chat-Stand zurücksetzen")

    h = ap.add_argument_group("Hilfe")
    d.add_argument("--force", action="store_true", help="Los-Prüfung nur warnen (z. B. Altbestand aus Split)")
    h.add_argument("--manual", nargs="?", const="", metavar="SUCHBEGRIFF", help="Handbuch anzeigen / durchsuchen")
    return ap


def _num_arg(ap, v, what):
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        ap.error(f"{what} muss eine Zahl sein: {v}")


def main():
    ap = build_parser()
    a = ap.parse_args()
    global FORCE
    FORCE = a.force
    global MIN_ORDER
    if a.min_order is not None:
        MIN_ORDER = a.min_order
    global SIZE_BASE, MIN_PRICE_EUR, REGIME
    SIZE_BASE = a.size or SIZE_BASE
    MIN_PRICE_EUR = a.min_price if a.min_price is not None else MIN_PRICE_EUR
    REGIME = a.regime or REGIME
    global TREND_MIN, RANGE_MAX, BREAKEVEN
    TREND_MIN = a.trend_min if a.trend_min is not None else TREND_MIN
    RANGE_MAX = a.range_max if a.range_max is not None else RANGE_MAX
    BREAKEVEN = a.breakeven if a.breakeven is not None else BREAKEVEN
    markets = tuple(m.strip().upper() for m in a.markets.split(",") if m.strip().upper() in MARKETS) or MARKETS
    if a.manual is not None:
        return show_manual(a.manual or None)
    if a.reset:
        save_state(empty_state())
        return print("Depot zurück auf Chat-Stand.")
    if a.import_csv:
        return import_depot(a.import_csv, a.set_cash)
    if a.set_cash is not None and not (a.buy or a.sell or a.add):
        return set_cash(a.set_cash)
    if a.buy:
        t, q, px = a.buy
        return manual_buy(t, _num_arg(ap, q, "STÜCK"), _num_arg(ap, px, "PREIS"), a.ccy, a.date, a.fee)
    if a.add:
        t, q, px = a.add
        return manual_buy(t, _num_arg(ap, q, "STÜCK"), _num_arg(ap, px, "EINSTAND"), a.ccy, a.date, 0.0, cash=False)
    if a.sell:
        t = a.sell[0]
        if len(a.sell) == 1:
            yt = resolve_ticker(t)
            px = _last_price(yt)
            if px is None:
                ap.error(f"kein Kurs für {yt} – bitte Stück und Preis angeben")
            print(f"  letzter Kurs {yt}: {px:.4g}")
            return manual_sell(t, None, px, a.ccy, a.date, a.fee)
        if len(a.sell) != 3:
            ap.error("--sell TICKER oder --sell TICKER STÜCK PREIS")
        return manual_sell(t, _num_arg(ap, a.sell[1], "STÜCK"), _num_arg(ap, a.sell[2], "PREIS"), a.ccy, a.date, a.fee)
    if a.remove:
        return manual_remove(a.remove)
    if a.status:
        return status()
    if a.history is not None:
        return history(a.history)
    if a.backtest:
        by = {"month": "M", "year": "Y", "none": ""}.get(a.by) if a.by else None
        try:
            parse_period(a.backtest)
        except (ValueError, IndexError):
            ap.error(f"Zeitraum nicht verstanden: {a.backtest} (z. B. 2024-03, 2023, 2021:2024, 3y)")
        return backtest(a.backtest, a.cash, markets, by, not a.no_retrain, not a.no_save)
    scan(apply=a.apply, force_train=a.train, rebuild_universe=a.universe, markets=markets, top=a.top)


if __name__ == "__main__":
    main()
