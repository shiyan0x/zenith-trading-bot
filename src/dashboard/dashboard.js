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
        case 'research': renderResearch(state); break;
        case 'comparison': renderComparison(state); break;
        case 'backtesting': renderBacktesting(state); break;
        case 'papertrading': renderPaperTrading(state); break;
        case 'monitoring': renderMonitoring(state); break;
        case 'controls': renderControls(state); break;
        case 'tfcompare': renderTimeframeComparison(state); break;
        case 'patterns': renderPatterns(state); break;
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
        alertBanner.textContent = state.alert_message;
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
        const safeUrl = safeExternalUrl(item.url);
        const titleHtml = safeUrl
            ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a>`
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

function safeExternalUrl(rawUrl) {
    if (!rawUrl) return '';
    try {
        const parsed = new URL(rawUrl, window.location.origin);
        return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : '';
    } catch (_) {
        return '';
    }
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
    } else if (state.status === 'backtesting') {
        statusEl.className = 'profile-status backtesting';
        statusEl.innerHTML = '<span class="pulse-dot"></span>Backtesting...';
    } else if (state.status === 'initializing') {
        statusEl.className = 'profile-status backtesting';
        statusEl.innerHTML = '<span class="pulse-dot"></span>Initializing...';
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
    initModalListeners();
    initControlsEvents();
    initTimeframeComparisonEvents();
    fetchDashboardToken();
});

// ═══════════════════════════════════════════════════
// TIMEFRAME SELECTOR & CONFIRMATION MODAL
// ═══════════════════════════════════════════════════
let pendingModalConfirmCallback = null;

function showConfirmModal(title, message, confirmText = 'Confirm', confirmClass = 'btn-danger', onConfirm = null) {
    if (typeof confirmText === 'function') {
        onConfirm = confirmText;
        confirmText = 'Confirm';
        confirmClass = 'btn-danger';
    } else if (typeof confirmClass === 'function') {
        onConfirm = confirmClass;
        confirmClass = 'btn-danger';
    }

    const modal = document.getElementById('confirm-modal');
    const titleEl = document.getElementById('modal-title');
    const bodyEl = document.getElementById('modal-body');
    const confirmBtn = document.getElementById('modal-confirm-btn');
    if (!modal || !titleEl || !bodyEl) return;

    titleEl.textContent = title;
    bodyEl.innerHTML = message;

    if (confirmBtn) {
        confirmBtn.textContent = confirmText;
        confirmBtn.className = `btn ${confirmClass}`;
    }

    pendingModalConfirmCallback = onConfirm;
    modal.classList.add('active');
}

function hideConfirmModal() {
    const modal = document.getElementById('confirm-modal');
    if (modal) modal.classList.remove('active');
    pendingModalConfirmCallback = null;
}

function initModalListeners() {
    const cancelBtn = document.getElementById('modal-cancel-btn');
    const confirmBtn = document.getElementById('modal-confirm-btn');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', hideConfirmModal);
    }
    if (confirmBtn) {
        confirmBtn.addEventListener('click', () => {
            const cb = pendingModalConfirmCallback;
            hideConfirmModal();
            if (typeof cb === 'function') {
                cb();
            }
        });
    }
}

function initTimeframeSelector() {
    const pills = document.querySelectorAll('.tf-pill');
    pills.forEach(pill => {
        pill.addEventListener('click', () => {
            const tf = pill.dataset.tf;
            const currentActive = document.querySelector('.tf-pill.active');
            const currentTf = currentActive ? currentActive.dataset.tf : '15m';
            if (currentTf === tf) return; // already active

            const openPositions = (lastState && lastState.wallet && lastState.wallet.positions) || [];
            const posCount = openPositions.length;

            const isAggregated = (tf === '10m');
            const typeDesc = isAggregated ? 'Aggregated Bar (2 × 5m native candles)' : 'Native Binance Kline';

            let warnText = `You are switching active trading timeframe from <strong>${escapeHtml(currentTf)}</strong> to <strong>${escapeHtml(tf)}</strong> (${typeDesc}).<br><br>`;
            if (posCount > 0) {
                warnText += `<div style="background:rgba(239, 68, 68, 0.15); border:1px solid rgba(239, 68, 68, 0.3); border-radius:4px; padding:8px; margin-bottom:10px; color:#fca5a5;">
                    ⚠️ <strong>Open Positions Warning:</strong> You currently have ${posCount} open paper position(s).<br>
                    To prevent cross-timeframe signal contamination, open positions will be closed cleanly at the current market price upon switching.
                </div>`;
            } else {
                warnText += `No open positions will be affected.<br>`;
            }
            warnText += `• Indicator buffers and strategy state will reset for the new interval.<br>
• Complete historical trade logs and PnL will be preserved.<br>
• The selection will be persisted to <code>settings.json</code>.`;

            showConfirmModal(
                `Switch Timeframe to ${tf}?`,
                warnText,
                'Confirm & Switch',
                'btn-primary',
                () => {
                    pills.forEach(p => p.classList.remove('active'));
                    pill.classList.add('active');

                    const badge = document.getElementById('active-tf-badge');
                    if (badge) {
                        badge.textContent = `${tf}${isAggregated ? ' (Aggregated)' : ''}`;
                    }

                    showToast(`Switching to ${tf} timeframe...`);
                    socket.emit('change_timeframe', { timeframe: tf });
                }
            );
        });
    });
}

function syncTimeframePill(state) {
    const tf = state.timeframe;
    if (!tf) return;
    document.querySelectorAll('.tf-pill').forEach(p => {
        p.classList.toggle('active', p.dataset.tf === tf);
    });
    const badge = document.getElementById('active-tf-badge');
    if (badge) {
        badge.textContent = `${tf}${tf === '10m' ? ' (Aggregated)' : ''}`;
    }
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

// ═══════════════════════════════════════════════════
// STAGE 6: AUTHENTICATION & API UTILITIES
// ═══════════════════════════════════════════════════
let dashboardToken = null;

async function fetchDashboardToken() {
    try {
        const res = await fetch('/api/auth/token');
        if (res.ok) {
            const data = await res.json();
            dashboardToken = data.token;
        }
    } catch (e) {
        console.warn('[Dashboard] Could not fetch auth token:', e);
    }
}

async function apiRequest(endpoint, method = 'GET', body = null) {
    const headers = { 'Content-Type': 'application/json' };
    if (dashboardToken) {
        headers['X-Zenith-Token'] = dashboardToken;
    }
    const options = { method, headers };
    if (body !== null) {
        if (method !== 'GET' && body.confirmation === undefined) {
            body.confirmation = true;
        }
        options.body = JSON.stringify(body);
    }
    const res = await fetch(endpoint, options);
    if (!res.ok) {
        let errData = {};
        try { errData = await res.json(); } catch (_) {}
        const msg = errData.message || errData.error || `HTTP ${res.status}`;
        throw new Error(msg);
    }
    return await res.json();
}

// ═══════════════════════════════════════════════════
// CONFIRMATION MODAL HELPER
// ═══════════════════════════════════════════════════
let pendingModalAction = null;

function showConfirmModal(title, message, confirmBtnText = 'Confirm', confirmBtnClass = 'btn-danger', onConfirm = null) {
    const modal = document.getElementById('confirm-modal');
    const titleEl = document.getElementById('modal-title');
    const bodyEl = document.getElementById('modal-body');
    const confirmBtn = document.getElementById('modal-confirm-btn');

    if (!modal) return;
    if (titleEl) titleEl.textContent = title;
    if (bodyEl) bodyEl.textContent = message;
    if (confirmBtn) {
        confirmBtn.textContent = confirmBtnText;
        confirmBtn.className = `btn ${confirmBtnClass}`;
    }

    pendingModalAction = onConfirm;
    modal.classList.add('active');
}

function hideConfirmModal() {
    const modal = document.getElementById('confirm-modal');
    if (modal) modal.classList.remove('active');
    pendingModalAction = null;
}

function initModalListeners() {
    const cancelBtn = document.getElementById('modal-cancel-btn');
    const confirmBtn = document.getElementById('modal-confirm-btn');
    const modal = document.getElementById('confirm-modal');

    if (cancelBtn) {
        cancelBtn.addEventListener('click', hideConfirmModal);
    }
    if (confirmBtn) {
        confirmBtn.addEventListener('click', async () => {
            if (pendingModalAction) {
                const action = pendingModalAction;
                hideConfirmModal();
                try {
                    await action();
                } catch (e) {
                    showToast(`Error: ${e.message}`);
                }
            } else {
                hideConfirmModal();
            }
        });
    }
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) hideConfirmModal();
        });
    }
}

function badgeClassForStatus(status) {
    if (!status) return 'badge-retired';
    const s = String(status).toLowerCase();
    if (s.includes('paper') || s.includes('active') || s.includes('normal') || s.includes('passed') || s.includes('completed')) return 'badge-paper';
    if (s.includes('promoted')) return 'badge-promoted';
    if (s.includes('candidate') || s.includes('running')) return 'badge-candidate';
    if (s.includes('retired') || s.includes('disabled') || s.includes('halted') || s.includes('failed') || s.includes('blowup')) return 'badge-danger';
    if (s.includes('warning') || s.includes('stopping') || s.includes('stale')) return 'badge-warning';
    return 'badge-retired';
}

// ═══════════════════════════════════════════════════
// 1. AI RESEARCH LAB
// ═══════════════════════════════════════════════════
let isFetchingResearch = false;
async function renderResearch(state) {
    if (isFetchingResearch) return;
    isFetchingResearch = true;
    try {
        const summary = await apiRequest('/api/research/summary').catch(() => null);
        if (summary) {
            const strats = summary.strategies || {};
            const exps = summary.experiments || {};
            const counts = strats.counts || {};

            const totalEl = document.getElementById('res-total-strats');
            if (totalEl) totalEl.textContent = strats.total || 0;

            const breakdownEl = document.getElementById('res-strat-breakdown');
            if (breakdownEl) {
                breakdownEl.textContent = `${counts.promoted || 0} promoted · ${counts.candidate || 0} candidates · ${counts.paper_trading || 0} paper`;
            }

            const totalExpEl = document.getElementById('res-total-exp');
            if (totalExpEl) totalExpEl.textContent = exps.total || 0;

            const compExpEl = document.getElementById('res-completed-exp');
            if (compExpEl) compExpEl.textContent = exps.completed || 0;

            const failExpEl = document.getElementById('res-failed-exp');
            if (failExpEl) failExpEl.textContent = exps.failed || 0;
        }

        const job = await apiRequest('/api/research/job-status').catch(() => null);
        if (job) {
            const badgeEl = document.getElementById('res-job-badge');
            if (badgeEl) {
                badgeEl.textContent = (job.status || 'IDLE').toUpperCase();
                badgeEl.className = `badge ${badgeClassForStatus(job.status)}`;
            }
            const timeEl = document.getElementById('res-job-time');
            if (timeEl && job.started_at) {
                timeEl.textContent = `Started ${timeAgo(job.started_at)}`;
            } else if (timeEl) {
                timeEl.textContent = 'Idle';
            }
        }

        const recData = await apiRequest('/api/research/recommendations').catch(() => null);
        const recContainer = document.getElementById('res-recommendations-container');
        if (recContainer && recData) {
            const recs = recData.recommendations || [];
            const patterns = recData.performance_patterns || [];
            const alerts = recData.overfitting_alerts || [];

            if (recs.length === 0 && patterns.length === 0 && alerts.length === 0) {
                recContainer.innerHTML = '<div style="font-size:0.78rem;color:var(--text-muted);text-align:center;padding:1rem;">No recommendations generated yet. Run experiments to generate AI insights.</div>';
            } else {
                let html = '';
                alerts.forEach(a => {
                    html += `<div style="background:rgba(239,68,68,0.1);border-left:3px solid var(--red);padding:6px 10px;margin-bottom:6px;border-radius:4px;font-size:0.75rem;">
                        <strong style="color:var(--red);">⚠️ Overfitting Alert:</strong> ${escapeHtml(a.message || a.warning || JSON.stringify(a))}
                    </div>`;
                });
                recs.forEach(r => {
                    html += `<div style="background:rgba(255,255,255,0.03);border:1px solid var(--border);padding:8px 10px;margin-bottom:6px;border-radius:4px;font-size:0.75rem;">
                        <strong style="color:var(--text-primary);">${escapeHtml(r.action || r.type || 'Suggestion')}:</strong> ${escapeHtml(r.description || r.rationale || r.message || '')}
                    </div>`;
                });
                patterns.forEach(p => {
                    html += `<div style="background:rgba(59,130,246,0.08);border-left:3px solid #3b82f6;padding:6px 10px;margin-bottom:6px;border-radius:4px;font-size:0.75rem;">
                        <strong style="color:#60a5fa;">💡 Pattern:</strong> ${escapeHtml(p.insight || p.pattern || JSON.stringify(p))}
                    </div>`;
                });
                recContainer.innerHTML = html;
            }
        }

        const strategies = await apiRequest('/api/research/strategies').catch(() => []);
        const candTbody = document.getElementById('res-candidates-tbody');
        if (candTbody) {
            if (!strategies || strategies.length === 0) {
                candTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:1.5rem;">No candidates discovered in registry yet.</td></tr>';
            } else {
                let html = '';
                strategies.slice(0, 30).forEach(s => {
                    const st = s.status || 'candidate';
                    const badge = `<span class="badge ${badgeClassForStatus(st)}">${escapeHtml(st)}</span>`;
                    html += `<tr>
                        <td><strong>${escapeHtml(s.name || s.id || '—')}</strong></td>
                        <td class="mono">v${escapeHtml(String(s.version || '1.0'))}</td>
                        <td>${escapeHtml(s.strategy_type || 'Custom')}</td>
                        <td class="mono">${escapeHtml(s.timeframe || '15m')}</td>
                        <td>${badge}</td>
                        <td style="color:var(--text-muted);font-size:0.75rem;">${escapeHtml(s.created_at ? s.created_at.slice(0, 19) : '—')}</td>
                    </tr>`;
                });
                candTbody.innerHTML = html;
            }
        }

        const experiments = await apiRequest('/api/research/experiments?limit=25').catch(() => []);
        const reasonsContainer = document.getElementById('res-reasons-container');
        if (reasonsContainer) {
            if (!experiments || experiments.length === 0) {
                reasonsContainer.innerHTML = '<div style="font-size:0.78rem;color:var(--text-muted);text-align:center;padding:1rem;">No experiment validation logs available yet.</div>';
            } else {
                let html = '<table class="data-table"><thead><tr><th>Exp ID</th><th>Strategy</th><th>Status</th><th>Validation Criteria & Reasons</th></tr></thead><tbody>';
                experiments.forEach(e => {
                    const st = e.status || 'unknown';
                    const badge = `<span class="badge ${badgeClassForStatus(st)}">${escapeHtml(st)}</span>`;
                    const reason = e.failure_reason || (st === 'completed' ? 'Passed train & test validation thresholds' : (e.notes || 'In progress'));
                    html += `<tr>
                        <td class="mono">${escapeHtml(String(e.id || '—'))}</td>
                        <td><strong>${escapeHtml(e.strategy_name || '—')}</strong></td>
                        <td>${badge}</td>
                        <td style="font-size:0.75rem;color:var(--text-secondary);">${escapeHtml(reason)}</td>
                    </tr>`;
                });
                html += '</tbody></table>';
                reasonsContainer.innerHTML = html;
            }
        }
    } catch (e) {
        console.error('[Dashboard] Error rendering research:', e);
    } finally {
        isFetchingResearch = false;
    }
}

// ═══════════════════════════════════════════════════
// 2. STRATEGY COMPARISON
// ═══════════════════════════════════════════════════
let isFetchingComparison = false;
async function renderComparison(state) {
    if (isFetchingComparison) return;
    isFetchingComparison = true;
    try {
        const baseline = await apiRequest('/api/research/baseline').catch(() => null);
        const nameEl = document.getElementById('comp-base-name');
        const dateEl = document.getElementById('comp-base-date');
        const prevEl = document.getElementById('comp-base-prev');

        if (baseline && baseline.strategy_name) {
            if (nameEl) nameEl.textContent = `${baseline.strategy_name} (v${baseline.version || '1.0'})`;
            if (dateEl) dateEl.textContent = baseline.set_at ? baseline.set_at.slice(0, 19) : 'Active';
            if (prevEl) prevEl.textContent = baseline.previous_baseline || 'None (Initial)';
        } else {
            if (nameEl) nameEl.textContent = 'None set (Awaiting first pass)';
            if (dateEl) dateEl.textContent = '—';
            if (prevEl) prevEl.textContent = '—';
        }

        const leaderboard = await apiRequest('/api/research/leaderboard').catch(() => []);
        const lbTbody = document.getElementById('comp-leaderboard-tbody');
        if (lbTbody) {
            if (!leaderboard || leaderboard.length === 0) {
                lbTbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:1.5rem;">No completed strategy evaluations yet.</td></tr>';
            } else {
                let html = '';
                leaderboard.forEach(item => {
                    const gradeClass = item.grade === 'A' ? 'positive' : item.grade === 'B' ? 'warning' : 'negative';
                    const retCls = pnlClass(item.return_pct || 0);
                    const verdictBadge = `<span class="badge ${item.verdict === 'passed' ? 'badge-paper' : 'badge-danger'}">${escapeHtml(item.verdict || '—')}</span>`;

                    html += `<tr>
                        <td><strong>${escapeHtml(item.strategy_name || '—')}</strong></td>
                        <td style="font-weight:800;" class="${gradeClass}">${escapeHtml(item.grade || '—')}</td>
                        <td class="mono">${Number(item.score || 0).toFixed(1)}</td>
                        <td class="mono">${Number(item.sharpe || 0).toFixed(2)}</td>
                        <td class="mono ${retCls}">${formatPct(item.return_pct)}</td>
                        <td class="mono">${Number(item.max_drawdown || 0).toFixed(1)}%</td>
                        <td class="mono">${Number(item.win_rate || 0).toFixed(1)}%</td>
                        <td class="mono">${item.trades || 0}</td>
                        <td>${verdictBadge}</td>
                    </tr>`;
                });
                lbTbody.innerHTML = html;
            }
        }

        const auditLog = await apiRequest('/api/research/audit').catch(() => []);
        const auditTbody = document.getElementById('comp-audit-tbody');
        if (auditTbody) {
            if (!auditLog || auditLog.length === 0) {
                auditTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:1.5rem;">No promotion audit records yet.</td></tr>';
            } else {
                let html = '';
                auditLog.forEach(row => {
                    const statusChange = `${row.from_status || '—'} → ${row.to_status || '—'}`;
                    html += `<tr>
                        <td style="font-size:0.75rem;color:var(--text-muted);">${escapeHtml(row.timestamp ? row.timestamp.slice(0, 19) : '—')}</td>
                        <td><strong>${escapeHtml(row.strategy_name || row.strategy_id || '—')}</strong></td>
                        <td><span class="badge badge-candidate">${escapeHtml(row.action || '—')}</span></td>
                        <td class="mono" style="font-size:0.75rem;">${escapeHtml(statusChange)}</td>
                        <td style="font-size:0.75rem;color:var(--text-secondary);">${escapeHtml(row.reason || '—')}</td>
                        <td>${escapeHtml(row.approved_by || 'system')}</td>
                    </tr>`;
                });
                auditTbody.innerHTML = html;
            }
        }
    } catch (e) {
        console.error('[Dashboard] Error rendering comparison:', e);
    } finally {
        isFetchingComparison = false;
    }
}

// ═══════════════════════════════════════════════════
// 3. BACKTESTING LAB
// ═══════════════════════════════════════════════════
let isFetchingBacktesting = false;
async function renderBacktesting(state) {
    if (isFetchingBacktesting) return;
    isFetchingBacktesting = true;
    try {
        const divData = await apiRequest('/api/execution/paper-vs-backtest').catch(() => null);
        if (divData) {
            const statusEl = document.getElementById('bt-div-status');
            const summaryEl = document.getElementById('bt-div-summary');
            const sharpeEl = document.getElementById('bt-div-sharpe');
            const wrEl = document.getElementById('bt-div-wr');
            const ddEl = document.getElementById('bt-div-dd');

            const isNormal = divData.divergence_level === 'NORMAL' || divData.status === 'OK' || !divData.divergence_level;
            if (statusEl) {
                statusEl.textContent = isNormal ? 'NORMAL' : 'DIVERGENCE ALERT';
                statusEl.className = `badge ${isNormal ? 'badge-paper' : 'badge-danger'}`;
            }
            if (summaryEl) {
                summaryEl.textContent = divData.summary || divData.message || 'Forward paper results are in alignment with backtest parameters.';
            }
            if (sharpeEl) sharpeEl.textContent = divData.sharpe_diff !== undefined ? Number(divData.sharpe_diff).toFixed(2) : '0.00';
            if (wrEl) wrEl.textContent = divData.win_rate_diff_pct !== undefined ? formatPct(divData.win_rate_diff_pct) : '0.00%';
            if (ddEl) ddEl.textContent = divData.drawdown_ratio !== undefined ? Number(divData.drawdown_ratio).toFixed(2) + 'x' : '1.00x';
        }

        const reports = await apiRequest('/api/research/reports?limit=25').catch(() => []);
        const reportsTbody = document.getElementById('bt-reports-tbody');
        if (reportsTbody) {
            if (!reports || reports.length === 0) {
                reportsTbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:1.5rem;">No completed backtest reports yet.</td></tr>';
            } else {
                let html = '';
                reports.forEach(r => {
                    html += `<tr>
                        <td class="mono">${escapeHtml(String(r.id || '—'))}</td>
                        <td><span class="badge badge-candidate">${escapeHtml(r.report_type || 'backtest')}</span></td>
                        <td><strong>${escapeHtml(r.strategy_name || '—')}</strong></td>
                        <td style="font-size:0.75rem;color:var(--text-muted);">${escapeHtml(r.created_at ? r.created_at.slice(0, 19) : '—')}</td>
                        <td style="font-size:0.75rem;color:var(--text-secondary);">${escapeHtml(r.summary || 'Summary unavailable')}</td>
                    </tr>`;
                });
                reportsTbody.innerHTML = html;
            }
        }
    } catch (e) {
        console.error('[Dashboard] Error rendering backtesting:', e);
    } finally {
        isFetchingBacktesting = false;
    }
}

// ═══════════════════════════════════════════════════
// 4. PAPER TRADING
// ═══════════════════════════════════════════════════
function renderPaperTrading(state) {
    const wallet = state.wallet || {};
    const stats = wallet.stats || {};
    const startingBalance = stats.starting_balance || 10000;
    const equity = wallet.equity !== undefined ? wallet.equity : startingBalance;
    const cash = wallet.cash !== undefined ? wallet.cash : startingBalance;
    const returnPct = ((equity - startingBalance) / startingBalance) * 100;
    const peakEquity = wallet.peak_equity || Math.max(equity, startingBalance);
    const dd = wallet.drawdown_pct || 0;
    const fees = stats.total_fees_paid || 0;

    const cashEl = document.getElementById('pt-cash');
    if (cashEl) cashEl.textContent = formatUSD(cash);

    const eqEl = document.getElementById('pt-equity');
    if (eqEl) {
        eqEl.textContent = formatUSD(equity);
        eqEl.className = 'stat-value ' + pnlClass(returnPct);
    }

    const retEl = document.getElementById('pt-return-pct');
    if (retEl) retEl.textContent = `${formatPct(returnPct)} return · from ${formatUSD(startingBalance)}`;

    const peakEl = document.getElementById('pt-peak');
    if (peakEl) peakEl.textContent = formatUSD(peakEquity);

    const ddEl = document.getElementById('pt-drawdown');
    if (ddEl) ddEl.textContent = `${Number(dd).toFixed(2)}% Drawdown`;

    const feesEl = document.getElementById('pt-fees');
    if (feesEl) feesEl.textContent = formatUSD(fees);

    const posTbody = document.getElementById('pt-positions-tbody');
    const positions = wallet.positions || [];
    if (posTbody) {
        if (positions.length === 0) {
            posTbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:1.5rem;">No open paper positions</td></tr>';
        } else {
            let html = '';
            positions.forEach(p => {
                const pnlCls = pnlClass(p.unrealized_pnl || 0);
                html += `<tr>
                    <td class="mono" style="font-size:0.75rem;">${escapeHtml(p.id || '—')}</td>
                    <td><strong>${escapeHtml(p.symbol || '—')}</strong></td>
                    <td>${p.side === 'long' ? '🟢 Long' : '🔴 Short'}</td>
                    <td class="mono">${Number(p.quantity || 0).toFixed(6)}</td>
                    <td class="mono">${formatUSD(p.entry_price)}</td>
                    <td class="mono">${formatUSD(p.current_price || p.entry_price)}</td>
                    <td class="mono ${pnlCls}">${formatPnL(p.unrealized_pnl)} (${formatPct(p.unrealized_pnl_pct)})</td>
                    <td class="mono">${p.stop_loss ? formatUSD(p.stop_loss) : '—'}</td>
                    <td class="mono">${p.take_profit ? formatUSD(p.take_profit) : '—'}</td>
                </tr>`;
            });
            posTbody.innerHTML = html;
        }
    }

    const tradesTbody = document.getElementById('pt-trades-tbody');
    const trades = state.recent_trades || [];
    if (tradesTbody) {
        if (trades.length === 0) {
            tradesTbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:1.5rem;">No trades recorded yet</td></tr>';
        } else {
            let html = '';
            [...trades].reverse().slice(0, 50).forEach(t => {
                const pnlCls = pnlClass(t.net_pnl || 0);
                const duration = t.duration_seconds ? `${Math.round(t.duration_seconds)}s` : '—';
                html += `<tr>
                    <td><strong>${escapeHtml(t.symbol || '—')}</strong></td>
                    <td>${t.side === 'long' ? '🟢 Long' : '🔴 Short'}</td>
                    <td style="font-size:0.75rem;">${escapeHtml(t.strategy_name || 'Active')}</td>
                    <td class="mono">${Number(t.quantity || 0).toFixed(6)}</td>
                    <td class="mono">${formatUSD(t.entry_price)}</td>
                    <td class="mono">${formatUSD(t.exit_price)}</td>
                    <td class="mono ${pnlCls}">${formatPnL(t.net_pnl)}</td>
                    <td class="mono">${formatUSD(t.total_fees || 0)}</td>
                    <td>${duration}</td>
                </tr>`;
            });
            tradesTbody.innerHTML = html;
        }
    }
}

// ═══════════════════════════════════════════════════
// 5. SYSTEM MONITORING
// ═══════════════════════════════════════════════════
let isFetchingMonitoring = false;
async function renderMonitoring(state) {
    if (isFetchingMonitoring) return;
    isFetchingMonitoring = true;
    try {
        const mon = await apiRequest('/api/execution/monitoring').catch(() => null);
        if (mon) {
            const broker = mon.broker || {};
            const feed = mon.data_feed || {};
            const risk = mon.risk || {};

            const feedStatusEl = document.getElementById('mon-feed-status');
            if (feedStatusEl) {
                feedStatusEl.textContent = feed.status || 'ACTIVE';
                feedStatusEl.className = 'stat-value ' + (feed.status === 'ACTIVE' ? 'positive' : 'negative');
            }

            const brokerModeEl = document.getElementById('mon-broker-mode');
            if (brokerModeEl) {
                brokerModeEl.textContent = (broker.mode || 'PAPER').toUpperCase();
            }

            const breakerStatusEl = document.getElementById('mon-breaker-status');
            const breakerActive = (state.risk || {}).breaker_active;
            if (breakerStatusEl) {
                breakerStatusEl.textContent = breakerActive ? 'TRIPPED' : 'ARMED';
                breakerStatusEl.className = 'stat-value ' + (breakerActive ? 'negative' : 'positive');
            }

            const emergencyHalted = risk.emergency_stop || false;
            const emStatusEl = document.getElementById('mon-emergency-status');
            if (emStatusEl) {
                emStatusEl.textContent = emergencyHalted ? 'HALTED' : 'DISARMED';
                emStatusEl.className = 'stat-value ' + (emergencyHalted ? 'negative' : 'positive');
            }

            const staleTbody = document.getElementById('mon-stale-tbody');
            if (staleTbody && feed.symbols) {
                let html = '';
                Object.entries(feed.symbols).forEach(([sym, info]) => {
                    const isStale = info.is_stale;
                    const badge = `<span class="badge ${isStale ? 'badge-danger' : 'badge-paper'}">${isStale ? 'STALE' : 'FRESH'}</span>`;
                    const elapsed = info.elapsed_seconds !== null && info.elapsed_seconds !== undefined ? `${info.elapsed_seconds}s` : '< 1s';
                    html += `<tr>
                        <td><strong>${escapeHtml(sym)}</strong></td>
                        <td>${badge}</td>
                        <td class="mono">${elapsed}</td>
                        <td class="mono">120s</td>
                    </tr>`;
                });
                if (html) staleTbody.innerHTML = html;
            }

            const limits = risk.limits || {};
            if (limits.max_order_usd && document.getElementById('mon-max-order')) document.getElementById('mon-max-order').textContent = formatUSD(limits.max_order_usd);
            if (limits.daily_loss_limit_usd && document.getElementById('mon-daily-loss-lim')) document.getElementById('mon-daily-loss-lim').textContent = `${formatUSD(limits.daily_loss_limit_usd)} / 24h`;
            if (limits.max_exposure_pct && document.getElementById('mon-max-exp')) document.getElementById('mon-max-exp').textContent = `${limits.max_exposure_pct}% Equity`;
            if (limits.max_open_positions && document.getElementById('mon-max-pos')) document.getElementById('mon-max-pos').textContent = `${limits.max_open_positions} Concurrent`;
        }

        const eventsRes = await apiRequest('/api/execution/events?limit=30').catch(() => ({ events: [] }));
        const eventsTbody = document.getElementById('mon-events-tbody');
        if (eventsTbody) {
            const events = eventsRes.events || [];
            if (events.length === 0) {
                eventsTbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:1.5rem;">No system events recorded.</td></tr>';
            } else {
                let html = '';
                events.forEach(ev => {
                    const sev = ev.severity || 'info';
                    const badgeCls = sev === 'error' || sev === 'critical' ? 'badge-danger' : sev === 'warning' ? 'badge-warning' : 'badge-paper';
                    const ts = ev.timestamp ? new Date(ev.timestamp * 1000).toISOString().slice(11, 19) : '—';
                    html += `<tr>
                        <td class="mono text-muted">${ts}</td>
                        <td><strong>${escapeHtml(ev.event_type || 'EVENT')}</strong></td>
                        <td><span class="badge ${badgeCls}">${escapeHtml(sev)}</span></td>
                        <td style="font-size:0.75rem;color:var(--text-secondary);">${escapeHtml(ev.message || JSON.stringify(ev.data || {}))}</td>
                    </tr>`;
                });
                eventsTbody.innerHTML = html;
            }
        }
    } catch (e) {
        console.error('[Dashboard] Error rendering monitoring:', e);
    } finally {
        isFetchingMonitoring = false;
    }
}

// ═══════════════════════════════════════════════════
// 6. SAFETY & CONTROLS
// ═══════════════════════════════════════════════════
let isFetchingControls = false;
async function renderControls(state) {
    if (isFetchingControls) return;
    isFetchingControls = true;
    try {
        const execStatus = await apiRequest('/api/execution/status').catch(() => null);
        const emBadge = document.getElementById('ctrl-emergency-status');
        if (emBadge && execStatus) {
            if (execStatus.is_emergency_halted) {
                emBadge.textContent = 'HALTED';
                emBadge.className = 'badge badge-danger';
            } else {
                emBadge.textContent = 'NORMAL';
                emBadge.className = 'badge badge-success';
            }
        }

        const pending = await apiRequest('/api/research/pending').catch(() => []);
        const container = document.getElementById('ctrl-pending-container');
        if (container) {
            if (!pending || pending.length === 0) {
                container.innerHTML = '<div style="font-size:0.8rem;color:var(--text-muted);padding:1.5rem;text-align:center;">No strategies currently awaiting human approval. All generated candidates have either been evaluated or processed.</div>';
            } else {
                let html = '<table class="data-table"><thead><tr><th>Strategy Name</th><th>Version</th><th>Type</th><th>Actions</th></tr></thead><tbody>';
                pending.forEach(strat => {
                    const sid = escapeHtml(strat.id || strat.name);
                    const sname = escapeHtml(strat.name || strat.id);
                    html += `<tr>
                        <td><strong>${sname}</strong></td>
                        <td class="mono">v${escapeHtml(String(strat.version || '1.0'))}</td>
                        <td>${escapeHtml(strat.strategy_type || 'Candidate')}</td>
                        <td>
                            <div style="display:flex;gap:0.5rem;">
                                <button class="btn btn-success btn-approve-strat" data-id="${sid}" data-name="${sname}" style="padding:0.3rem 0.7rem;font-size:0.72rem;">✅ Approve for Paper</button>
                                <button class="btn btn-danger btn-reject-strat" data-id="${sid}" data-name="${sname}" style="padding:0.3rem 0.7rem;font-size:0.72rem;">❌ Reject</button>
                            </div>
                        </td>
                    </tr>`;
                });
                html += '</tbody></table>';
                container.innerHTML = html;

                container.querySelectorAll('.btn-approve-strat').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const id = btn.dataset.id;
                        const name = btn.dataset.name;
                        showConfirmModal(
                            'Approve Strategy for Paper Trading',
                            `Are you sure you want to approve "${name}" for forward paper trading? This will activate virtual order generation.`,
                            'Approve Strategy',
                            'btn-success',
                            async () => {
                                await apiRequest('/api/research/approve', 'POST', {
                                    strategy_id: id,
                                    approved_by: 'dashboard_operator',
                                    confirmation: true
                                });
                                showToast(`Strategy ${name} approved for paper trading!`);
                                renderControls(lastState);
                            }
                        );
                    });
                });

                container.querySelectorAll('.btn-reject-strat').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const id = btn.dataset.id;
                        const name = btn.dataset.name;
                        showConfirmModal(
                            'Reject Strategy Candidate',
                            `Are you sure you want to reject and retire strategy candidate "${name}"?`,
                            'Reject Strategy',
                            'btn-danger',
                            async () => {
                                await apiRequest('/api/research/reject', 'POST', {
                                    strategy_id: id,
                                    reason: 'Operator rejected via dashboard controls',
                                    confirmation: true
                                });
                                showToast(`Strategy ${name} rejected.`);
                                renderControls(lastState);
                            }
                        );
                    });
                });
            }
        }
    } catch (e) {
        console.error('[Dashboard] Error rendering controls:', e);
    } finally {
        isFetchingControls = false;
    }
}

// ═══════════════════════════════════════════════════
// CONTROLS EVENT HANDLERS
// ═══════════════════════════════════════════════════
function initControlsEvents() {
    // 1. Emergency Stop Button
    const emBtn = document.getElementById('btn-trigger-emergency');
    if (emBtn) {
        emBtn.addEventListener('click', () => {
            showConfirmModal(
                'Trigger Emergency Stop',
                'Are you sure you want to trigger GLOBAL EMERGENCY STOP? This will immediately halt all trading routines, reject incoming signals, cancel any active orders, and liquidate all open positions.',
                'TRIGGER EMERGENCY STOP',
                'btn-emergency',
                async () => {
                    await apiRequest('/api/execution/emergency-stop', 'POST', {
                        confirmation: true,
                        reason: 'Triggered manually by dashboard operator'
                    });
                    showToast('🚨 Global Emergency Stop Triggered!');
                    renderControls(lastState);
                    renderMonitoring(lastState);
                }
            );
        });
    }

    // 2. Emergency Reset Button
    const resetBtn = document.getElementById('btn-reset-emergency');
    if (resetBtn) {
        resetBtn.addEventListener('click', () => {
            const tokenInput = document.getElementById('ctrl-reset-token-input');
            const tokenVal = tokenInput ? tokenInput.value.trim() : '';
            if (!tokenVal) {
                showToast('Please enter an emergency reset token.');
                return;
            }
            showConfirmModal(
                'Reset Emergency Stop',
                'Reset the emergency stop and restore normal trading readiness?',
                'Reset System',
                'btn-secondary',
                async () => {
                    const res = await apiRequest('/api/execution/emergency-reset', 'POST', {
                        confirmation: true,
                        reset_token: tokenVal
                    });
                    if (res.success) {
                        showToast('Emergency Stop successfully reset.');
                        if (tokenInput) tokenInput.value = '';
                        renderControls(lastState);
                        renderMonitoring(lastState);
                    } else {
                        showToast(`Reset failed: ${res.message}`);
                    }
                }
            );
        });
    }

    // 3. Stop Trading Button
    const stopTradeBtn = document.getElementById('btn-ctrl-stop-trading');
    if (stopTradeBtn) {
        stopTradeBtn.addEventListener('click', () => {
            showConfirmModal(
                'Stop Paper Trading',
                'Pause the market feed processing loop and stop generating new paper orders?',
                'Stop Paper Trading',
                'btn-danger',
                async () => {
                    await apiRequest('/api/execution/stop-trading', 'POST', { confirmation: true });
                    showToast('Paper trading paused.');
                    renderControls(lastState);
                    renderMonitoring(lastState);
                }
            );
        });
    }

    // 4. Resume Trading Button
    const resumeTradeBtn = document.getElementById('btn-ctrl-resume-trading');
    if (resumeTradeBtn) {
        resumeTradeBtn.addEventListener('click', () => {
            showToast('Paper trading engine active and running.');
        });
    }

    // 5. Research Run Buttons
    function triggerLaunchResearch() {
        const candInput = document.getElementById('ctrl-max-candidates');
        const timeInput = document.getElementById('ctrl-max-runtime');
        const maxCandidates = candInput ? parseInt(candInput.value, 10) || 3 : 3;
        const maxRuntime = timeInput ? parseInt(timeInput.value, 10) || 300 : 300;

        showConfirmModal(
            'Launch AI Research Run',
            `Launch an autonomous research run generating up to ${maxCandidates} candidate strategies with a maximum runtime of ${maxRuntime} seconds?`,
            'Launch Run',
            'btn-primary',
            async () => {
                await apiRequest('/api/research/run-job', 'POST', {
                    confirmation: true,
                    max_candidates: maxCandidates,
                    max_runtime_seconds: maxRuntime
                });
                showToast('AI Research run initiated in background!');
                renderResearch(lastState);
            }
        );
    }

    const runJobBtn1 = document.getElementById('btn-ctrl-run-job');
    const runJobBtn2 = document.getElementById('btn-run-research-job');
    if (runJobBtn1) runJobBtn1.addEventListener('click', triggerLaunchResearch);
    if (runJobBtn2) runJobBtn2.addEventListener('click', triggerLaunchResearch);

    // 6. Research Stop Buttons
    function triggerStopResearch() {
        showConfirmModal(
            'Stop Research Job',
            'Halt the active AI research job? Completed experiments will be preserved.',
            'Halt Job',
            'btn-secondary',
            async () => {
                await apiRequest('/api/research/stop-job', 'POST', { confirmation: true });
                showToast('Research job stop requested.');
                renderResearch(lastState);
            }
        );
    }

    const stopJobBtn1 = document.getElementById('btn-ctrl-stop-job');
    const stopJobBtn2 = document.getElementById('btn-stop-research-job');
    if (stopJobBtn1) stopJobBtn1.addEventListener('click', triggerStopResearch);
    if (stopJobBtn2) stopJobBtn2.addEventListener('click', triggerStopResearch);
}

// ═══════════════════════════════════════════════════
// MULTI-TIMEFRAME PERFORMANCE COMPARISON (STEPS 5 & 6)
// ═══════════════════════════════════════════════════

function initTimeframeComparisonEvents() {
    const filters = ['tf-filter-date', 'tf-filter-symbol', 'tf-filter-strategy', 'tf-filter-mode'];
    filters.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', () => fetchTimeframeComparisonData());
        }
    });

    const refreshBtn = document.getElementById('btn-refresh-tf-compare');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => fetchTimeframeComparisonData());
    }

    const runEvalBtn = document.getElementById('btn-run-tf-evaluation');
    if (runEvalBtn) {
        runEvalBtn.addEventListener('click', runControlledTfEvaluation);
    }
}

async function renderTimeframeComparison(state) {
    await fetchTimeframeComparisonData();
}

async function fetchTimeframeComparisonData() {
    const tbody = document.getElementById('tf-comparison-tbody');
    if (!tbody) return;

    const dateVal = document.getElementById('tf-filter-date')?.value || 'all';
    const symbolVal = document.getElementById('tf-filter-symbol')?.value || 'all';
    const strategyVal = document.getElementById('tf-filter-strategy')?.value || 'all';
    const modeVal = document.getElementById('tf-filter-mode')?.value || 'all';

    const params = new URLSearchParams();
    if (symbolVal !== 'all') params.append('symbol', symbolVal);
    if (strategyVal !== 'all') params.append('strategy', strategyVal);
    if (modeVal !== 'all') params.append('mode', modeVal);

    if (dateVal === '7d' || dateVal === '30d' || dateVal === '90d') {
        const days = parseInt(dateVal.replace('d', ''), 10);
        const startDate = new Date(Date.now() - days * 86400000).toISOString();
        params.append('start_date', startDate);
    }

    try {
        const res = await fetch(`/api/timeframe/comparison?${params.toString()}`);
        if (!res.ok) {
            tbody.innerHTML = `<tr><td colspan="13" style="text-align:center;color:#ef4444;padding:1rem;">Failed to fetch comparison data</td></tr>`;
            return;
        }

        const data = await res.json();
        renderTfComparisonTable(data.timeframes || []);
        renderTfComparisonBars(data.timeframes || []);

        const noticeEl = document.getElementById('tf-comparability-text');
        if (noticeEl && data.comparability_notice) {
            noticeEl.textContent = data.comparability_notice;
        }
    } catch (e) {
        console.error('[Dashboard] Error fetching timeframe comparison:', e);
        tbody.innerHTML = `<tr><td colspan="13" style="text-align:center;color:#ef4444;padding:1rem;">Error: ${escapeHtml(e.message)}</td></tr>`;
    }
}

function renderTfComparisonTable(rows) {
    const tbody = document.getElementById('tf-comparison-tbody');
    if (!tbody) return;

    if (!rows || rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="13" style="text-align:center;color:var(--text-muted);padding:1.5rem;">No timeframe metrics recorded yet.</td></tr>`;
        return;
    }

    tbody.innerHTML = rows.map(r => {
        const isInsufficient = (r.status === 'insufficient_data');
        const pnlCls = r.net_pnl > 0 ? 'positive' : (r.net_pnl < 0 ? 'negative' : 'neutral');
        const pfStr = (r.profit_factor >= 999 || r.profit_factor === Infinity) ? '∞' : r.profit_factor.toFixed(2);
        const tfLabel = r.timeframe + (r.type === 'aggregated' ? '*' : '');
        const typeBadge = r.type === 'native'
            ? `<span style="font-size:0.65rem;padding:2px 6px;border-radius:4px;background:rgba(59,130,246,0.15);color:#60a5fa;">Native</span>`
            : `<span style="font-size:0.65rem;padding:2px 6px;border-radius:4px;background:rgba(168,85,247,0.15);color:#c084fc;" title="Aggregated from 2 × 5m bars">Aggregated (5m)</span>`;

        if (isInsufficient) {
            return `<tr>
                <td class="mono font-bold">${escapeHtml(tfLabel)}</td>
                <td>${typeBadge}</td>
                <td><span style="background:rgba(100,116,139,0.2);padding:2px 6px;border-radius:4px;font-size:0.75rem;">${r.sample_size} trades</span></td>
                <td colspan="8" style="color:var(--text-muted);font-style:italic;padding-left:1rem;">
                    Not enough data (${r.sample_size} / ${r.min_required_trades} trades required for statistical validity)
                </td>
                <td>${r.data_gaps_and_errors}</td>
                <td><span style="font-size:0.7rem;color:var(--text-muted);background:rgba(255,255,255,0.05);padding:2px 6px;border-radius:4px;">Pending Data</span></td>
            </tr>`;
        }

        return `<tr>
            <td class="mono font-bold">${escapeHtml(tfLabel)}</td>
            <td>${typeBadge}</td>
            <td><strong>${r.total_trades}</strong> <span style="font-size:0.7rem;color:var(--text-muted);">(${r.date_range})</span></td>
            <td class="mono font-bold ${r.win_rate >= 50 ? 'positive' : 'negative'}">${r.win_rate.toFixed(1)}%</td>
            <td class="mono">${r.winning_trades} / ${r.losing_trades}</td>
            <td class="mono">${formatUSD(r.gross_pnl)}</td>
            <td class="mono">${formatUSD(r.fees_paid)}</td>
            <td class="mono font-bold ${pnlCls}">${formatPnL(r.net_pnl)}</td>
            <td class="mono ${pnlCls}">${formatPnL(r.avg_net_pnl)}</td>
            <td class="mono negative">${r.max_drawdown_pct.toFixed(1)}%</td>
            <td class="mono ${r.profit_factor >= 1.0 ? 'positive' : 'negative'}">${pfStr}</td>
            <td>${r.data_gaps_and_errors} <span style="font-size:0.65rem;color:var(--text-muted);">(${r.uptime_pct.toFixed(0)}% uptime)</span></td>
            <td><span style="font-size:0.7rem;color:#10b981;background:rgba(16,185,129,0.12);padding:2px 6px;border-radius:4px;font-weight:600;">Active</span></td>
        </tr>`;
    }).join('');
}

function renderTfComparisonBars(rows) {
    const pnlContainer = document.getElementById('tf-pnl-bars');
    const winContainer = document.getElementById('tf-winrate-bars');
    if (!pnlContainer || !winContainer) return;

    if (!rows || rows.length === 0) {
        pnlContainer.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem;">No data</div>';
        winContainer.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem;">No data</div>';
        return;
    }

    const maxPnl = Math.max(...rows.map(r => Math.abs(r.net_pnl || 0)), 10.0);

    pnlContainer.innerHTML = rows.map(r => {
        const pnl = r.net_pnl || 0;
        const isNeg = pnl < 0;
        const widthPct = Math.min(100, Math.round((Math.abs(pnl) / maxPnl) * 100));
        const color = isNeg ? '#ef4444' : (pnl > 0 ? '#10b981' : '#64748b');
        const label = r.status === 'insufficient_data' ? 'Not enough data' : formatPnL(pnl);

        return `<div>
            <div style="display:flex;justify-content:space-between;font-size:0.75rem;margin-bottom:3px;">
                <span class="mono font-bold">${r.timeframe}</span>
                <span class="mono" style="color:${color};font-weight:600;">${label}</span>
            </div>
            <div style="background:rgba(255,255,255,0.06);height:8px;border-radius:4px;overflow:hidden;">
                <div style="background:${color};height:100%;width:${widthPct}%;transition:width 0.3s ease;"></div>
            </div>
        </div>`;
    }).join('');

    winContainer.innerHTML = rows.map(r => {
        const winRate = r.win_rate || 0;
        const color = winRate >= 50 ? '#10b981' : (winRate > 0 ? '#f59e0b' : '#64748b');
        const label = r.status === 'insufficient_data' ? 'Not enough data' : `${winRate.toFixed(1)}% (${r.total_trades} trades)`;

        return `<div>
            <div style="display:flex;justify-content:space-between;font-size:0.75rem;margin-bottom:3px;">
                <span class="mono font-bold">${r.timeframe}</span>
                <span class="mono" style="color:${color};font-weight:600;">${label}</span>
            </div>
            <div style="background:rgba(255,255,255,0.06);height:8px;border-radius:4px;overflow:hidden;">
                <div style="background:${color};height:100%;width:${winRate}%;transition:width 0.3s ease;"></div>
            </div>
        </div>`;
    }).join('');
}

async function runControlledTfEvaluation() {
    const symbol = document.getElementById('tf-eval-symbol')?.value || 'BTCUSDT';
    const strategy = document.getElementById('tf-eval-strategy')?.value || 'ema_vwap_rsi';
    const days = parseInt(document.getElementById('tf-eval-days')?.value || '30', 10);

    const statusEl = document.getElementById('tf-eval-status');
    const resultsContainer = document.getElementById('tf-eval-results-container');
    const tbody = document.getElementById('tf-eval-tbody');
    const btn = document.getElementById('btn-run-tf-evaluation');

    if (!statusEl || !resultsContainer || !tbody || !btn) return;

    btn.disabled = true;
    btn.textContent = '⏳ Evaluating...';
    statusEl.style.display = 'block';
    statusEl.style.background = 'rgba(59, 130, 246, 0.15)';
    statusEl.style.color = '#93c5fd';
    statusEl.innerHTML = `<strong>Running isolated evaluations for ${escapeHtml(symbol)} (${escapeHtml(strategy)}) across 5m, 10m, 15m, 1h, and 4h...</strong><br>
        <span style="font-size:0.75rem;color:var(--text-muted);">Fetching historical candles from Binance, simulating realistic fees and slippage, and performing 70/30 walk-forward testing. Please wait.</span>`;

    resultsContainer.style.display = 'none';

    try {
        const res = await fetch('/api/timeframe/evaluate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, strategy, days, timeframes: ['5m', '10m', '15m', '1h', '4h'] })
        });

        const data = await res.json();
        if (!res.ok || data.status === 'ERROR') {
            throw new Error(data.message || 'Evaluation failed');
        }

        const rows = data.timeframes || [];
        statusEl.style.background = 'rgba(16, 185, 129, 0.15)';
        statusEl.style.color = '#6ee7b7';
        const bestBanner = data.best_timeframe
            ? ` | Highest test Sharpe: <strong>${data.best_timeframe}</strong>`
            : ' | No timeframe passed all test criteria';
        statusEl.innerHTML = `✅ <strong>Evaluation Complete for ${escapeHtml(symbol)} (${days} days)</strong>${bestBanner}`;

        tbody.innerHTML = rows.map(r => {
            const hasError = r.status === 'error';
            if (hasError) {
                return `<tr>
                    <td class="mono font-bold">${escapeHtml(r.timeframe)}</td>
                    <td><span style="color:#ef4444;font-size:0.75rem;">Error</span></td>
                    <td colspan="6" style="color:var(--text-muted);">${escapeHtml(r.error || 'Failed')}</td>
                    <td><span style="color:#ef4444;font-size:0.75rem;">FAILED</span></td>
                </tr>`;
            }

            const pnlCls = r.total_return_pct > 0 ? 'positive' : (r.total_return_pct < 0 ? 'negative' : 'neutral');
            const passedBadge = r.passed
                ? `<span style="background:rgba(16,185,129,0.2);color:#34d399;padding:2px 6px;border-radius:4px;font-weight:700;font-size:0.75rem;">PASSED</span>`
                : `<span style="background:rgba(239,68,68,0.2);color:#f87171;padding:2px 6px;border-radius:4px;font-weight:700;font-size:0.75rem;">FAILED</span>`;

            return `<tr>
                <td class="mono font-bold">${escapeHtml(r.timeframe)}</td>
                <td><span style="color:#10b981;font-size:0.75rem;">Completed</span></td>
                <td class="mono">${r.total_trades}</td>
                <td class="mono">${r.win_rate}%</td>
                <td class="mono font-bold ${pnlCls}">${r.total_return_pct > 0 ? '+' : ''}${r.total_return_pct}%</td>
                <td class="mono negative">${r.max_drawdown_pct}%</td>
                <td class="mono font-bold ${r.sharpe_ratio >= 1.0 ? 'positive' : 'negative'}">${r.sharpe_ratio}</td>
                <td class="mono">${r.profit_factor}</td>
                <td>${passedBadge}</td>
            </tr>`;
        }).join('');

        resultsContainer.style.display = 'block';
    } catch (e) {
        console.error('[Dashboard] Error in controlled evaluation:', e);
        statusEl.style.background = 'rgba(239, 68, 68, 0.15)';
        statusEl.style.color = '#fca5a5';
        statusEl.innerHTML = `❌ <strong>Evaluation Error:</strong> ${escapeHtml(e.message)}`;
    } finally {
        btn.disabled = false;
        btn.textContent = '🚀 Run Controlled Evaluation';
    }
}

// ═══════════════════════════════════════════════════
// CHART PATTERNS — Detection, Confluence & Outcomes
// ═══════════════════════════════════════════════════
let patternsListenersInitialized = false;

function initPatternsListeners() {
    if (patternsListenersInitialized) return;
    patternsListenersInitialized = true;

    const btnRefresh = document.getElementById('btn-refresh-patterns');
    if (btnRefresh) {
        btnRefresh.addEventListener('click', () => {
            renderPatterns(lastState);
        });
    }

    const filters = ['patterns-filter-symbol', 'patterns-filter-category', 'patterns-filter-status'];
    filters.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', () => {
                renderPatterns(lastState);
            });
        }
    });
}

async function renderPatterns(state) {
    initPatternsListeners();

    const tbody = document.getElementById('patterns-tbody');
    const outcomesTbody = document.getElementById('patterns-outcomes-tbody');
    const activeCountEl = document.getElementById('patterns-active-count');
    const activeSubEl = document.getElementById('patterns-active-sub');
    const confirmedCountEl = document.getElementById('patterns-confirmed-count');
    const avgConfEl = document.getElementById('patterns-avg-confidence');
    const tableCountEl = document.getElementById('patterns-table-count');

    const filterSymbol = document.getElementById('patterns-filter-symbol')?.value || 'ALL';
    const filterCategory = document.getElementById('patterns-filter-category')?.value || 'ALL';
    const filterStatus = document.getElementById('patterns-filter-status')?.value || 'ALL';

    // 1. Fetch active patterns
    try {
        let activeUrl = '/api/patterns/active';
        if (filterSymbol !== 'ALL') {
            activeUrl += `?symbol=${encodeURIComponent(filterSymbol)}`;
        }
        const activeRes = await fetch(activeUrl);
        if (activeRes.ok) {
            const data = await activeRes.json();
            let patterns = data.patterns || [];

            // Apply category and status filters
            if (filterCategory !== 'ALL') {
                patterns = patterns.filter(p => p.pattern_type === filterCategory);
            }
            if (filterStatus !== 'ALL') {
                patterns = patterns.filter(p => p.status === filterStatus);
            }

            // Summary counters
            const formingCount = patterns.filter(p => p.status === 'FORMING').length;
            const confirmedCount = patterns.filter(p => p.status === 'CONFIRMED').length;
            if (activeCountEl) activeCountEl.textContent = patterns.length;
            if (activeSubEl) activeSubEl.textContent = `${formingCount} forming · ${confirmedCount} confirmed`;
            if (confirmedCountEl) confirmedCountEl.textContent = confirmedCount;

            if (patterns.length > 0) {
                const avgConf = patterns.reduce((sum, p) => sum + (p.confidence || 0), 0) / patterns.length;
                if (avgConfEl) avgConfEl.textContent = `${Math.round(avgConf * 100)}%`;
            } else {
                if (avgConfEl) avgConfEl.textContent = '—';
            }

            if (tableCountEl) tableCountEl.textContent = `${patterns.length} displayed`;

            if (tbody) {
                if (patterns.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;color:var(--text-muted);padding:2rem;">No patterns matching current filters.</td></tr>`;
                } else {
                    tbody.innerHTML = patterns.map(p => {
                        const isBullish = p.direction === 'BULLISH';
                        const isBearish = p.direction === 'BEARISH';
                        const dirBadge = isBullish
                            ? `<span style="background:rgba(34,197,94,0.15);color:#22c55e;padding:2px 8px;border-radius:4px;font-weight:700;font-size:0.75rem;">▲ Bullish</span>`
                            : isBearish
                            ? `<span style="background:rgba(239,68,68,0.15);color:#ef4444;padding:2px 8px;border-radius:4px;font-weight:700;font-size:0.75rem;">▼ Bearish</span>`
                            : `<span style="background:rgba(100,116,139,0.15);color:#94a3b8;padding:2px 8px;border-radius:4px;font-weight:700;font-size:0.75rem;">◆ Neutral</span>`;

                        const statusBadge = p.status === 'CONFIRMED'
                            ? `<span style="background:rgba(34,197,94,0.2);color:#34d399;padding:2px 8px;border-radius:4px;font-weight:700;font-size:0.72rem;">CONFIRMED</span>`
                            : p.status === 'FORMING'
                            ? `<span style="background:rgba(245,158,11,0.2);color:#fbbf24;padding:2px 8px;border-radius:4px;font-weight:700;font-size:0.72rem;">FORMING</span>`
                            : `<span style="background:rgba(239,68,68,0.2);color:#f87171;padding:2px 8px;border-radius:4px;font-weight:700;font-size:0.72rem;">INVALIDATED</span>`;

                        const breakout = p.breakout_level ? formatUSD(p.breakout_level) : '—';
                        const invalidation = p.invalidation_level ? formatUSD(p.invalidation_level) : '—';
                        const target = p.target_price ? formatUSD(p.target_price) : '—';
                        const confPct = Math.round((p.confidence || 0) * 100);

                        // Context confluence details
                        const ctx = p.context || {};
                        const regime = ctx.regime || 'neutral';
                        const trend = ctx.trend || 'neutral';
                        const rsi = ctx.rsi ? `RSI ${Math.round(ctx.rsi)}` : '';
                        const vol = ctx.volume_ratio ? `${ctx.volume_ratio.toFixed(1)}x Vol` : '';
                        const contextSummary = [trend, vol, rsi].filter(Boolean).join(' · ');

                        return `<tr>
                            <td class="mono" style="font-size:0.72rem;color:var(--text-muted);">${timeAgo(p.timestamp)}</td>
                            <td class="mono font-bold">${escapeHtml(p.symbol || '—')} <span style="font-size:0.7rem;color:var(--text-muted);">${escapeHtml(p.timeframe || '')}</span></td>
                            <td><strong>${escapeHtml((p.pattern_name || '').replace(/_/g, ' ').toUpperCase())}</strong></td>
                            <td>${dirBadge}</td>
                            <td>${statusBadge}</td>
                            <td class="mono">${breakout}</td>
                            <td class="mono" style="color:#ef4444;">${invalidation}</td>
                            <td class="mono" style="color:#22c55e;">${target}</td>
                            <td class="mono font-bold ${confPct >= 65 ? 'positive' : 'neutral'}">${confPct}%</td>
                            <td style="font-size:0.72rem;color:var(--text-secondary);">${escapeHtml(contextSummary || regime)}</td>
                        </tr>`;
                    }).join('');
                }
            }
        }
    } catch (e) {
        console.error('[Dashboard] Error rendering patterns:', e);
    }

    // 2. Fetch outcomes / scorecard
    try {
        const outcomesRes = await fetch('/api/patterns/outcomes?limit=50');
        if (outcomesRes.ok) {
            const data = await outcomesRes.json();
            const outcomes = data.outcomes || [];
            if (outcomesTbody) {
                if (outcomes.length === 0) {
                    outcomesTbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:1.5rem;">No historical pattern outcomes recorded yet. Live and backtest runs log outcomes automatically.</td></tr>`;
                } else {
                    // Group outcomes by pattern_name
                    const grouped = {};
                    outcomes.forEach(o => {
                        const name = o.pattern_name || 'unknown';
                        if (!grouped[name]) grouped[name] = [];
                        grouped[name].push(o);
                    });

                    outcomesTbody.innerHTML = Object.entries(grouped).map(([name, items]) => {
                        const samples = items.length;
                        const wins = items.filter(i => (i.net_return || 0) > 0).length;
                        const winRate = Math.round((wins / samples) * 100);
                        const avgReturn = items.reduce((s, i) => s + (i.net_return || 0), 0) / samples;
                        const avgMae = items.reduce((s, i) => s + (i.max_adverse_excursion || 0), 0) / samples;

                        return `<tr>
                            <td class="mono font-bold">${escapeHtml(name.replace(/_/g, ' ').toUpperCase())}</td>
                            <td class="mono">${samples}</td>
                            <td class="mono font-bold ${winRate >= 50 ? 'positive' : 'negative'}">${winRate}%</td>
                            <td class="mono ${avgReturn >= 0 ? 'positive' : 'negative'}">${formatPct(avgReturn * 100)}</td>
                            <td class="mono negative">-${(avgMae * 100).toFixed(2)}%</td>
                        </tr>`;
                    }).join('');
                }
            }
        }
    } catch (e) {
        console.error('[Dashboard] Error fetching pattern outcomes:', e);
    }
}

