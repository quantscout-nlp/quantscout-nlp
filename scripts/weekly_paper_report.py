#!/usr/bin/env python3
"""
Weekly paper-trading report generator for the QuantScout Alpaca paper account.

Usage:
    python3 scripts/weekly_paper_report.py \
        --bundle docs/data/paper-week1-2026-11-09.json \
        --out docs/paper-trading-week1-2026-11-09.html

The bundle is a JSON snapshot of Alpaca data (orders, portfolio history, deposits,
benchmarks) for the week. The report is a single self-contained HTML file: no CDN,
no external fonts, opens offline in any browser.
"""
from __future__ import annotations

import argparse
import html
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def parse_ts(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)


def money(x: float, signed: bool = True) -> str:
    sign = "+" if (signed and x > 0) else ("-" if x < 0 else "")
    return f"{sign}${abs(x):,.2f}"


def pct(x: float, signed: bool = True, nd: int = 2) -> str:
    sign = "+" if (signed and x > 0) else ("-" if x < 0 else "")
    return f"{sign}{abs(x) * 100:.{nd}f}%"


def esc(s) -> str:
    return html.escape(str(s))


# ----------------------------------------------------------------------------
# core computations
# ----------------------------------------------------------------------------
def build_fills(orders, start: str, end: str):
    fills = []
    flat = []
    for o in orders:
        flat.append(o)
        # OCO / bracket children arrive nested under the parent (nested=true); a filled stop leg
        # under a canceled parent is still a real fill.
        for leg in (o.get("legs") or []):
            leg = dict(leg)
            leg.setdefault("source", o.get("source"))
            flat.append(leg)
    for o in flat:
        if o["status"] != "filled" or not o.get("filled_at"):
            continue
        t = parse_ts(o["filled_at"])
        d = t.date().isoformat()
        if not (start <= d <= end):
            continue
        fills.append({
            "t": t,
            "date": d,
            "symbol": o["symbol"],
            "side": o["side"],
            "intent": o.get("position_intent") or "",
            "qty": float(o["filled_qty"]),
            "price": float(o["filled_avg_price"]),
            "type": o["type"],
            "source": o.get("source") or "unknown",
            "order_class": o.get("order_class") or "",
            "id": o.get("id"),
        })
    fills.sort(key=lambda f: f["t"])
    return fills


def pair_trades(fills):
    """FIFO pairing per symbol. Returns (round_trips, residual_closes)."""
    open_lots = defaultdict(list)  # symbol -> list of dicts (long or short)
    trades, residual = [], []
    for f in fills:
        sym = f["symbol"]
        opens_long = f["intent"] == "buy_to_open" or (f["side"] == "buy" and f["intent"] != "buy_to_close")
        opens_short = f["intent"] == "sell_to_open" or f["side"] == "sell_short"
        if opens_long or opens_short:
            open_lots[sym].append({
                "dir": "short" if opens_short else "long",
                "qty": f["qty"], "price": f["price"], "t": f["t"], "src": f["source"],
            })
            continue
        # closing
        qty = f["qty"]
        if not open_lots[sym]:
            residual.append(f)
            continue
        while qty > 1e-9 and open_lots[sym]:
            lot = open_lots[sym][0]
            take = min(qty, lot["qty"])
            sign = 1 if lot["dir"] == "long" else -1
            pnl = (f["price"] - lot["price"]) * take * sign
            hold_min = (f["t"] - lot["t"]).total_seconds() / 60
            trades.append({
                "symbol": sym, "dir": lot["dir"], "qty": take,
                "entry_t": lot["t"], "entry": lot["price"],
                "exit_t": f["t"], "exit": f["price"],
                "pnl": pnl, "pnl_pct": pnl / (lot["price"] * take) if lot["price"] else 0.0,
                "hold_min": hold_min,
                "overnight": lot["t"].date() != f["t"].date(),
                "exit_date": f["t"].date().isoformat(),
                "entry_date": lot["t"].date().isoformat(),
                "entry_src": lot["src"], "exit_src": f["source"],
            })
            lot["qty"] -= take
            qty -= take
            if lot["qty"] <= 1e-9:
                open_lots[sym].pop(0)
    still_open = {s: lots for s, lots in open_lots.items() if lots}
    return trades, residual, still_open


def deposit_adjusted_hourly(ph, deposits, start: str, end: str):
    """Return list of (dt_et, equity_adj, equity_raw) within the window, with deposits removed."""
    out = []
    dep_events = [(parse_ts(d["created_at"]), d["net_amount"]) for d in deposits if d.get("created_at")]
    for ts, eq in zip(ph["timestamp"], ph["equity"]):
        t = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET)
        d = t.date().isoformat()
        if not (start <= d <= end):
            continue
        adj = eq - sum(a for (dt_, a) in dep_events if dt_ <= t)
        out.append((t, adj, eq))
    return out


def daily_equity(bundle, start, end):
    pd_ = bundle["portfolio_daily"]
    dep_by_date = defaultdict(float)
    for a in bundle["non_trade_activities"]:
        if a["activity_type"] == "CSD":
            dep_by_date[a["date"]] += a["net_amount"]
    rows = []
    cum_dep = 0.0
    prev_adj = None
    for d, e in zip(pd_["dates"], pd_["equity"]):
        if d > end:
            break
        cum_dep += dep_by_date.get(d, 0.0)
        adj = e - cum_dep if d >= start else e  # before the window keep raw
        if d < start:
            prev_adj = e
            prev_cum = cum_dep
            continue
        rows.append({"date": d, "equity_raw": e, "equity_adj": e - (cum_dep - prev_cum), "deposit": dep_by_date.get(d, 0.0)})
    # equity_adj above removes only deposits inside the window
    return prev_adj, rows


# ----------------------------------------------------------------------------
# report assembly
# ----------------------------------------------------------------------------
def compute(bundle):
    meta = bundle["meta"]
    start, end = meta["week_start"], meta["week_end"]
    fills = build_fills(bundle["orders"], start, end)
    trades, residual, still_open = pair_trades(fills)

    deposits = [a for a in bundle["non_trade_activities"] if a["activity_type"] == "CSD" and start <= a["date"] <= end]
    fees = [a for a in bundle["non_trade_activities"] if a["activity_type"] == "FEE" and start <= a["date"] <= end]

    base_equity, day_rows = daily_equity(bundle, start, end)
    hourly = deposit_adjusted_hourly(bundle["portfolio_hourly"], deposits, start, end)

    # daily stats
    by_day = defaultdict(lambda: {"realized": 0.0, "trades": 0, "wins": 0, "symbols": set(), "orders": 0, "fees": 0.0, "buys": 0, "sells": 0})
    for t in trades:
        d = by_day[t["exit_date"]]
        d["realized"] += t["pnl"]; d["trades"] += 1; d["wins"] += t["pnl"] > 0; d["symbols"].add(t["symbol"])
    for f in fills:
        d = by_day[f["date"]]
        d["orders"] += 1
        d["symbols"].add(f["symbol"])
        if f["side"].startswith("buy"): d["buys"] += 1
        else: d["sells"] += 1
    for a in fees:
        by_day[a["date"]]["fees"] += a["net_amount"]

    prev = base_equity
    for r in day_rows:
        s = by_day[r["date"]]
        r["day_pnl"] = r["equity_adj"] - prev if prev is not None else 0.0
        r["day_ret"] = r["day_pnl"] / prev if prev else 0.0
        r["realized"] = s["realized"]; r["trades"] = s["trades"]; r["wins"] = s["wins"]
        r["symbols"] = sorted(s["symbols"]); r["orders"] = s["orders"]; r["fees"] = s["fees"]
        r["buys"] = s["buys"]; r["sells"] = s["sells"]
        prev = r["equity_adj"]

    end_equity_adj = day_rows[-1]["equity_adj"] if day_rows else base_equity
    week_pnl = end_equity_adj - base_equity
    week_ret = week_pnl / base_equity if base_equity else 0.0

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    realized = sum(t["pnl"] for t in trades)
    exposure = sum(t["entry"] * t["qty"] for t in trades)
    intraday = [t for t in trades if not t["overnight"]]
    overnight = [t for t in trades if t["overnight"]]

    # benchmarks indexed to prev close
    bench = {}
    for sym, series in bundle["benchmarks"].items():
        prev_close = series.get(meta["prev_close_date"])
        pts = [(d, v / prev_close) for d, v in sorted(series.items()) if start <= d <= end]
        bench[sym] = {"prev_close": prev_close, "pts": pts, "week_ret": pts[-1][1] - 1 if pts else 0.0}

    # order-level facts
    win_orders = [o for o in bundle["orders"] if o.get("submitted_at") and start <= parse_ts(o["submitted_at"]).date().isoformat() <= end]
    sources = defaultdict(int)
    for o in win_orders:
        sources[o.get("source") or "unknown"] += 1
    canceled = [o for o in win_orders if o["status"] == "canceled"]
    oco = [o for o in win_orders if (o.get("order_class") or "") == "oco"]
    market_orders = [o for o in win_orders if o["type"] == "market"]

    return {
        "meta": meta, "start": start, "end": end, "fills": fills, "trades": trades, "residual": residual,
        "still_open": still_open, "deposits": deposits, "fees": fees, "base_equity": base_equity,
        "day_rows": day_rows, "hourly": hourly, "end_equity_adj": end_equity_adj,
        "week_pnl": week_pnl, "week_ret": week_ret, "wins": wins, "losses": losses,
        "gross_win": gross_win, "gross_loss": gross_loss, "realized": realized, "exposure": exposure,
        "intraday": intraday, "overnight": overnight, "bench": bench,
        "sources": dict(sources), "canceled": canceled, "oco": oco, "market_orders": market_orders,
        "win_orders": win_orders, "fee_total": sum(a["net_amount"] for a in fees),
    }


def fmt_t(t: datetime) -> str:
    return t.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def fmt_clock(t: datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def render(c, bundle) -> str:
    meta = c["meta"]
    trades = c["trades"]
    n = len(trades)
    win_rate = len(c["wins"]) / n if n else 0
    pf = (c["gross_win"] / c["gross_loss"]) if c["gross_loss"] else float("inf")
    avg_win = statistics.mean([t["pnl"] for t in c["wins"]]) if c["wins"] else 0
    avg_loss = statistics.mean([t["pnl"] for t in c["losses"]]) if c["losses"] else 0
    best = max(trades, key=lambda t: t["pnl"]) if trades else None
    worst = min(trades, key=lambda t: t["pnl"]) if trades else None
    avg_hold_intraday = statistics.mean([t["hold_min"] for t in c["intraday"]]) if c["intraday"] else 0
    symbols = sorted({t["symbol"] for t in trades})
    spy = c["bench"].get("SPY", {}).get("week_ret", 0.0)
    qqq = c["bench"].get("QQQ", {}).get("week_ret", 0.0)
    acct = bundle["account"]
    start_dt = datetime.fromisoformat(c["start"]); end_dt = datetime.fromisoformat(c["end"])
    title_range = f"{start_dt.strftime('%b %d')} – {end_dt.strftime('%b %d, %Y')}"

    # ------------------------------------------------------------------ JSON for charts
    sessions = sorted({t.date().isoformat() for (t, _, _) in c["hourly"]})
    def session_x(t: datetime) -> float:
        di = sessions.index(t.date().isoformat())
        open_ = t.replace(hour=9, minute=30, second=0, microsecond=0)
        frac = (t - open_).total_seconds() / (6.5 * 3600)
        return di + min(1.0, max(0.0, frac))
    data = {
        "sessions": [{"date": d, "label": datetime.fromisoformat(d).strftime("%a %b %d")} for d in sessions],
        "hourly": [{"x": round(session_x(t), 4), "date": t.date().isoformat(), "label": t.strftime("%a %I:%M %p").replace(" 0", " "), "adj": round(a, 2), "raw": round(r, 2)} for (t, a, r) in c["hourly"]],
        "base": c["base_equity"],
        "days": [{"date": r["date"], "dow": datetime.fromisoformat(r["date"]).strftime("%a"), "pnl": round(r["day_pnl"], 2), "ret": r["day_ret"], "equity": round(r["equity_adj"], 2), "realized": round(r["realized"], 2), "trades": r["trades"], "wins": r["wins"], "symbols": r["symbols"], "orders": r["orders"], "fees": round(r["fees"], 2)} for r in c["day_rows"]],
        "bench": {k: {"pts": [{"x": sessions.index(d) + 1.0, "date": d, "v": val} for (d, val) in v["pts"] if d in sessions]} for k, v in c["bench"].items()},
        "trades": [{"symbol": t["symbol"], "dir": t["dir"], "qty": t["qty"], "entry_t": t["entry_t"].isoformat(), "entry_label": fmt_t(t["entry_t"]), "entry": round(t["entry"], 4), "exit_t": t["exit_t"].isoformat(), "exit_label": fmt_t(t["exit_t"]), "exit": round(t["exit"], 4), "pnl": round(t["pnl"], 2), "pnl_pct": t["pnl_pct"], "hold_min": round(t["hold_min"]), "overnight": t["overnight"], "exit_date": t["exit_date"], "entry_src": t["entry_src"], "exit_src": t["exit_src"]} for t in sorted(trades, key=lambda x: x["exit_t"])],
    }

    # ------------------------------------------------------------------ day cards
    notes = meta.get("day_notes", {})
    day_cards = []
    for r in c["day_rows"]:
        d = r["date"]
        dd = datetime.fromisoformat(d)
        dts = [t for t in trades if t["exit_date"] == d]
        opens = sorted({f["symbol"] for f in c["fills"] if f["date"] == d and f["side"].startswith("buy") and f["intent"] != "buy_to_close"} | {f["symbol"] for f in c["fills"] if f["date"] == d and f["side"] == "sell_short"})
        first_fill = min((f["t"] for f in c["fills"] if f["date"] == d), default=None)
        last_fill = max((f["t"] for f in c["fills"] if f["date"] == d), default=None)
        held_over = [s for s, lots in c["still_open"].items()] if d == c["end"] else []
        carried_in = sorted({t["symbol"] for t in dts if t["overnight"]})
        carried_out = sorted({t["symbol"] for t in trades if t["entry_date"] == d and t["overnight"]})
        dep = r["deposit"]
        trade_lines = "".join(
            f"<li><span class='sym'>{esc(t['symbol'])}</span> {esc(t['dir'])} {t['qty']:g} sh · in {esc(fmt_clock(t['entry_t']))} @ ${t['entry']:,.2f} → out {esc(fmt_clock(t['exit_t']))} @ ${t['exit']:,.2f} · <span class='{'pos' if t['pnl']>0 else 'neg' if t['pnl']<0 else ''}'>{money(t['pnl'])}</span>{' · <em>overnight</em>' if t['overnight'] else ''}</li>"
            for t in sorted(dts, key=lambda x: x["exit_t"]))
        facts = []
        if first_fill: facts.append(f"First fill {fmt_clock(first_fill)} ET, last fill {fmt_clock(last_fill)} ET")
        facts.append(f"{r['orders']} filled orders ({r['buys']} buys, {r['sells']} sells)")
        if opens: facts.append(f"Opened: {', '.join(opens)}")
        if carried_in: facts.append(f"Closed positions carried in from a prior day: {', '.join(carried_in)}")
        if carried_out: facts.append(f"Held overnight into the next session: {', '.join(carried_out)}")
        if dep: facts.append(f"Paper cash deposit of ${dep:,.0f} added (excluded from all P&L figures)")
        if r["fees"]: facts.append(f"Regulatory fees {money(r['fees'])}")
        note = notes.get(d, "")
        cls = "pos" if r["day_pnl"] > 0 else "neg" if r["day_pnl"] < 0 else ""
        day_cards.append(f"""
        <article class="card day" data-date="{d}">
          <header>
            <div><div class="dow">{dd.strftime('%A')}</div><div class="date">{dd.strftime('%B %d, %Y')}</div></div>
            <div class="daypnl {cls}">{money(r['day_pnl'])}<small>{pct(r['day_ret'])} on equity</small></div>
          </header>
          <ul class="facts">{''.join(f'<li>{esc(x)}</li>' for x in facts)}</ul>
          <details><summary>{len(dts)} round trip{'s' if len(dts)!=1 else ''} · realized {money(r['realized'])}</summary><ul class="trades">{trade_lines or '<li>No round trips closed this day.</li>'}</ul></details>
          {f'<p class="note">{esc(note)}</p>' if note else ''}
        </article>""")

    # ------------------------------------------------------------------ symbol table
    by_sym = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        s = by_sym[t["symbol"]]; s["n"] += 1; s["pnl"] += t["pnl"]; s["wins"] += t["pnl"] > 0
    sym_rows = "".join(
        f"<tr><td>{esc(s)}</td><td>{v['n']}</td><td>{v['wins']}/{v['n']}</td><td class='{'pos' if v['pnl']>0 else 'neg' if v['pnl']<0 else ''}'>{money(v['pnl'])}</td></tr>"
        for s, v in sorted(by_sym.items(), key=lambda kv: -kv[1]["pnl"]))

    src_desc = ", ".join(f"{v} via {('Alpaca web dashboard' if k=='dashboard' else 'API key' if k=='access_key' else k)}" for k, v in sorted(c["sources"].items(), key=lambda kv: -kv[1]))
    pre = meta.get("context_before", "")
    after = meta.get("context_after", "")
    lessons = meta.get("lessons", [])
    next_steps = meta.get("next_steps", [])
    still_open_txt = ", ".join(f"{s} ({sum(l['qty'] for l in lots):g} sh)" for s, lots in c["still_open"].items()) or "none"

    def tile(label, value, sub="", cls=""):
        return f'<div class="tile"><div class="tl">{esc(label)}</div><div class="tv {cls}">{value}</div><div class="ts">{sub}</div></div>'

    tiles = "".join([
        tile("Week P&L (equity, ex-deposit)", money(c["week_pnl"]), f"{pct(c['week_ret'])} on ${c['base_equity']:,.2f} starting equity", "pos" if c["week_pnl"] > 0 else "neg"),
        tile("Realized on closed trades", money(c["realized"]), f"{n} round trips · {len(symbols)} symbols", "pos" if c["realized"] > 0 else "neg"),
        tile("Win rate", f"{win_rate*100:.0f}%", f"{len(c['wins'])} winners · {len(c['losses'])} losers"),
        tile("Profit factor", f"{pf:.2f}" if pf != float('inf') else "∞", f"gross +${c['gross_win']:,.0f} / −${c['gross_loss']:,.0f}"),
        tile("Avg win / avg loss", f"{money(avg_win)} / {money(avg_loss)}", f"payoff ratio {abs(avg_win/avg_loss):.2f}" if avg_loss else ""),
        tile("SPY · QQQ same week", f"{pct(spy)} · {pct(qqq)}", "close-to-close from the prior Friday"),
        tile("Avg intraday hold", f"{avg_hold_intraday/60:.1f} h", f"{len(c['intraday'])} intraday · {len(c['overnight'])} overnight round trips"),
        tile("Capital deployed", f"${c['exposure']:,.0f}", "sum of entry notional across round trips"),
    ])

    best_txt = f"{best['symbol']} {money(best['pnl'])} ({pct(best['pnl_pct'])})" if best else "n/a"
    worst_txt = f"{worst['symbol']} {money(worst['pnl'])} ({pct(worst['pnl_pct'])})" if worst else "n/a"

    headline = meta.get("headline") or (
        f"The paper book {'gained' if c['week_pnl']>=0 else 'lost'} {money(c['week_pnl'])} ({pct(c['week_ret'])}) over five sessions, "
        f"closing {n} round trips across {len(symbols)} symbols with a {win_rate*100:.0f}% win rate. "
        f"SPY returned {pct(spy)} over the same window.")

    data_json = json.dumps(data).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>QuantScout Paper Trading — {esc(meta['week_label'])} · {esc(title_range)}</title>
<style>
  :root {{ --bg:#07090d; --card:#10141c; --card2:#0b1018; --line:#1c2430; --text:#e8edf4; --muted:#93a0b3; --amber:#f5c15c;
           --pos:#3987e5; --neg:#e66767; --bench1:#d95926; --bench2:#199e70; --grid:#1a2230; --focus:#7dd3fc; }}
  * {{ box-sizing:border-box; }}
  html,body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif; }}
  .wrap {{ max-width:1120px; margin:0 auto; padding:22px 22px 72px; }}
  .badge {{ display:inline-block; background:#7a5a16; color:var(--amber); font-size:11px; font-weight:700; letter-spacing:.06em; padding:4px 10px; border-radius:999px; margin-right:6px; }}
  .badge.blue {{ background:#143a63; color:#9ccbff; }}
  h1 {{ font-size:26px; margin:10px 0 6px; line-height:1.25; }}
  h2 {{ font-size:18px; margin:34px 0 10px; border-bottom:1px solid var(--line); padding-bottom:6px; }}
  h3 {{ font-size:15px; margin:14px 0 6px; color:var(--focus); }}
  .meta {{ color:var(--muted); font-size:13px; }}
  .lead {{ font-size:16.5px; max-width:900px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin:10px 0; }}
  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:10px; margin:14px 0; }}
  .tile {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }}
  .tl {{ color:var(--muted); font-size:12px; letter-spacing:.02em; }}
  .tv {{ font-size:24px; font-weight:700; margin:2px 0; font-variant-numeric:tabular-nums; }}
  .ts {{ color:var(--muted); font-size:12px; }}
  .pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}
  .chart {{ width:100%; height:auto; display:block; }}
  .chartwrap {{ position:relative; }}
  .tip {{ position:absolute; pointer-events:none; background:#0b1018; border:1px solid var(--line); border-radius:8px; padding:8px 10px; font-size:12.5px; color:var(--text); display:none; min-width:150px; box-shadow:0 6px 24px rgba(0,0,0,.5); z-index:5; }}
  .tip b {{ font-variant-numeric:tabular-nums; }}
  .legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:12.5px; color:var(--muted); margin:4px 0 8px; }}
  .legend button {{ background:none; border:1px solid var(--line); color:var(--text); border-radius:999px; padding:3px 10px; cursor:pointer; font-size:12.5px; display:flex; align-items:center; gap:6px; }}
  .legend button.off {{ opacity:.4; }}
  .sw {{ width:14px; height:3px; border-radius:2px; display:inline-block; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ text-align:right; padding:6px 8px; border-bottom:1px solid var(--line); white-space:nowrap; font-variant-numeric:tabular-nums; }}
  th:first-child, td:first-child, th.l, td.l {{ text-align:left; }}
  th {{ color:var(--muted); font-weight:600; cursor:pointer; user-select:none; position:sticky; top:0; background:var(--card); }}
  th.sorted::after {{ content:" ▾"; color:var(--focus); }} th.sorted.asc::after {{ content:" ▴"; }}
  .scroll {{ overflow-x:auto; max-height:520px; overflow-y:auto; border:1px solid var(--line); border-radius:10px; }}
  .filters {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin:8px 0 10px; font-size:13px; color:var(--muted); }}
  .filters select, .filters input {{ background:var(--card2); color:var(--text); border:1px solid var(--line); border-radius:8px; padding:6px 8px; font-size:13px; }}
  .filters .count {{ margin-left:auto; }}
  .day header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }}
  .dow {{ font-weight:700; font-size:16px; }} .date {{ color:var(--muted); font-size:12.5px; }}
  .daypnl {{ font-size:22px; font-weight:700; text-align:right; font-variant-numeric:tabular-nums; }}
  .daypnl small {{ display:block; font-size:11.5px; color:var(--muted); font-weight:400; }}
  .facts {{ margin:8px 0 6px; padding-left:18px; color:#c9d3e0; font-size:13.5px; }}
  .facts li {{ margin:2px 0; }}
  details summary {{ cursor:pointer; color:var(--focus); font-size:13.5px; }}
  ul.trades {{ font-size:13px; padding-left:18px; margin:6px 0; }}
  ul.trades li {{ margin:3px 0; }} .sym {{ font-weight:700; }}
  .note {{ border-left:3px solid var(--amber); padding:6px 10px; margin:8px 0 0; color:#dfe6f0; font-size:13.5px; background:#141a24; border-radius:0 8px 8px 0; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
  @media (max-width:820px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
  .kv {{ display:grid; grid-template-columns:max-content 1fr; gap:4px 14px; font-size:13.5px; }}
  .kv div:nth-child(odd) {{ color:var(--muted); }}
  .lock {{ color:var(--muted); font-size:12.5px; }}
  ol.lessons li, ul.next li {{ margin:6px 0; }}
  .bar-label {{ font-size:11px; fill:var(--text); }}
  .axis {{ font-size:11px; fill:var(--muted); }}
  code {{ color:var(--focus); font-size:12.5px; }}
  footer {{ margin-top:40px; color:var(--muted); font-size:12px; }}
</style>
</head>
<body>
<div class="wrap">
  <span class="badge">PAPER ONLY</span><span class="badge blue">{esc(meta['week_label']).upper()} · DAILY PAPER TRADING TEST</span>
  <h1>QuantScout Paper Trading — {esc(meta['week_label'])} Report<br><span class="meta">{esc(title_range)} · 5 sessions · Alpaca paper account</span></h1>
  <p class="meta">Generated {esc(meta['generated'])} · {esc(meta['source'])} · all times US Eastern · figures exclude paper cash deposits</p>
  <p class="lead">{esc(headline)}</p>

  <div class="tiles">{tiles}</div>

  <h2>Equity curve vs. the market</h2>
  <div class="card">
    <p class="meta">Hourly account equity, indexed to 100 at the prior Friday close (${c['base_equity']:,.2f}). The ${sum(d['net_amount'] for d in c['deposits']):,.0f} paper deposit on {', '.join(d['date'] for d in c['deposits']) or 'n/a'} is removed so the line shows trading only. SPY and QQQ are daily closes indexed the same way.</p>
    <div class="legend" id="eqLegend"></div>
    <div class="chartwrap"><svg id="eqChart" class="chart" viewBox="0 0 1060 340" role="img" aria-label="Equity curve indexed to 100 versus SPY and QQQ"></svg><div class="tip" id="eqTip"></div></div>
  </div>

  <div class="grid2">
    <div class="card">
      <h3>Daily P&L on equity</h3>
      <p class="meta">Close-to-close change in deposit-adjusted equity. Includes mark-to-market on anything held overnight. Click a bar to filter the trade log.</p>
      <div class="chartwrap"><svg id="dayChart" class="chart" viewBox="0 0 520 260" role="img" aria-label="Daily profit and loss bars"></svg><div class="tip" id="dayTip"></div></div>
    </div>
    <div class="card">
      <h3>Round trips ranked by P&L</h3>
      <p class="meta">Every closed trade of the week, FIFO-paired from fills. Hover for entry and exit.</p>
      <div class="chartwrap"><svg id="tradeChart" class="chart" viewBox="0 0 520 {max(260, 22*n+40)}" role="img" aria-label="Trades ranked by profit and loss"></svg><div class="tip" id="tradeTip"></div></div>
    </div>
  </div>

  <h2>What it did, day by day</h2>
  {''.join(day_cards)}

  <h2>Trade log</h2>
  <div class="card">
    <div class="filters">
      <label>Day <select id="fDay"><option value="">All</option>{''.join(f'<option value="{r["date"]}">{datetime.fromisoformat(r["date"]).strftime("%a %b %d")}</option>' for r in c['day_rows'])}</select></label>
      <label>Outcome <select id="fOut"><option value="">All</option><option value="win">Winners</option><option value="loss">Losers</option></select></label>
      <label>Hold <select id="fHold"><option value="">All</option><option value="intraday">Intraday</option><option value="overnight">Overnight</option></select></label>
      <label>Symbol <input id="fSym" placeholder="e.g. CEG" size="8"></label>
      <span class="count" id="fCount"></span>
    </div>
    <div class="scroll"><table id="tradeTable">
      <thead><tr>
        <th class="l" data-k="exit_t">Closed</th><th class="l" data-k="symbol">Symbol</th><th class="l" data-k="dir">Side</th><th data-k="qty">Qty</th>
        <th class="l" data-k="entry_t">Entry</th><th data-k="entry">Entry $</th><th class="l" data-k="exit_t">Exit</th><th data-k="exit">Exit $</th>
        <th data-k="hold_min">Hold</th><th data-k="pnl">P&L $</th><th data-k="pnl_pct">P&L %</th>
      </tr></thead><tbody></tbody>
      <tfoot><tr id="tfoot"></tr></tfoot>
    </table></div>
  </div>

  <div class="grid2">
    <div class="card">
      <h3>By symbol</h3>
      <table><thead><tr><th class="l">Symbol</th><th>Round trips</th><th>Wins</th><th>Net P&L</th></tr></thead><tbody>{sym_rows}</tbody></table>
    </div>
    <div class="card">
      <h3>Execution facts</h3>
      <div class="kv">
        <div>Orders submitted</div><div>{len(c['win_orders'])} ({len(c['market_orders'])} market, {len(c['win_orders'])-len(c['market_orders'])} limit/OCO)</div>
        <div>Order entry path</div><div>{esc(src_desc)}</div>
        <div>Bracket / OCO exits</div><div>{len(c['oco'])} submitted, {sum(1 for o in c['oco'] if o['status']=='canceled')} canceled before the limit leg filled</div>
        <div>Canceled orders</div><div>{len(c['canceled'])}</div>
        <div>Best trade</div><div class="pos">{esc(best_txt)}</div>
        <div>Worst trade</div><div class="neg">{esc(worst_txt)}</div>
        <div>Regulatory fees (REG + CAT)</div><div>{money(c['fee_total'])}</div>
        <div>Open at week end</div><div>{esc(still_open_txt)}</div>
        <div>Residual closes (pre-week lots)</div><div>{', '.join(f"{f['symbol']} {f['qty']:g} sh" for f in c['residual']) or 'none'}</div>
      </div>
    </div>
  </div>

  <h2>Context and takeaways</h2>
  <div class="card">
    <h3>Before week 1</h3><p>{esc(pre)}</p>
    <h3>Since week 1</h3><p>{esc(after)}</p>
    <h3>What the week showed</h3>
    <ol class="lessons">{''.join(f'<li>{esc(x)}</li>' for x in lessons)}</ol>
    <h3>Suggested focus for week 2</h3>
    <ul class="next">{''.join(f'<li>{esc(x)}</li>' for x in next_steps)}</ul>
  </div>

  <h2>Account snapshot (as pulled)</h2>
  <div class="card kv">
    <div>Account status</div><div>{esc(acct['status'])} · paper · margin multiplier {acct['multiplier']}x · shorting {'on' if acct['shorting_enabled'] else 'off'} · fractional {'on' if acct['fractional_trading'] else 'off'}</div>
    <div>Equity (latest)</div><div>${acct['equity']:,.2f} (balance as of {esc(acct['balance_asof'])})</div>
    <div>Cash / long market value</div><div>${acct['cash']:,.2f} / ${acct['long_market_value']:,.2f}</div>
    <div>Open positions now</div><div>{', '.join(f"{p['symbol']} {p['qty']:g} sh @ ${p['avg_entry_price']:,.2f}, unrealized {money(p['unrealized_pl'])}" for p in bundle['positions_now']) or 'none'}</div>
    <div>Accrued fees</div><div>${acct['accrued_fees']:,.2f}</div>
  </div>

  <p class="lock">Method: round trips are FIFO-paired from filled orders in the Alpaca order history (filled quantity × filled average price), so partial fills are consolidated. Week P&L is the change in deposit-adjusted account equity and therefore includes overnight mark-to-market and fees. SPY and QQQ use IEX daily closes. Paper trading only. Not trade advice.</p>
  <footer>QuantScout NLP · paper trading weekly report · regenerate with <code>python3 scripts/weekly_paper_report.py --bundle docs/data/&lt;bundle&gt;.json --out docs/&lt;report&gt;.html</code></footer>
</div>

<script id="data" type="application/json">{data_json}</script>
<script>
(function(){{
  const D = JSON.parse(document.getElementById('data').textContent);
  const fmt$ = (x, s=true) => (x>0&&s?'+':x<0?'−':'') + '$' + Math.abs(x).toLocaleString(undefined,{{minimumFractionDigits:2,maximumFractionDigits:2}});
  const fmtP = (x, nd=2) => (x>0?'+':x<0?'−':'') + (Math.abs(x)*100).toFixed(nd) + '%';
  const COL = {{pos:'#3987e5', neg:'#e66767', eq:'#3987e5', SPY:'#d95926', QQQ:'#199e70', grid:'#1a2230', text:'#e8edf4', muted:'#93a0b3'}};
  const svgNS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs, parent){{ const e=document.createElementNS(svgNS, tag); for(const k in attrs) e.setAttribute(k, attrs[k]); if(parent) parent.appendChild(e); return e; }}
  function showTip(tip, host, x, y, html){{ tip.innerHTML = html; tip.style.display='block'; const r=host.getBoundingClientRect(); const tw=tip.offsetWidth, th=tip.offsetHeight; let left=x+14, top=y-th/2; if(left+tw>r.width) left=x-tw-14; if(top<0) top=4; if(top+th>r.height) top=r.height-th-4; tip.style.left=left+'px'; tip.style.top=top+'px'; }}
  function hideTip(tip){{ tip.style.display='none'; }}
  function pt(svg, evt){{ const r=svg.getBoundingClientRect(); const vb=svg.viewBox.baseVal; return {{x:(evt.clientX-r.left)*vb.width/r.width, y:(evt.clientY-r.top)*vb.height/r.height, px:evt.clientX-r.left, py:evt.clientY-r.top}}; }}

  // ---------------------------------------------------------------- equity curve (indexed)
  (function(){{
    const svg=document.getElementById('eqChart'), tip=document.getElementById('eqTip'), host=svg.parentElement;
    const W=1060,H=340,m={{l:54,r:70,t:16,b:44}};
    const base=D.base;
    const eq=D.hourly.map(p=>({{sx:p.x, date:p.date, v:p.adj/base*100, adj:p.adj, raw:p.raw, label:p.label}}));
    const bench={{}}; for(const k in D.bench) bench[k]=D.bench[k].pts.map(p=>({{sx:p.x, date:p.date, v:p.v*100}}));
    const series=[{{key:'Equity',color:COL.eq,pts:eq,on:true}},{{key:'SPY',color:COL.SPY,pts:bench.SPY||[],on:true}},{{key:'QQQ',color:COL.QQQ,pts:bench.QQQ||[],on:true}}];
    // x positions are session-relative (session index + fraction of the 9:30-16:00 ET session), precomputed
    // server-side so the chart is identical in every browser timezone; nights are compressed out.
    const days=D.sessions;
    const xOf=(sx)=> m.l + sx*(W-m.l-m.r)/days.length;
    function draw(){{
      svg.innerHTML='';
      const on=series.filter(s=>s.on);
      let vals=[]; on.forEach(s=>s.pts.forEach(p=>vals.push(p.v))); vals.push(100);
      let lo=Math.min(...vals), hi=Math.max(...vals); const pad=(hi-lo)*0.12||1; lo-=pad; hi+=pad;
      const y=v=>m.t+(hi-v)*(H-m.t-m.b)/(hi-lo);
      // grid
      const ticks=5; for(let i=0;i<=ticks;i++){{ const v=lo+(hi-lo)*i/ticks; el('line',{{x1:m.l,x2:W-m.r,y1:y(v),y2:y(v),stroke:COL.grid,'stroke-width':1}},svg); const tx=el('text',{{x:m.l-8,y:y(v)+4,'text-anchor':'end',class:'axis'}},svg); tx.textContent=v.toFixed(1); }}
      // baseline 100
      el('line',{{x1:m.l,x2:W-m.r,y1:y(100),y2:y(100),stroke:COL.muted,'stroke-width':1,'stroke-dasharray':'4 4'}},svg);
      // day labels + separators
      days.forEach((d,i)=>{{ const x0=m.l+i*(W-m.l-m.r)/days.length; if(i>0) el('line',{{x1:x0,x2:x0,y1:m.t,y2:H-m.b,stroke:COL.grid}},svg); const tx=el('text',{{x:x0+(W-m.l-m.r)/days.length/2,y:H-m.b+18,'text-anchor':'middle',class:'axis'}},svg); tx.textContent=d.label; }});
      el('text',{{x:m.l-8,y:m.t-4,'text-anchor':'end',class:'axis'}},svg).textContent='index';
      on.forEach(s=>{{
        const pts=s.pts.map(p=>({{x:xOf(p.sx),y:y(p.v),p}}));
        if(!pts.length) return;
        const d=pts.map((q,i)=>(i?'L':'M')+q.x.toFixed(1)+' '+q.y.toFixed(1)).join(' ');
        el('path',{{d,fill:'none',stroke:s.color,'stroke-width':2,'stroke-linejoin':'round','stroke-linecap':'round'}},svg);
        if(s.key!=='Equity') pts.forEach(q=>el('circle',{{cx:q.x,cy:q.y,r:4,fill:s.color,stroke:'#10141c','stroke-width':2}},svg));
        const last=pts[pts.length-1]; const lab=el('text',{{x:last.x+8,y:last.y+4,class:'axis',fill:COL.text}},svg); lab.textContent=s.key+' '+last.p.v.toFixed(1); lab.setAttribute('fill',COL.text);
      }});
      // crosshair layer
      const cross=el('line',{{x1:0,x2:0,y1:m.t,y2:H-m.b,stroke:COL.muted,'stroke-width':1,opacity:0}},svg);
      const dot=el('circle',{{r:5,fill:COL.eq,stroke:'#10141c','stroke-width':2,opacity:0}},svg);
      svg.onmousemove=(e)=>{{ const q=pt(svg,e); const eqPts=eq.map(p=>({{x:xOf(p.sx),p}})); let best=null,bd=1e9; eqPts.forEach(o=>{{ const d=Math.abs(o.x-q.x); if(d<bd){{bd=d;best=o;}} }}); if(!best) return; cross.setAttribute('x1',best.x); cross.setAttribute('x2',best.x); cross.setAttribute('opacity',1); dot.setAttribute('cx',best.x); dot.setAttribute('cy',y(best.p.v)); dot.setAttribute('opacity',1);
        const b=[]; for(const k of ['SPY','QQQ']) {{ const s=series.find(z=>z.key===k); if(!s.on) continue; const same=s.pts.filter(z=>z.date===best.p.date)[0]; if(same) b.push(`<div><span class="sw" style="background:${{s.color}}"></span> ${{k}} close <b>${{same.v.toFixed(2)}}</b></div>`); }}
        showTip(tip,host,q.px,q.py,`<div style="color:${{COL.muted}}">${{best.p.label}} ET</div><div><span class="sw" style="background:${{COL.eq}}"></span> Equity <b>${{best.p.v.toFixed(2)}}</b> · ${{fmt$(best.p.adj,false)}}</div><div style="color:${{COL.muted}}">vs start ${{fmt$(best.p.adj-base)}} (${{fmtP(best.p.adj/base-1)}})</div>${{b.join('')}}`); }};
      svg.onmouseleave=()=>{{ hideTip(tip); cross.setAttribute('opacity',0); dot.setAttribute('opacity',0); }};
    }}
    const leg=document.getElementById('eqLegend');
    series.forEach(s=>{{ const b=document.createElement('button'); b.innerHTML=`<span class="sw" style="background:${{s.color}}"></span>${{s.key}}${{s.key==='Equity'?' (hourly, deposit-adjusted)':' (daily close)'}}`; b.onclick=()=>{{ s.on=!s.on; b.classList.toggle('off',!s.on); draw(); }}; leg.appendChild(b); }});
    draw();
  }})();

  // ---------------------------------------------------------------- daily bars
  (function(){{
    const svg=document.getElementById('dayChart'), tip=document.getElementById('dayTip'), host=svg.parentElement;
    const W=520,H=260,m={{l:56,r:12,t:18,b:40}};
    const vals=D.days.map(d=>d.pnl); const mx=Math.max(...vals.map(Math.abs))*1.25||1;
    const y=v=>m.t+(mx-v)*(H-m.t-m.b)/(2*mx);
    const bw=(W-m.l-m.r)/D.days.length;
    for(let i=-2;i<=2;i++){{ const v=mx*i/2; el('line',{{x1:m.l,x2:W-m.r,y1:y(v),y2:y(v),stroke:i===0?COL.muted:COL.grid}},svg); const t=el('text',{{x:m.l-6,y:y(v)+4,'text-anchor':'end',class:'axis'}},svg); t.textContent=(v>0?'+':v<0?'−':'')+'$'+Math.abs(v).toFixed(0); }}
    D.days.forEach((d,i)=>{{ const x=m.l+i*bw+bw*0.18, w=bw*0.64; const y0=y(0), y1=y(d.pnl); const top=Math.min(y0,y1), h=Math.max(2,Math.abs(y1-y0)); const r=4;
      const path = d.pnl>=0 ? `M${{x}} ${{y0}} V${{top+r}} Q${{x}} ${{top}} ${{x+r}} ${{top}} H${{x+w-r}} Q${{x+w}} ${{top}} ${{x+w}} ${{top+r}} V${{y0}} Z` : `M${{x}} ${{y0}} V${{y0+h-r}} Q${{x}} ${{y0+h}} ${{x+r}} ${{y0+h}} H${{x+w-r}} Q${{x+w}} ${{y0+h}} ${{x+w}} ${{y0+h-r}} V${{y0}} Z`;
      const p=el('path',{{d:path,fill:d.pnl>=0?COL.pos:COL.neg,style:'cursor:pointer'}},svg);
      const lab=el('text',{{x:x+w/2,y:d.pnl>=0?top-6:y0+h+14,'text-anchor':'middle',class:'bar-label'}},svg); lab.textContent=fmt$(d.pnl);
      const ax=el('text',{{x:x+w/2,y:H-m.b+18,'text-anchor':'middle',class:'axis'}},svg); ax.textContent=d.dow+' '+d.date.slice(5).replace('-','/');
      const hit=el('rect',{{x:m.l+i*bw,y:m.t,width:bw,height:H-m.t-m.b,fill:'transparent',style:'cursor:pointer'}},svg);
      const over=(e)=>{{ const q=pt(svg,e); showTip(tip,host,q.px,q.py,`<div style="color:${{COL.muted}}">${{d.dow}} ${{d.date}}</div><div>Equity P&L <b>${{fmt$(d.pnl)}}</b> (${{fmtP(d.ret)}})</div><div>Realized on closes <b>${{fmt$(d.realized)}}</b></div><div>${{d.trades}} round trips · ${{d.wins}} wins · ${{d.orders}} fills</div><div style="color:${{COL.muted}}">${{d.symbols.join(', ')}}</div><div style="color:${{COL.muted}}">Close equity ${{fmt$(d.equity,false)}} · fees ${{fmt$(d.fees)}}</div><div style="color:${{COL.muted}};margin-top:4px">click to filter the trade log</div>`); }};
      [p,hit].forEach(n=>{{ n.onmousemove=over; n.onmouseleave=()=>hideTip(tip); n.onclick=()=>{{ const s=document.getElementById('fDay'); s.value=(s.value===d.date?'':d.date); s.dispatchEvent(new Event('change')); document.getElementById('tradeTable').scrollIntoView({{behavior:'smooth',block:'start'}}); }}; }});
    }});
  }})();

  // ---------------------------------------------------------------- trade bars ranked
  (function(){{
    const svg=document.getElementById('tradeChart'), tip=document.getElementById('tradeTip'), host=svg.parentElement;
    const T=[...D.trades].sort((a,b)=>b.pnl-a.pnl);
    const W=520, rowH=22, m={{l:84,r:70,t:14,b:24}}; const H=Math.max(260, rowH*T.length+m.t+m.b); svg.setAttribute('viewBox',`0 0 ${{W}} ${{H}}`);
    const mx=Math.max(...T.map(t=>Math.abs(t.pnl)))||1; const x0=m.l+(W-m.l-m.r)/2; const sc=(W-m.l-m.r)/2/mx;
    el('line',{{x1:x0,x2:x0,y1:m.t,y2:H-m.b,stroke:COL.muted}},svg);
    T.forEach((t,i)=>{{ const yy=m.t+i*rowH; const w=Math.max(2,Math.abs(t.pnl)*sc); const x=t.pnl>=0?x0:x0-w; const r=Math.min(4,w/2);
      const path = t.pnl>=0 ? `M${{x}} ${{yy+3}} H${{x+w-r}} Q${{x+w}} ${{yy+3}} ${{x+w}} ${{yy+3+r}} V${{yy+rowH-3-r}} Q${{x+w}} ${{yy+rowH-3}} ${{x+w-r}} ${{yy+rowH-3}} H${{x}} Z` : `M${{x+w}} ${{yy+3}} H${{x+r}} Q${{x}} ${{yy+3}} ${{x}} ${{yy+3+r}} V${{yy+rowH-3-r}} Q${{x}} ${{yy+rowH-3}} ${{x+r}} ${{yy+rowH-3}} H${{x+w}} Z`;
      el('path',{{d:path,fill:t.pnl>=0?COL.pos:COL.neg,opacity:t.overnight?0.75:1}},svg);
      if(t.overnight){{ el('path',{{d:path,fill:'none',stroke:'#e8edf4','stroke-width':1,'stroke-dasharray':'3 2'}},svg); }}
      const l=el('text',{{x:m.l-6,y:yy+rowH/2+4,'text-anchor':'end',class:'axis',fill:COL.text}},svg); l.textContent=t.symbol+(t.dir==='short'?' (short)':''); l.setAttribute('fill',COL.text);
      const v=el('text',{{x:t.pnl>=0?x+w+6:x-6,y:yy+rowH/2+4,'text-anchor':t.pnl>=0?'start':'end',class:'bar-label'}},svg); v.textContent=fmt$(t.pnl);
      const hit=el('rect',{{x:0,y:yy,width:W,height:rowH,fill:'transparent'}},svg);
      hit.onmousemove=(e)=>{{ const q=pt(svg,e); showTip(tip,host,q.px,q.py,`<div><b>${{t.symbol}}</b> ${{t.dir}} ${{t.qty}} sh ${{t.overnight?'· <em>overnight</em>':''}}</div><div style="color:${{COL.muted}}">In ${{t.entry_label}} @ $${{t.entry.toFixed(2)}}</div><div style="color:${{COL.muted}}">Out ${{t.exit_label}} @ $${{t.exit.toFixed(2)}}</div><div>P&L <b>${{fmt$(t.pnl)}}</b> (${{fmtP(t.pnl_pct)}}) · held ${{(t.hold_min/60).toFixed(1)}} h</div>`); }};
      hit.onmouseleave=()=>hideTip(tip);
    }});
    const cap=el('text',{{x:W-m.r,y:H-6,'text-anchor':'end',class:'axis'}},svg); cap.textContent='dashed outline = held overnight';
  }})();

  // ---------------------------------------------------------------- trade table
  (function(){{
    const tb=document.querySelector('#tradeTable tbody'), foot=document.getElementById('tfoot'), cnt=document.getElementById('fCount');
    const fDay=document.getElementById('fDay'), fOut=document.getElementById('fOut'), fHold=document.getElementById('fHold'), fSym=document.getElementById('fSym');
    let sortK='exit_t', asc=true;
    function rows(){{ let T=D.trades.filter(t=>(!fDay.value||t.exit_date===fDay.value)&&(!fOut.value||(fOut.value==='win'?t.pnl>0:t.pnl<0))&&(!fHold.value||(fHold.value==='overnight'?t.overnight:!t.overnight))&&(!fSym.value||t.symbol.toLowerCase().includes(fSym.value.trim().toLowerCase())));
      T.sort((a,b)=>{{ let x=a[sortK], y=b[sortK]; if(typeof x==='string'&&typeof y==='string') return asc?x.localeCompare(y):y.localeCompare(x); return asc?x-y:y-x; }}); return T; }}
    function render(){{ const T=rows(); tb.innerHTML=T.map(t=>`<tr><td class="l">${{t.exit_label.split(',')[0]}}</td><td class="l"><b>${{t.symbol}}</b></td><td class="l">${{t.dir}}</td><td>${{t.qty}}</td><td class="l">${{t.entry_label.split(', ')[1]}}${{t.overnight?' <span style="color:#f5c15c">('+t.entry_label.split(',')[0]+')</span>':''}}</td><td>$${{t.entry.toFixed(2)}}</td><td class="l">${{t.exit_label.split(', ')[1]}}</td><td>$${{t.exit.toFixed(2)}}</td><td>${{t.hold_min>=60?(t.hold_min/60).toFixed(1)+' h':t.hold_min+' m'}}</td><td class="${{t.pnl>0?'pos':t.pnl<0?'neg':''}}">${{fmt$(t.pnl)}}</td><td class="${{t.pnl>0?'pos':t.pnl<0?'neg':''}}">${{fmtP(t.pnl_pct)}}</td></tr>`).join('');
      const sum=T.reduce((a,t)=>a+t.pnl,0), w=T.filter(t=>t.pnl>0).length; cnt.textContent=`${{T.length}} of ${{D.trades.length}} round trips · ${{w}} wins · net ${{fmt$(sum)}}`;
      foot.innerHTML=`<td class="l" colspan="9" style="color:#93a0b3">Total for current filter</td><td class="${{sum>0?'pos':sum<0?'neg':''}}"><b>${{fmt$(sum)}}</b></td><td></td>`;
      document.querySelectorAll('#tradeTable th').forEach(th=>{{ th.classList.toggle('sorted',th.dataset.k===sortK); th.classList.toggle('asc',th.dataset.k===sortK&&asc); }}); }}
    document.querySelectorAll('#tradeTable th').forEach(th=>th.onclick=()=>{{ if(sortK===th.dataset.k) asc=!asc; else {{sortK=th.dataset.k; asc=true;}} render(); }});
    [fDay,fOut,fHold].forEach(s=>s.onchange=render); fSym.oninput=render; render();
  }})();
}})();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    bundle = json.load(open(args.bundle))
    c = compute(bundle)
    out = render(c, bundle)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"wrote {args.out}: {len(c['trades'])} round trips, week P&L {money(c['week_pnl'])} ({pct(c['week_ret'])}), realized {money(c['realized'])}")
    for r in c["day_rows"]:
        print(f"  {r['date']}  equity {r['equity_adj']:9.2f}  day {r['day_pnl']:+8.2f}  realized {r['realized']:+8.2f}  trades {r['trades']}  fills {r['orders']}")
    if c["residual"]:
        print("  residual closes:", [(f["symbol"], f["qty"]) for f in c["residual"]])
    if c["still_open"]:
        print("  still open:", {k: sum(l["qty"] for l in v) for k, v in c["still_open"].items()})


if __name__ == "__main__":
    main()
