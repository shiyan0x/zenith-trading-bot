# 🤖 Zenith Trading Bot

### Real prices. Fake money. It never lies.

An AI-powered **paper trading bot** that trades crypto using real live market data from Binance — but with fake money so nothing is at risk. Every trade, every fee, every loss is tracked honestly. Built to help you learn how automated trading works, not to make you rich.

---

## 🎯 What This Bot Can Do

### 1. 📡 Live Market Data (No API Key Needed)
- Pulls **real-time crypto prices** from Binance via WebSocket (BTC, ETH)
- Fetches **historical candlestick data** (OHLCV) via Binance REST API
- Streams live 1-minute candles with open, high, low, close, and volume
- Auto-reconnects if the WebSocket connection drops
- **Zero API keys required** — uses free public Binance endpoints

### 2. 🧠 Three Trading Strategies
The bot comes with 3 built-in technical analysis strategies:

#### SMA Crossover (Simple Moving Average)
- Tracks a **short-term average** (10 candles) and a **long-term average** (30 candles)
- **Buys** when the short SMA crosses above the long SMA (uptrend signal)
- **Sells** when the short SMA crosses below the long SMA (downtrend signal)
- Works best in trending markets, gets chopped up in sideways markets

#### RSI Mean Reversion (Relative Strength Index)
- Calculates RSI (0–100 scale) using Wilder smoothing method over 14 candles
- **Buys** when RSI drops below 30 (oversold — price dropped too much, might bounce)
- **Sells** when RSI rises above 70 (overbought — price rose too much, might pull back)
- Works when markets bounce, but in a real crash "oversold" can get more oversold

#### Donchian Channel Breakout
- Looks at the highest high and lowest low of the last 20 candles (the "channel")
- **Buys** when price breaks above the channel (new momentum signal)
- **Sells** when price breaks below the channel low
- Many false signals in choppy markets — relies on a few big wins to cover many small losses

### 3. 📊 Walk-Forward Backtesting
Before the bot trades live, it tests every strategy on **real historical data**:

- Downloads 90 days of real hourly candles from Binance
- Splits data: **70% for training**, **30% for testing** (unseen data)
- Runs each strategy on training data first to check if it works at all
- Then runs on the **unseen test data** — this is the honest score
- Applies full fees + slippage during backtesting (no cheating)
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

### 5. 💸 Realistic Fee & Slippage Model
Every single trade pays real costs — nothing is free:

- **Trading fees**: 0.1% maker/taker for spot (Binance VIP 0 rates, verified July 2026)
- **Slippage simulation**: 5 basis points base, scaled up to 30 bps in volatile conditions
- **Volatility-adjusted slippage**: multiplied by market volatility so fast-moving markets have higher costs
- **Funding fees**: Available for futures mode (disabled by default, spot trading)
- All fee rates sourced from official Binance fee schedule

### 6. 📐 Kelly Criterion Position Sizing
The bot uses math to decide **how much to bet on each trade**:

- Calculates **Kelly fraction**: `f* = W - (1 - W) / R`
  - W = win rate (e.g., 55% of trades win)
  - R = risk-reward ratio (avg win / avg loss)
- Uses **Half-Kelly** (0.5 × f*) — standard practice among pro traders
  - Keeps ~75% of growth rate with much less drawdown
- Recalculates every 20 trades based on recent performance
- If Kelly ≤ 0 (no edge), position size = 0 → **stops trading automatically**
- Requires minimum 10 trades before calculating (uses 1% until then)
- Never risks more than 2% of equity per trade (hard cap)

### 7. 🛡️ Risk Management & Circuit Breaker
The safety net that prevents the bot from losing everything:

- **Max drawdown limit**: If equity drops 15% from its peak → STOP all trading
- **Per-trade risk cap**: Never risk more than 2% of equity on a single trade
- **Circuit breaker system**:
  1. Stops all trading immediately when triggered
  2. Closes all open positions at market price
  3. Logs what happened and why
  4. Waits for a 60-minute cooldown period
  5. Only resumes when conditions improve
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
| **📚 Lessons** | AI-derived insights — net PnL summary, biggest win/loss analysis, fee impact, win rate commentary, honesty reminders |
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
2. **Fetch live price** to verify Binance connection
3. **Run backtests** on all strategies with real historical data
4. **Filter strategies** — only keep ones that pass on unseen test data
5. **Stream live candles** via Binance WebSocket
6. **On each closed candle**:
   - Check risk manager → stop if circuit breaker active
   - Check drawdown → close everything if limit exceeded
   - Feed candle to all active strategies
   - Check exit signals first (if holding a position)
   - Check entry signals (if no position)
   - Calculate position size via Kelly criterion
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
    sma_crossover.py         — SMA Crossover strategy (short vs long moving average)
    rsi_mean_revert.py       — RSI Mean Reversion strategy (oversold/overbought)
    breakout.py              — Donchian Channel Breakout strategy (channel high/low)
  
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
  history/                   — Cached historical candle data
```

---

## 🚀 Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot
python src/main.py
```

Then open **http://localhost:5000** in your browser to see the dashboard.

---

## ⚙️ Configuration

All settings are in `config/settings.json`:

| Setting | Default | Description |
|---------|---------|-------------|
| `starting_balance` | $10,000 | Fake money to start with |
| `symbols` | BTCUSDT, ETHUSDT | Crypto pairs to trade |
| `timeframe` | 1m | Candle interval for live trading |
| `spot_maker/taker` | 0.1% | Trading fee rates |
| `base_bps` | 5 | Base slippage in basis points |
| `max_drawdown_pct` | 15% | Circuit breaker threshold |
| `max_risk_per_trade_pct` | 2% | Max risk per single trade |
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

- The strategies (SMA crossover, RSI mean-reversion, Donchian breakout) are **textbook indicators** with no proven long-term edge in efficient markets
- The backtester may find periods where they work, but **past performance does not predict future results**
- Real trading has more slippage, emotional pressure, exchange outages, and real financial risk
- This bot is built to **show you the truth** about automated trading — including when it loses
- It can lose everything. That honesty is the whole point.

---

*Built following the [Fabrichhhhhh Guide](bot%20guide/).*
