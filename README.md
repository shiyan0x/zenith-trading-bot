# 🤖 Zenith Trading Bot

### Real prices. Fake money. It never lies.

A rule-based **paper trading bot** that trades crypto using real live market data from Binance — but with fake money so nothing is at risk. Every trade, every fee, every loss is tracked honestly. Built to help you learn how automated trading works, not to make you rich.

---

## 🎯 What This Bot Can Do

### 1. 📡 Live Market Data (No API Key Needed)
- Pulls **real-time crypto prices** from Binance via one WebSocket stream per configured symbol
- Fetches **historical candlestick data** (OHLCV) via Binance REST API
- Streams the configured candle interval (15 minutes by default) with open, high, low, close, and volume
- Auto-reconnects if the WebSocket connection drops
- **Zero API keys required** — uses free public Binance endpoints

### 2. 🧠 Two Trading Strategies

#### EMA + VWAP + RSI trend

- Long signal: EMA 9 > EMA 21, price > session VWAP, and RSI is 50–70.
- A 2×ATR stop and 2:1 target are stored with the position.

#### RSI + Bollinger Bands mean reversion

- Long signal: ranging regime (ADX < 25), lower Bollinger Band, oversold RSI, and optional candle confirmation.
- Exits at the middle band, ATR stop, time stop, or regime change.

Both strategies can create short signals in research. The live bot is **spot by default**, so it executes long signals only. Enabling live shorts requires both futures fee mode and `trading.allow_short: true`.

### 3. 📊 Walk-Forward Backtesting
Before the bot trades live, it tests every strategy on **real historical data**:

- Downloads 90 days of real candles using the **same interval as live trading**
- Splits data: **70% for training**, **30% for testing** (unseen data)
- Runs each strategy on training data first to check if it works at all
- Then runs on the **unseen test data** — this is the honest score
- Applies fees, volatility-aware slippage, stop-distance sizing, and deterministic fills during backtesting
- **Only activates strategies that pass** on the test set
- If NO strategy passes, it says so honestly — never forces a pick
- Measures: win rate, total return, Sharpe ratio, max drawdown, profit factor

### 4. 💰 Paper Wallet (Fake Money, Real Accounting)
- Starts with **$10,000 USDT** (fake money)
- Tracks positions with real entry prices, quantities, and fees
- Calculates **unrealized PnL** on open positions using live prices
- Records every trade with entry price, exit price, net PnL, and fees
- Balance can go to zero — the bot doesn't prevent it, it shows it
- Losing trades close at the **real market price**, never rounded up
- Supports conservative 1× collateral accounting for optional paper-futures shorts

### 5. 💸 Realistic Fee & Slippage Model
Every single trade pays real costs — nothing is free:

- **Trading fees**: configurable maker/taker rates; market orders use the taker rate
- **Slippage simulation**: 5 basis points base, scaled up to 30 bps in volatile conditions
- **Volatility-adjusted slippage**: multiplied by market volatility so fast-moving markets have higher costs
- Backtests disable random fill jitter so results are reproducible

### 6. 📐 Kelly Criterion Position Sizing
The bot uses math to decide **how much to bet on each trade**:

- Calculates **Kelly fraction**: `f* = W - (1 - W) / R`
  - W = win rate (e.g., 55% of trades win)
  - R = risk-reward ratio (avg win / avg loss)
- Uses **Half-Kelly** (0.5 × f*) — standard practice among pro traders
  - Keeps ~75% of growth rate with much less drawdown
- Calculates separately for each strategy/symbol pair
- Uses the configured 1% bootstrap risk only until it has 10 completed trades
- If Kelly ≤ 0 (no edge), position size = 0 → **stops trading automatically**
- Sizes from actual entry-to-stop distance; the configured 2% limit is a maximum loss-at-stop, not merely a notional cap

### 7. 🛡️ Risk Management & Circuit Breaker
The safety net that prevents the bot from losing everything:

- **Max drawdown limit**: If equity drops 15% from its peak → STOP all trading
- **Per-trade risk cap**: Never risk more than 2% of equity on a single trade
- **Circuit breaker system**:
  1. Stops all trading immediately when triggered
  2. Closes all open positions at market price
  3. Logs what happened and why
  4. Waits for a 60-minute cooldown period
  5. Re-runs out-of-sample validation after cooldown and resumes only if a strategy passes
- Tracks full history of all circuit breaker triggers

### 8. 📋 Trade Logging
Every trade is permanently logged to disk:

- Saves to `data/logs/` as structured trade records
- Logs: symbol, side, entry/exit prices, quantity, fees, slippage, net PnL
- Maintains running balance after each trade
- Also logs to `data/logs/bot.log` with timestamps for debugging

### 9. 🖥️ Live Web Dashboard
A premium sidebar-based web dashboard at `http://localhost:5000` with **8 views**:

| View | What It Shows |
|------|---------------|
| **📊 Overview** | Account equity, cash, win rate, max drawdown, goal progress bar, live equity curve, open positions summary |
| **⚡ Positions** | Detailed table of all open positions — symbol, side, quantity, entry price, current price, unrealized PnL, % change, time held |
| **🎯 Episodes** | Trading runs visualized as colored bars (green = hit goal, red = blowup, amber = running). Finished runs list with PnL |
| **📈 Evolution** | Bot's generation counter, best Sharpe ratio, total return, best strategy, full equity history chart, win/loss donut chart |
| **🧠 Strategies** | Backtest scoreboard — each strategy's pass/fail status, trade count, win rate, return, Sharpe ratio, max drawdown |
| **🌍 World** | Live market prices for all tracked symbols, risk panel with drawdown meter, Kelly fraction, position sizing, total fees, circuit breaker status |
| **📚 Lessons** | Derived insights — net PnL summary, biggest win/loss analysis, fee impact, win rate commentary, honesty reminders |
| **💱 Trades** | Complete trade history log with numbered entries — symbol, side, entry, exit, net PnL, fees, balance after |

**Dashboard features:**
- Real-time updates via WebSocket (SocketIO) — data refreshes every 3 seconds
- SPA navigation — smooth animated view switching, no page reloads
- Warm dark theme with glassmorphism effects and gradient accents
- Mobile responsive — sidebar collapses to hamburger menu on small screens
- Canvas-drawn equity curves with gradient fills and animated dots

### 10. 🔄 Full Trading Loop
The bot's main loop runs continuously:

1. **Start dashboard** on localhost:5000
2. **Fetch live prices for every configured symbol** to verify Binance connection
3. **Run same-timeframe backtests** on all strategy/symbol pairs with real historical data
4. **Filter strategies** — only keep ones that pass on unseen test data
5. **Stream live candles for every configured symbol** via Binance WebSocket
6. **On each closed candle**:
   - Check risk manager → stop if circuit breaker active
   - Check drawdown → close everything if limit exceeded
   - Feed candle to all active strategies
   - Let only the owning strategy check a position's exit
   - Check entry signals (if no position)
   - Calculate position size from Kelly and the stop distance
   - Execute trade through order engine (with fees + slippage)
   - Update dashboard state
7. **Push updates** to the dashboard every 3 seconds

---

## 📁 Project Structure

```
config/
  settings.json              — All tunable parameters (balance, fees, risk, strategies)

src/
  main.py                    — Entry point, trading loop orchestrator
  
  core/
    market_feed.py           — Binance WebSocket + REST API (live & historical prices)
    paper_wallet.py          — Fake money wallet, positions, equity tracking
    fee_model.py             — Trading fees + slippage + funding fee simulation
    order_engine.py          — Market buy/sell execution with fee/slippage application
    trade_logger.py          — Persistent trade logging to disk
  
  strategies/
    base_strategy.py         — Abstract base class all strategies implement
    ema_vwap_rsi.py          — EMA + VWAP + RSI trend strategy
    mean_reversion.py        — RSI + Bollinger Bands mean-reversion strategy
    research/                — standalone strategy research tools
  
  brain/
    backtester.py            — Walk-forward backtester on real historical data
    kelly_sizer.py           — Fractional Kelly Criterion position sizing
    risk_manager.py          — Drawdown circuit breaker + cooldown system
  
  dashboard/
    server.py                — Flask + SocketIO server with REST API endpoints
    index.html               — Dashboard UI (sidebar SPA, 8 views, warm dark theme)
    dashboard.js             — Real-time rendering engine (charts, tables, nav)

data/
  logs/                      — Trade logs and bot.log
tests/                       — regression tests for accounting, risk, and backtesting
```

---

## 🚀 Quick Start

```bash
# Install dependencies
python -m pip install -r requirements.txt

# Run the bot
python src/main.py
```

Then open **http://localhost:5000** in your browser to see the dashboard.

Run the offline regression suite with:

```bash
python -m unittest discover -s tests -v
```

---

## ⚙️ Configuration

All settings are in `config/settings.json`:

| Setting | Default | Description |
|---------|---------|-------------|
| `starting_balance` | $10,000 | Fake money to start with |
| `symbols` | BTCUSDT, ETHUSDT | Crypto pairs to trade |
| `timeframe` | 15m | Candle interval for both live trading and validation |
| `spot_maker/taker` | 0.1% | Trading fee rates |
| `base_bps` | 5 | Base slippage in basis points |
| `max_drawdown_pct` | 15% | Circuit breaker threshold |
| `max_risk_per_trade_pct` | 2% | Max risk per single trade |
| `max_notional_pct` | 100% | Maximum 1× notional exposure |
| `kelly_fraction` | 0.5 | Half-Kelly for position sizing |
| `history_days` | 90 | Days of historical data for backtesting |
| `train_ratio` | 70% | Backtest train/test split |
| `min_sharpe` | 0.5 | Minimum Sharpe ratio to pass backtest |
| `cooldown_minutes` | 60 | Circuit breaker cooldown period |

---

## 📦 Dependencies

```
aiohttp          — async HTTP for Binance REST API
websockets       — WebSocket client for live price streaming
flask            — web server for the dashboard
flask-socketio   — real-time WebSocket updates to the browser
```

---

## ⚠️ Honest Disclaimer

**This is a simulation, not a money machine.**

- The EMA/VWAP/RSI and RSI/Bollinger strategies are **textbook indicator combinations** with no proven long-term edge in efficient markets
- The backtester may find periods where they work, but **past performance does not predict future results**
- Real trading has more slippage, emotional pressure, exchange outages, and real financial risk
- This bot is built to **show you the truth** about automated trading — including when it loses
- It can lose everything. That honesty is the whole point.

---

*Built following the [Fabrichhhhhh Guide](bot%20guide/).*
