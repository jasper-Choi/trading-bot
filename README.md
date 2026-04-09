# Trading Bot — Paper Trading with Auto Market Scan

A paper trading bot that scans the entire Upbit KRW market every 15 minutes, applies a volatility breakout + trend-following strategy, and manages up to 3 simultaneous positions with ATR-based stops and a daily loss limit.

## Features

- **Full market scan**: Ranks all KRW pairs by 24h volume, selects top 30 each cycle
- **Strategy**: Volatility breakout (`close > open + K × prev_range`) + EMA uptrend filter
- **Signal scoring** (0–3 pts): RSI oversold, volume surge, EMA golden cross
- **Risk management**: ATR stop-loss, trailing stop, 6-hour time exit, daily −3% loss limit
- **KOSDAQ screener**: Gap-up stock detection at market open (09:00–09:30 KST)
- **REST API**: FastAPI backend for position/trade/stats queries and bot control
- **Dashboard**: React + Vite frontend with PnL chart, position table, live logs

## Tech Stack

| Layer | Tech |
|---|---|
| Strategy engine | Python, pandas, NumPy |
| Market data | Upbit Public API |
| Stock data | pykrx (optional) |
| API backend | FastAPI, Uvicorn |
| Scheduler | schedule |
| Frontend | React 18, Vite, Recharts |

## Project Structure

```
trading-bot/
├── main.py              # CLI entry point + 15-min scheduler
├── config.py            # All tunable parameters
├── requirements.txt
├── src/
│   ├── screener.py      # Top-30 KRW coin scan
│   ├── stock_screener.py# KOSDAQ gap-up scan
│   ├── data_fetcher.py  # Upbit 15m candle API
│   ├── strategy.py      # Indicators + entry signal + scoring
│   ├── position_manager.py
│   └── reporter.py
├── api/
│   ├── main.py          # FastAPI app
│   ├── models.py        # Pydantic response models
│   └── routers/         # bot / positions / trades / stats
└── frontend/            # Vite + React dashboard
```

## Quick Start

### Strategy bot (CLI)

```bash
pip install -r requirements.txt

python main.py            # Start scheduler (runs every 15 min)
python main.py run        # Run one cycle immediately
python main.py status     # Print current positions
python main.py history    # Print trade history
```

### API + Dashboard

```bash
# Backend
uvicorn api.main:app --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` — the dashboard auto-refreshes every 30 seconds.

### Environment variables (optional)

Copy `.env.example` to `.env`. The bot uses Upbit's public API and requires no keys for read-only paper trading. Keys are reserved for future live trading integration.

```bash
cp .env.example .env
```

## Configuration (`config.py`)

| Parameter | Default | Description |
|---|---|---|
| `TOP_COINS_COUNT` | 30 | Coins to scan per cycle |
| `MAX_POSITIONS` | 3 | Max simultaneous positions |
| `DAILY_LOSS_LIMIT_PCT` | 0.03 | Halt new entries at −3% daily loss |
| `INITIAL_CAPITAL_PER_COIN` | 500,000 KRW | Virtual capital per position |
| `K` | 0.6 | Volatility breakout coefficient |
| `MAX_HOLD_CANDLES` | 24 | Max hold time (24 × 15 min = 6 h) |
| `ATR_STOP_MULT` | 1.5 | Initial stop distance (× ATR) |
| `ATR_TRAIL_MULT` | 3.0 | Trailing stop distance (× ATR) |

## Deployment (Railway)

The `Procfile` is included for one-command Railway deployment:

```
web: uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

Push to GitHub, connect the repo in Railway, and set any required environment variables.

## Disclaimer

This project is for educational and paper trading purposes only. No real funds are used or at risk.
