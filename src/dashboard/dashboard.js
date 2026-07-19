/**
 * dashboard.js — Sidebar-based SPA dashboard for the Zenith Trading Bot.
 *
 * Connects via SocketIO and renders 8 views:
 *   Overview, Positions, Episodes, Evolution, Strategies, World, Lessons, Trades
 */

// ─── SocketIO Connection ───
const socket = io();

// ─── State ───
let equityCurve = [];
const MAX_EQUITY_POINTS = 200;
let currentView = 'overview';
let lastState = {};
let episodeData = [];

// ─── Formatters ───
function formatUSD(val) {
    if (val === null || val === undefined) return '—';
    return '$' + Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatPct(val) {
    if (val === null || val === undefined) return '—';
    const sign = val >= 0 ? '+' : '';
    return sign + Number(val).toFixed(2) + '%';
}

function formatPnL(val) {
    if (val === null || val === undefined) return '—';
    const sign = val >= 0 ? '+' : '';
    return sign + formatUSD(val);
}

function pnlClass(val) {
    if (val > 0) return 'positive';
    if (val < 0) return 'negative';
    return 'neutral';
}

function timeAgo(ts) {
    if (!ts) return '—';
    const seconds = Math.floor(Date.now() / 1000 - ts);
    if (seconds < 60) return seconds + 's ago';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
    return Math.floor(seconds / 86400) + 'd ago';
}

// ═══════════════════════════════════════════════════
// NAVIGATION — SPA View Switching
// ═══════════════════════════════════════════════════
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    const hamburger = document.getElementById('hamburger-btn');

    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const view = item.dataset.view;
            switchView(view);
            // Close mobile sidebar
            sidebar.classList.remove('open');
            overlay.classList.remove('open');
        });
    });

    // Mobile hamburger
    hamburger.addEventListener('click', () => {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('open');
    });

    overlay.addEventListener('click', () => {
        sidebar.classList.remove('open');
        overlay.classList.remove('open');
    });
}

function switchView(viewName) {
    currentView = viewName;

    // Update nav active state
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.view === viewName);
    });

    // Show/hide sections
    document.querySelectorAll('.view-section').forEach(section => {
        section.classList.toggle('active', section.id === 'view-' + viewName);
    });

    // Re-render the active view with latest data
    if (lastState && Object.keys(lastState).length > 0) {
        renderView(viewName, lastState);
    }
}

function renderView(view, state) {
    switch (view) {
        case 'overview': renderOverview(state); break;
        case 'positions': renderPositions(state); break;
        case 'episodes': renderEpisodes(state); break;
        case 'evolution': renderEvolution(state); break;
        case 'strategies': renderStrategies(state); break;
        case 'world': renderWorld(state); break;
        case 'lessons': renderLessons(state); break;
        case 'trades': renderTrades(state); break;
    }
}

// ═══════════════════════════════════════════════════
// CHART — Canvas Equity Curve
// ═══════════════════════════════════════════════════
function drawEquityChart(canvasId, data) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !canvas.parentElement) return;
    const ctx = canvas.getContext('2d');

    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = rect.width + 'px';
    canvas.style.height = rect.height + 'px';
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const pad = { top: 20, right: 20, bottom: 30, left: 75 };

    ctx.clearRect(0, 0, w, h);

    if (data.length < 2) {
        ctx.fillStyle = '#5e5a73';
        ctx.font = '14px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('Waiting for data...', w / 2, h / 2);
        return;
    }

    const plotW = w - pad.left - pad.right;
    const plotH = h - pad.top - pad.bottom;
    const minVal = Math.min(...data) * 0.999;
    const maxVal = Math.max(...data) * 1.001;
    const range = maxVal - minVal || 1;

    const toX = (i) => pad.left + (i / (data.length - 1)) * plotW;
    const toY = (v) => pad.top + plotH - ((v - minVal) / range) * plotH;

    // Grid
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
        const y = pad.top + (plotH / 4) * i;
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(w - pad.right, y);
        ctx.stroke();

        const val = maxVal - (range / 4) * i;
        ctx.fillStyle = '#5e5a73';
        ctx.font = '11px Inter, sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(formatUSD(val), pad.left - 10, y + 4);
    }

    // Area fill
    const startVal = data[0];
    const lastVal = data[data.length - 1];
    const isUp = lastVal >= startVal;

    ctx.beginPath();
    ctx.moveTo(toX(0), toY(data[0]));
    for (let i = 1; i < data.length; i++) {
        ctx.lineTo(toX(i), toY(data[i]));
    }
    ctx.lineTo(toX(data.length - 1), pad.top + plotH);
    ctx.lineTo(toX(0), pad.top + plotH);
    ctx.closePath();

    const gradColor = isUp ? '34, 197, 94' : '239, 68, 68';
    const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
    grad.addColorStop(0, `rgba(${gradColor}, 0.15)`);
    grad.addColorStop(1, `rgba(${gradColor}, 0.0)`);
    ctx.fillStyle = grad;
    ctx.fill();

    // Line
    ctx.beginPath();
    ctx.moveTo(toX(0), toY(data[0]));
    for (let i = 1; i < data.length; i++) {
        ctx.lineTo(toX(i), toY(data[i]));
    }
    ctx.strokeStyle = isUp ? '#22c55e' : '#ef4444';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Current dot with glow
    const lastX = toX(data.length - 1);
    const lastY = toY(lastVal);
    const dotColor = isUp ? '#22c55e' : '#ef4444';

    ctx.beginPath();
    ctx.arc(lastX, lastY, 8, 0, Math.PI * 2);
    ctx.fillStyle = isUp ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)';
    ctx.fill();

    ctx.beginPath();
    ctx.arc(lastX, lastY, 4, 0, Math.PI * 2);
    ctx.fillStyle = dotColor;
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,0.4)';
    ctx.lineWidth = 1.5;
    ctx.stroke();
}

// ─── Donut Chart ───
function drawDonut(canvasId, wins, losses, breakeven) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const size = 100;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    canvas.style.width = size + 'px';
    canvas.style.height = size + 'px';
    ctx.scale(dpr, dpr);

    const total = wins + losses + breakeven || 1;
    const cx = size / 2, cy = size / 2, r = 38, lineWidth = 10;

    const slices = [
        { val: wins, color: '#22c55e' },
        { val: losses, color: '#ef4444' },
        { val: breakeven, color: '#5e5a73' },
    ];

    let startAngle = -Math.PI / 2;
    slices.forEach(s => {
        const sweep = (s.val / total) * Math.PI * 2;
        ctx.beginPath();
        ctx.arc(cx, cy, r, startAngle, startAngle + sweep);
        ctx.strokeStyle = s.color;
        ctx.lineWidth = lineWidth;
        ctx.lineCap = 'round';
        ctx.stroke();
        startAngle += sweep;
    });

    // Center text
    ctx.fillStyle = '#eeedf5';
    ctx.font = '700 16px Inter';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(total, cx, cy - 6);
    ctx.fillStyle = '#5e5a73';
    ctx.font = '500 9px Inter';
    ctx.fillText('trades', cx, cy + 8);
}

// ═══════════════════════════════════════════════════
// VIEW RENDERERS
// ═══════════════════════════════════════════════════

// ─── OVERVIEW ───
function renderOverview(state) {
    const wallet = state.wallet || {};
    const stats = wallet.stats || {};
    const equity = wallet.equity || stats.starting_balance || 10000;
    const startBal = stats.starting_balance || 10000;
    const changePct = ((equity - startBal) / startBal) * 100;
    const changeAbs = equity - startBal;

    // Stat cards
    document.getElementById('ov-equity').textContent = formatUSD(equity);
    document.getElementById('ov-equity').className = 'stat-value ' + pnlClass(changePct);
    document.getElementById('ov-equity-change').textContent =
        formatPnL(changeAbs) + ' · ' + formatPct(changePct) + ' this run · from ' + formatUSD(startBal);

    document.getElementById('ov-cash').textContent = formatUSD(wallet.cash || startBal);
    const posCount = (wallet.positions || []).length;
    document.getElementById('ov-open-count').textContent = posCount + ' open position' + (posCount !== 1 ? 's' : '');

    // Nav badge
    document.getElementById('nav-pos-count').textContent = posCount;

    const winRate = stats.win_rate_pct;
    document.getElementById('ov-winrate').textContent = winRate !== undefined ? winRate.toFixed(1) + '%' : '—';
    const totalTrades = stats.total_trades || 0;
    document.getElementById('ov-trade-summary').textContent =
        totalTrades + ' trades (' + (stats.winning_trades || 0) + 'W / ' + (stats.losing_trades || 0) + 'L)';
    document.getElementById('nav-trade-count').textContent = totalTrades;

    const dd = wallet.drawdown_pct || 0;
    document.getElementById('ov-drawdown').textContent = dd.toFixed(1) + '%';
    document.getElementById('ov-drawdown').className = 'stat-value ' + (dd > 10 ? 'negative' : dd > 5 ? 'warning' : 'neutral');
    document.getElementById('ov-dd-status').textContent = dd > 15 ? '⚠️ BREAKER LIMIT' : dd > 10 ? '⚠️ Warning zone' : 'Within limits';

    // Goal Progress
    const goalTarget = 500;
    const goalProgress = Math.max(0, Math.min(100, (changeAbs / goalTarget) * 100));
    document.getElementById('ov-goal-pct').textContent = goalProgress.toFixed(0) + '%';
    const goalFill = document.getElementById('ov-goal-fill');
    goalFill.style.width = goalProgress + '%';
    goalFill.className = 'goal-fill ' + (goalProgress > 60 ? 'on-track' : goalProgress > 30 ? 'behind' : 'danger');

    // Alert banner
    const alertBanner = document.getElementById('alert-banner');
    if (state.alert_message) {
        alertBanner.innerHTML = state.alert_message;
        alertBanner.classList.add('visible');
    } else if (totalTrades > 0) {
        const lastTrade = (state.recent_trades || []).slice(-1)[0];
        if (lastTrade) {
            const pnlStr = formatPnL(lastTrade.net_pnl);
            const pnlPctStr = formatPct(lastTrade.net_pnl_pct || ((lastTrade.net_pnl / startBal) * 100));
            alertBanner.innerHTML = `<strong>Latest trade:</strong> ${lastTrade.symbol || '—'} ${lastTrade.side || '—'} → ${pnlStr} (${pnlPctStr})`;
            alertBanner.classList.add('visible');
        }
    } else {
        alertBanner.classList.remove('visible');
    }

    // Equity curve
    equityCurve.push(equity);
    if (equityCurve.length > MAX_EQUITY_POINTS) equityCurve.shift();
    drawEquityChart('equity-chart', equityCurve);

    // Open positions summary
    renderPositionsMini(state);
}

function renderPositionsMini(state) {
    const container = document.getElementById('ov-positions-container');
    const positions = (state.wallet || {}).positions || [];

    if (positions.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="emoji">🔍</div><div class="empty-text">No open positions</div></div>';
        return;
    }

    let html = '<table class="data-table"><thead><tr><th>Symbol</th><th>Side</th><th>PnL</th><th>Entry</th></tr></thead><tbody>';
    positions.forEach(p => {
        const pnlCls = pnlClass(p.unrealized_pnl || 0);
        html += `<tr>
            <td><strong>${p.symbol}</strong></td>
            <td>${p.side === 'long' ? '🟢 Long' : '🔴 Short'}</td>
            <td class="mono ${pnlCls}">${formatPnL(p.unrealized_pnl)}</td>
            <td class="mono">${formatUSD(p.entry_price)}</td>
        </tr>`;
    });
    html += '</tbody></table>';
    container.innerHTML = html;
}

// ─── POSITIONS ───
function renderPositions(state) {
    const container = document.getElementById('pos-table-container');
    const positions = (state.wallet || {}).positions || [];

    if (positions.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="emoji">⚡</div><div class="empty-text">No open positions right now. The bot is waiting for a clear signal.</div></div>';
        return;
    }

    let html = '<table class="data-table"><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry Price</th><th>Current Price</th><th>Unrealized PnL</th><th>% Change</th><th>Opened</th></tr></thead><tbody>';

    positions.forEach(p => {
        const pnlCls = pnlClass(p.unrealized_pnl || 0);
        html += `<tr>
            <td><strong>${p.symbol}</strong></td>
            <td>${p.side === 'long' ? '🟢 Long' : '🔴 Short'}</td>
            <td class="mono">${Number(p.quantity).toFixed(6)}</td>
            <td class="mono">${formatUSD(p.entry_price)}</td>
            <td class="mono">${formatUSD(p.current_price)}</td>
            <td class="mono ${pnlCls}">${formatPnL(p.unrealized_pnl)}</td>
            <td class="mono ${pnlCls}">${formatPct(p.unrealized_pnl_pct)}</td>
            <td>${timeAgo(p.timestamp)}</td>
        </tr>`;
    });

    html += '</tbody></table>';
    container.innerHTML = html;
}

// ─── EPISODES ───
function deriveEpisodes(state) {
    const trades = state.recent_trades || [];
    const stats = (state.wallet || {}).stats || {};
    const startBal = stats.starting_balance || 10000;

    if (trades.length === 0) {
        // Create one "running" episode
        const equity = (state.wallet || {}).equity || startBal;
        return [{
            number: 1,
            status: 'running',
            trades: 0,
            pnl: equity - startBal,
            pnlPct: ((equity - startBal) / startBal) * 100,
            startBalance: startBal,
            endBalance: equity
        }];
    }

    // Group trades into episodes based on significant gaps or circuit breaker events
    const episodes = [];
    let currentEpisode = {
        number: 1,
        trades: [],
        startBalance: startBal,
        pnl: 0
    };

    trades.forEach((trade, i) => {
        currentEpisode.trades.push(trade);
        currentEpisode.pnl += (trade.net_pnl || 0);

        // Check if this should end the episode
        const isBlowup = (currentEpisode.startBalance + currentEpisode.pnl) <= currentEpisode.startBalance * 0.5;
        const isGoal = currentEpisode.pnl >= 500; // $500 goal

        if (isBlowup || isGoal || i === trades.length - 1) {
            const endBal = currentEpisode.startBalance + currentEpisode.pnl;
            episodes.push({
                number: currentEpisode.number,
                status: i === trades.length - 1 && !isBlowup && !isGoal ? 'running' : isGoal ? 'goal' : 'blowup',
                trades: currentEpisode.trades.length,
                pnl: currentEpisode.pnl,
                pnlPct: (currentEpisode.pnl / currentEpisode.startBalance) * 100,
                startBalance: currentEpisode.startBalance,
                endBalance: endBal
            });

            if (i < trades.length - 1) {
                currentEpisode = {
                    number: currentEpisode.number + 1,
                    trades: [],
                    startBalance: endBal,
                    pnl: 0
                };
            }
        }
    });

    return episodes;
}

function renderEpisodes(state) {
    const episodes = deriveEpisodes(state);
    episodeData = episodes;

    // Episode bars
    const barsContainer = document.getElementById('episode-bars-container');
    const numbersContainer = document.getElementById('episode-numbers');

    if (episodes.length === 0) {
        barsContainer.innerHTML = '';
        numbersContainer.innerHTML = '';
        return;
    }

    const maxPnl = Math.max(...episodes.map(e => Math.abs(e.pnlPct)), 10);
    let barsHtml = '';
    let numbersHtml = '';

    episodes.forEach(ep => {
        const height = Math.max(20, (Math.abs(ep.pnlPct) / maxPnl) * 100);
        barsHtml += `<div class="episode-bar ${ep.status}" style="height:${height}px;" title="Episode ${ep.number}: ${ep.status} (${formatPct(ep.pnlPct)})"></div>`;
        numbersHtml += `<div class="episode-number" style="flex:1;max-width:60px;">${ep.number}</div>`;
    });

    barsContainer.innerHTML = barsHtml;
    numbersContainer.innerHTML = numbersHtml;

    // Finished runs list
    const runsContainer = document.getElementById('finished-runs-container');
    const finishedEps = episodes.filter(e => e.status !== 'running');

    if (finishedEps.length === 0 && episodes.some(e => e.status === 'running')) {
        runsContainer.innerHTML = '<div class="empty-state"><div class="emoji">🏁</div><div class="empty-text">No completed runs yet. Episode ' + episodes[0].number + ' is in progress.</div></div>';
        return;
    }

    let runsHtml = '';
    [...finishedEps].reverse().forEach(ep => {
        runsHtml += `<div class="run-item">
            <span class="run-number">#${ep.number}</span>
            <span class="run-status ${ep.status}">${ep.status}</span>
            <span class="mono text-secondary" style="font-size:0.78rem;">${formatPnL(ep.pnl)}</span>
            <span class="run-expand">↗</span>
        </div>`;
    });

    // Also show running episode
    const running = episodes.find(e => e.status === 'running');
    if (running) {
        runsHtml = `<div class="run-item">
            <span class="run-number">#${running.number}</span>
            <span class="run-status running">running</span>
            <span class="mono text-secondary" style="font-size:0.78rem;">${formatPnL(running.pnl)} (${running.trades} trades)</span>
            <span class="run-expand">↗</span>
        </div>` + runsHtml;
    }

    runsContainer.innerHTML = runsHtml;
}

// ─── EVOLUTION ───
function renderEvolution(state) {
    const wallet = state.wallet || {};
    const stats = wallet.stats || {};
    const strategies = state.strategies || [];
    const equity = wallet.equity || stats.starting_balance || 10000;
    const startBal = stats.starting_balance || 10000;
    const changePct = ((equity - startBal) / startBal) * 100;

    // Stats
    document.getElementById('evo-generation').textContent = Math.max(1, Math.floor((stats.total_trades || 0) / 20) + 1);

    // Best Sharpe from strategies
    const sharpes = strategies.filter(s => s.sharpe_ratio).map(s => s.sharpe_ratio);
    document.getElementById('evo-sharpe').textContent = sharpes.length > 0 ? Math.max(...sharpes).toFixed(2) : '—';

    document.getElementById('evo-return').textContent = formatPct(changePct);
    document.getElementById('evo-return').className = 'stat-value ' + pnlClass(changePct);

    // Best strategy by win rate
    if (strategies.length > 0) {
        const best = strategies.reduce((a, b) => ((a.win_rate || 0) > (b.win_rate || 0) ? a : b));
        document.getElementById('evo-best-strat').textContent = best.strategy || '—';
        document.getElementById('evo-best-strat-sub').textContent = ((best.win_rate || 0) * 100).toFixed(1) + '% win rate';
    }

    // Full equity chart
    drawEquityChart('evolution-chart', equityCurve);

    // Donut
    const wins = stats.winning_trades || 0;
    const losses = stats.losing_trades || 0;
    const total = stats.total_trades || 0;
    const breakeven = Math.max(0, total - wins - losses);

    drawDonut('evo-donut', wins, losses, breakeven);

    const legend = document.getElementById('evo-donut-legend');
    legend.innerHTML = `
        <div><span class="donut-dot" style="background:#22c55e"></span> Winning Trades: ${wins}</div>
        <div><span class="donut-dot" style="background:#ef4444"></span> Losing Trades: ${losses}</div>
        <div><span class="donut-dot" style="background:#5e5a73"></span> Break Even: ${breakeven}</div>
    `;
}

// ─── STRATEGIES ───
function renderStrategies(state) {
    const container = document.getElementById('strat-table-container');
    const strategies = state.strategies || [];

    if (strategies.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="emoji">🔄</div><div class="empty-text">Running backtests on historical data...</div></div>';
        return;
    }

    let html = '<table class="data-table"><thead><tr><th>Strategy</th><th>Status</th><th>Trades</th><th>Win Rate</th><th>Return</th><th>Sharpe</th><th>Max DD</th></tr></thead><tbody>';

    strategies.forEach(s => {
        const statusBadge = s.passed
            ? '<span class="strat-badge passed">✅ Active</span>'
            : '<span class="strat-badge failed">❌ Disabled</span>';
        const retCls = pnlClass(s.total_return_pct || 0);
        html += `<tr>
            <td><strong>${s.strategy || '—'}</strong></td>
            <td>${statusBadge}</td>
            <td class="mono">${s.total_trades || 0}</td>
            <td class="mono">${((s.win_rate || 0) * 100).toFixed(1)}%</td>
            <td class="mono ${retCls}">${formatPct(s.total_return_pct)}</td>
            <td class="mono">${(s.sharpe_ratio || 0).toFixed(2)}</td>
            <td class="mono">${(s.max_drawdown_pct || 0).toFixed(1)}%</td>
        </tr>`;
    });

    html += '</tbody></table>';
    container.innerHTML = html;
}

// ─── WORLD ───
function renderWorld(state) {
    const prices = state.prices || {};
    const risk = state.risk || {};
    const wallet = state.wallet || {};
    const kelly = state.kelly || {};

    // Market tiles
    const tilesContainer = document.getElementById('world-tiles-container');
    const symbols = Object.keys(prices);

    if (symbols.length === 0) {
        tilesContainer.innerHTML = '<div class="stat-card"><div class="stat-label">Waiting for price data...</div><div class="stat-value neutral">—</div></div>';
    } else {
        let tilesHtml = '';
        symbols.forEach(sym => {
            const price = prices[sym];
            const displayName = sym.replace('USDT', '');
            const pair = displayName + ' / USDT';
            tilesHtml += `<div class="market-tile">
                <div>
                    <div class="market-symbol">${displayName}</div>
                    <div class="market-pair">${pair}</div>
                </div>
                <div class="market-price">${formatUSD(price)}</div>
            </div>`;
        });
        tilesContainer.innerHTML = tilesHtml;
    }

    // Risk panel
    const dd = wallet.drawdown_pct || 0;
    const maxDd = risk.max_drawdown_pct || 15;

    document.getElementById('world-risk-dd').textContent = dd.toFixed(1) + '% / ' + maxDd + '%';

    const fill = document.getElementById('world-risk-fill');
    const fillPct = Math.min((dd / maxDd) * 100, 100);
    fill.style.width = fillPct + '%';
    fill.style.background = fillPct > 80 ? 'var(--red)' : fillPct > 50 ? 'var(--amber)' : 'var(--green)';

    document.getElementById('world-kelly').textContent =
        kelly.raw_kelly !== undefined ? (kelly.raw_kelly * 100).toFixed(2) + '%' : '—';
    document.getElementById('world-pos-size').textContent =
        kelly.fractional_kelly !== undefined ? (kelly.fractional_kelly * 100).toFixed(2) + '% (half-Kelly)' : '—';
    document.getElementById('world-fees').textContent =
        formatUSD((wallet.stats || {}).total_fees_paid || 0);

    // Circuit breaker
    const breakerAlert = document.getElementById('world-breaker-alert');
    if (risk.breaker_active) {
        breakerAlert.classList.add('active');
        document.getElementById('world-breaker-reason').textContent = risk.breaker_reason || 'Unknown';
    } else {
        breakerAlert.classList.remove('active');
    }

    // ─── News Feed ───
    renderNewsFeed(state);
}

function renderNewsFeed(state) {
    const news = state.news || [];
    const sentiment = state.sentiment || {};

    // Sentiment meter
    const sentLabel = document.getElementById('news-sentiment-label');
    const sentFill = document.getElementById('news-sentiment-fill');
    const label = sentiment.overall_label || 'neutral';
    const score = sentiment.overall_score || 0;

    sentLabel.textContent = label.toUpperCase();
    sentLabel.className = 'sentiment-label ' + label;

    // Bar: score goes from -1 to +1, center is 50%
    // Bullish: fill goes right from center, green
    // Bearish: fill goes left from center, red
    const barWidth = Math.abs(score) * 50; // max 50% each side
    if (score >= 0) {
        sentFill.style.left = '50%';
        sentFill.style.width = barWidth + '%';
        sentFill.style.background = score > 0.2
            ? 'linear-gradient(90deg, var(--green), #4ade80)'
            : 'var(--amber)';
    } else {
        sentFill.style.left = (50 - barWidth) + '%';
        sentFill.style.width = barWidth + '%';
        sentFill.style.background = score < -0.2
            ? 'linear-gradient(90deg, #f87171, var(--red))'
            : 'var(--amber)';
    }

    // Blocking alert
    const blockAlert = document.getElementById('news-blocking-alert');
    if (sentiment.is_blocking) {
        blockAlert.classList.add('active');
    } else {
        blockAlert.classList.remove('active');
    }

    // News list
    const container = document.getElementById('news-list-container');
    if (news.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="emoji">📰</div><div class="empty-text">Fetching news...</div></div>';
        return;
    }

    let html = '';
    news.forEach(item => {
        const age = item.age_minutes || 0;
        let ageText;
        if (age < 1) ageText = 'just now';
        else if (age < 60) ageText = Math.round(age) + 'm ago';
        else if (age < 1440) ageText = Math.round(age / 60) + 'h ago';
        else ageText = Math.round(age / 1440) + 'd ago';

        const sentClass = item.sentiment_label || 'neutral';
        const titleHtml = item.url
            ? `<a href="${item.url}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a>`
            : escapeHtml(item.title);

        html += `<div class="news-item">
            <div class="news-dot ${sentClass}"></div>
            <div class="news-content">
                <div class="news-title">${titleHtml}</div>
                <div class="news-meta">
                    <span class="news-source">${escapeHtml(item.source || '')}</span>
                    <span>·</span>
                    <span>${ageText}</span>
                    <span>·</span>
                    <span style="color: var(--${sentClass === 'bullish' ? 'green' : sentClass === 'bearish' ? 'red' : 'amber'})">${(item.sentiment_score >= 0 ? '+' : '') + (item.sentiment_score || 0).toFixed(2)}</span>
                </div>
            </div>
        </div>`;
    });
    container.innerHTML = html;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ─── LESSONS ───
function deriveLessons(state) {
    const trades = state.recent_trades || [];
    const wallet = state.wallet || {};
    const stats = wallet.stats || {};
    const lessons = [];

    if (trades.length === 0) {
        lessons.push({
            icon: '🌱',
            title: 'Just Getting Started',
            body: 'No trades yet. The bot is analyzing market conditions and waiting for a clear signal. Patience is a strategy.'
        });
        return lessons;
    }

    // Total PnL lesson
    const totalPnl = trades.reduce((sum, t) => sum + (t.net_pnl || 0), 0);
    if (totalPnl > 0) {
        lessons.push({
            icon: '💰',
            title: 'Net Positive So Far',
            body: `The bot has made ${formatUSD(totalPnl)} in net profit across ${trades.length} trades. Remember: past performance doesn't predict future results, especially in a simulation.`
        });
    } else {
        lessons.push({
            icon: '📉',
            title: 'Net Negative — But Honest',
            body: `The bot is down ${formatUSD(Math.abs(totalPnl))} across ${trades.length} trades. This is the truth — most simple strategies lose money. The bot doesn't hide this.`
        });
    }

    // Biggest win
    const biggestWin = trades.reduce((max, t) => (t.net_pnl || 0) > (max.net_pnl || 0) ? t : max, { net_pnl: 0 });
    if (biggestWin.net_pnl > 0) {
        lessons.push({
            icon: '🏆',
            title: 'Biggest Win: ' + formatUSD(biggestWin.net_pnl),
            body: `${biggestWin.symbol || 'Unknown'} ${biggestWin.side || ''} trade. Entry ${formatUSD(biggestWin.entry_price)} → Exit ${formatUSD(biggestWin.exit_price)}. Don't chase this result — it may be luck.`
        });
    }

    // Biggest loss
    const biggestLoss = trades.reduce((min, t) => (t.net_pnl || 0) < (min.net_pnl || 0) ? t : min, { net_pnl: 0 });
    if (biggestLoss.net_pnl < 0) {
        lessons.push({
            icon: '🩸',
            title: 'Biggest Loss: ' + formatUSD(biggestLoss.net_pnl),
            body: `${biggestLoss.symbol || 'Unknown'} ${biggestLoss.side || ''} trade. This is why risk management matters. The circuit breaker exists to prevent catastrophic losses.`
        });
    }

    // Fees lesson
    const totalFees = stats.total_fees_paid || 0;
    if (totalFees > 0) {
        lessons.push({
            icon: '🏦',
            title: 'Fees Ate ' + formatUSD(totalFees),
            body: `Trading fees are a silent drain. Even at low rates, they compound over time. Real exchanges charge even more for small accounts.`
        });
    }

    // Win rate lesson
    const winRate = stats.win_rate_pct;
    if (winRate !== undefined) {
        if (winRate > 55) {
            lessons.push({
                icon: '🎯',
                title: winRate.toFixed(1) + '% Win Rate',
                body: `Above 50% sounds good, but edge comes from the ratio of average win to average loss, not just win rate. A 60% win rate with small wins and big losses still loses.`
            });
        } else if (winRate < 45) {
            lessons.push({
                icon: '🎲',
                title: winRate.toFixed(1) + '% Win Rate — Below Half',
                body: `Losing more often than winning. This is common for trend-following strategies that rely on a few big wins. Check if the average win covers the losses.`
            });
        }
    }

    // Honesty lesson (always present)
    lessons.push({
        icon: '🪞',
        title: 'This Is Fake Money',
        body: 'Everything here uses real market prices but simulated trades. Real trading adds emotional pressure, exchange outages, worse slippage, and real financial risk. Never trust a backtest blindly.'
    });

    return lessons;
}

function renderLessons(state) {
    const container = document.getElementById('lessons-container');
    const lessons = deriveLessons(state);

    let html = '';
    lessons.forEach(l => {
        html += `<div class="lesson-card">
            <div class="lesson-icon">${l.icon}</div>
            <div class="lesson-title">${l.title}</div>
            <div class="lesson-body">${l.body}</div>
        </div>`;
    });

    container.innerHTML = html;
}

// ─── TRADES ───
function renderTrades(state) {
    const container = document.getElementById('trades-table-container');
    const trades = state.recent_trades || [];

    if (trades.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="emoji">⏳</div><div class="empty-text">No trades yet — waiting for signals</div></div>';
        return;
    }

    let html = '<table class="data-table"><thead><tr><th>#</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th><th>Net PnL</th><th>Fees</th><th>Balance After</th></tr></thead><tbody>';

    [...trades].reverse().forEach((t, i) => {
        const pnlCls = pnlClass(t.net_pnl || 0);
        html += `<tr>
            <td class="text-muted">${trades.length - i}</td>
            <td><strong>${t.symbol || '—'}</strong></td>
            <td>${t.side === 'long' ? '🟢' : '🔴'} ${t.side || '—'}</td>
            <td class="mono">${formatUSD(t.entry_price)}</td>
            <td class="mono">${formatUSD(t.exit_price)}</td>
            <td class="mono ${pnlCls}">${formatPnL(t.net_pnl)}</td>
            <td class="mono">${formatUSD(t.total_fees)}</td>
            <td class="mono">${formatUSD(t.balance_after)}</td>
        </tr>`;
    });

    html += '</tbody></table>';
    container.innerHTML = html;
}

// ═══════════════════════════════════════════════════
// SIDEBAR STATUS SYNC
// ═══════════════════════════════════════════════════
function updateSidebarStatus(state) {
    const statusEl = document.getElementById('sidebar-status');
    const risk = state.risk || {};

    if (risk.breaker_active) {
        statusEl.className = 'profile-status stopped';
        statusEl.innerHTML = '<span class="pulse-dot"></span>Breaker Active';
    } else if (state.status === 'running') {
        statusEl.className = 'profile-status live';
        statusEl.innerHTML = '<span class="pulse-dot"></span>Paper Trading';
    } else {
        statusEl.className = 'profile-status stopped';
        statusEl.innerHTML = '<span class="pulse-dot"></span>Stopped';
    }
}

// ═══════════════════════════════════════════════════
// SOCKET EVENTS
// ═══════════════════════════════════════════════════
socket.on('connect', () => {
    console.log('[Dashboard] Connected to server');
    socket.emit('request_state');
});

socket.on('state_update', (state) => {
    lastState = state;
    updateSidebarStatus(state);
    syncTimeframePill(state);
    renderView(currentView, state);
});

socket.on('timeframe_changed', (data) => {
    console.log('[Dashboard] Timeframe changed to:', data.timeframe);
    showToast(`Switched to ${data.timeframe} timeframe. Reconnecting...`);
    // Update active pill
    document.querySelectorAll('.tf-pill').forEach(p => {
        p.classList.toggle('active', p.dataset.tf === data.timeframe);
    });
});

socket.on('disconnect', () => {
    console.log('[Dashboard] Disconnected');
    const statusEl = document.getElementById('sidebar-status');
    statusEl.className = 'profile-status stopped';
    statusEl.innerHTML = '<span class="pulse-dot"></span>Disconnected';
});

// Poll every 3 seconds
setInterval(() => {
    if (socket.connected) {
        socket.emit('request_state');
    }
}, 3000);

// ═══════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initTimeframeSelector();
});

// ═══════════════════════════════════════════════════
// TIMEFRAME SELECTOR
// ═══════════════════════════════════════════════════
function initTimeframeSelector() {
    const pills = document.querySelectorAll('.tf-pill');
    pills.forEach(pill => {
        pill.addEventListener('click', () => {
            const tf = pill.dataset.tf;
            const currentActive = document.querySelector('.tf-pill.active');
            if (currentActive && currentActive.dataset.tf === tf) return; // already active

            // Optimistic UI update
            pills.forEach(p => p.classList.remove('active'));
            pill.classList.add('active');

            // Show toast
            showToast(`Switching to ${tf} timeframe...`);

            // Send to server
            socket.emit('change_timeframe', { timeframe: tf });
        });
    });
}

function syncTimeframePill(state) {
    const tf = state.timeframe;
    if (!tf) return;
    document.querySelectorAll('.tf-pill').forEach(p => {
        p.classList.toggle('active', p.dataset.tf === tf);
    });
}

let toastTimer = null;
function showToast(message) {
    const toast = document.getElementById('tf-toast');
    const text = document.getElementById('tf-toast-text');
    text.textContent = message;
    toast.classList.add('visible');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('visible'), 3000);
}

// Resize charts on window resize
window.addEventListener('resize', () => {
    if (currentView === 'overview') drawEquityChart('equity-chart', equityCurve);
    if (currentView === 'evolution') drawEquityChart('evolution-chart', equityCurve);
});
