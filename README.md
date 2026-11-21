# QuantScout NLP Sentiment Tracker

Institutional-grade live sentiment scanner for stocks using paid Alpaca (real-time quotes) and Tiingo (news), with VADER sentiment analysis.

## Features
- Real-time price & news sentiment
- BUY/SELL/HOLD signals
- Interactive Plotly charts
- Auto-refresh live mode
- Fallbacks for resilience

Deployed on Streamlit Cloud.

## Paper trading reports

Weekly interactive HTML reports on the Alpaca paper account live in `docs/`. Each one is a
single self-contained file (no CDN, no external fonts) that opens offline in any browser.

| Week | Window | Report | Data |
|---|---|---|---|
| 1 | Nov 9 – Nov 13, 2026 | `docs/paper-trading-week1-2026-11-09.html` | `docs/data/paper-week1-2026-11-09.json` |

Regenerate a report from its data bundle:

```bash
python3 scripts/weekly_paper_report.py \
  --bundle docs/data/paper-week1-2026-11-09.json \
  --out docs/paper-trading-week1-2026-11-09.html
```

The bundle is a JSON snapshot of the Alpaca order history (nested, so OCO legs are included),
hourly and daily portfolio history, non-trade activities (deposits and fees), SPY/QQQ daily
closes, and the narrative fields (`headline`, `day_notes`, `lessons`, `next_steps`) under `meta`.
Paper cash deposits are subtracted from the equity curve automatically.
