const state = {
  payload: null,
  selectedStrategy: null,
};

const elements = {
  expirySelect: document.getElementById("expirySelect"),
  spotInput: document.getElementById("spotInput"),
  strategyLots: document.getElementById("strategyLots"),
  executionMode: document.getElementById("executionMode"),
  refreshDashboardBtn: document.getElementById("refreshDashboardBtn"),
  liveSpotBtn: document.getElementById("liveSpotBtn"),
  watchlistQuotesBtn: document.getElementById("watchlistQuotesBtn"),
  accountSnapshotBtn: document.getElementById("accountSnapshotBtn"),
  environmentInput: document.getElementById("environmentInput"),
  consumerKeyInput: document.getElementById("consumerKeyInput"),
  mobileInput: document.getElementById("mobileInput"),
  uccInput: document.getElementById("uccInput"),
  mpinInput: document.getElementById("mpinInput"),
  totpInput: document.getElementById("totpInput"),
  totpSecretInput: document.getElementById("totpSecretInput"),
  connectionChecklist: document.getElementById("connectionChecklist"),
  brokerStatus: document.getElementById("brokerStatus"),
  sdkStatus: document.getElementById("sdkStatus"),
  envStatus: document.getElementById("envStatus"),
  brokerMessage: document.getElementById("brokerMessage"),
  diagnosticsPanel: document.getElementById("diagnosticsPanel"),
  heroSummary: document.getElementById("heroSummary"),
  metricsGrid: document.getElementById("metricsGrid"),
  signalBadge: document.getElementById("signalBadge"),
  oiChart: document.getElementById("oiChart"),
  signalMeter: document.getElementById("signalMeter"),
  supportResistanceCard: document.getElementById("supportResistanceCard"),
  peSupports: document.getElementById("peSupports"),
  ceResistances: document.getElementById("ceResistances"),
  signalCards: document.getElementById("signalCards"),
  ideasTable: document.getElementById("ideasTable"),
  alertsList: document.getElementById("alertsList"),
  watchlistTable: document.getElementById("watchlistTable"),
  quoteFeed: document.getElementById("quoteFeed"),
  strategyOutlookFilter: document.getElementById("strategyOutlookFilter"),
  strategySelect: document.getElementById("strategySelect"),
  strategyDetail: document.getElementById("strategyDetail"),
  payoffChart: document.getElementById("payoffChart"),
  strategyLegs: document.getElementById("strategyLegs"),
  orderSymbol: document.getElementById("orderSymbol"),
  orderPayloadPreview: document.getElementById("orderPayloadPreview"),
  stagedBasket: document.getElementById("stagedBasket"),
  accountSnapshot: document.getElementById("accountSnapshot"),
  searchResults: document.getElementById("searchResults"),
  toast: document.getElementById("toast"),
};

function toast(message, tone = "ok") {
  elements.toast.textContent = message;
  elements.toast.className = `toast ${tone}`;
  setTimeout(() => {
    elements.toast.className = "toast hidden";
  }, 3500);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok || data.status === "error") {
    const error = new Error(data.message || "Request failed");
    error.data = data;
    throw error;
  }
  return data;
}

function asBadge(text, tone = "") {
  return `<span class="badge ${tone}">${text}</span>`;
}

function renderTable(container, rows, columns) {
  if (!rows || !rows.length) {
    container.innerHTML = `<p class="muted">No data available.</p>`;
    return;
  }

  const head = columns.map((column) => `<th>${column.label}</th>`).join("");
  const body = rows.map((row) => {
    const cells = columns.map((column) => {
      const value = typeof column.render === "function" ? column.render(row) : row[column.key];
      return `<td>${value ?? ""}</td>`;
    }).join("");
    return `<tr>${cells}</tr>`;
  }).join("");

  container.innerHTML = `<table class="table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function setActiveTab(name) {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tabTarget === name);
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.id === `tab-${name}`);
  });
}

function number(value, digits = 2) {
  return Number(value || 0).toFixed(digits);
}

function currentExpiry() {
  return elements.expirySelect.value || "";
}

function currentSpot() {
  return parseFloat(elements.spotInput.value || "0");
}

function currentLots() {
  return parseInt(elements.strategyLots.value || "1", 10);
}

function gatherCredentials() {
  return {
    environment: elements.environmentInput.value.trim(),
    consumer_key: elements.consumerKeyInput.value.trim(),
    mobile_number: elements.mobileInput.value.trim(),
    ucc: elements.uccInput.value.trim(),
    mpin: elements.mpinInput.value.trim(),
    totp: elements.totpInput.value.trim(),
    totp_secret: elements.totpSecretInput.value.trim(),
  };
}

function connectionChecklistRows(payload = state.payload) {
  const brokerReady = Boolean(payload?.broker?.broker_ready);
  const credentials = gatherCredentials();
  const hasCore = Boolean(credentials.consumer_key && credentials.mobile_number && credentials.ucc && credentials.mpin);
  const hasTotpPath = Boolean(credentials.totp || credentials.totp_secret || payload?.auto_totp);
  const diagnostic = payload?.last_diagnostic;
  const readyToConnect = Boolean(diagnostic?.ready_to_connect || brokerReady);
  return [
    { label: "Credentials", ok: hasCore, detail: hasCore ? "Core broker fields are present." : "Consumer key, mobile, UCC, and MPIN are required." },
    { label: "TOTP", ok: hasTotpPath, detail: hasTotpPath ? "A TOTP or secret is available." : "Paste a fresh TOTP or click Generate TOTP." },
    { label: "SDK", ok: Boolean(payload?.broker?.sdk_installed), detail: payload?.broker?.sdk_installed ? "Kotak Neo SDK is installed." : "Install neo_api_client in this environment." },
    { label: "Session", ok: brokerReady, detail: brokerReady ? "Broker session is active." : readyToConnect ? "Ready to connect." : "Run Test to validate the next step." },
  ];
}

function renderConnectionChecklist(payload = state.payload) {
  const rows = connectionChecklistRows(payload);
  elements.connectionChecklist.innerHTML = rows.map((row) => `
    <div class="check-row ${row.ok ? "ok" : "waiting"}">
      <span>${row.label}</span>
      <strong>${row.ok ? "Ready" : "Pending"}</strong>
      <p class="muted">${row.detail}</p>
    </div>
  `).join("");
}

async function loadDashboard(options = {}) {
  const expiry = options.expiry ?? currentExpiry();
  const spot = options.spot ?? currentSpot();
  const query = new URLSearchParams();
  if (expiry) query.set("expiry", expiry);
  if (spot) query.set("spot", String(spot));

  const payload = await fetchJson(`/api/bootstrap?${query.toString()}`);
  state.payload = payload;
  if (payload.auto_totp && !elements.totpInput.value) {
    elements.totpInput.value = payload.auto_totp;
  }
  hydrateDashboard(payload, { preserveExpiry: Boolean(expiry) });
}

function hydrateDashboard(payload, options = {}) {
  const { metrics, signals, signal_score, stance, alerts, ideas, watchlist, strategies, staged_legs, pe_supports, ce_resistances, broker, credentials, last_diagnostic } = payload;

  if (!options.preserveExpiry) {
    elements.expirySelect.innerHTML = payload.expiries.map((expiry) => `<option value="${expiry}" ${expiry === payload.selected_expiry ? "selected" : ""}>${expiry}</option>`).join("");
  }
  elements.expirySelect.value = payload.selected_expiry || "";
  elements.spotInput.value = metrics.spot;

  elements.brokerStatus.textContent = broker.broker_ready ? "Connected" : "Disconnected";
  elements.sdkStatus.textContent = broker.sdk_installed ? "Installed" : "Missing";
  elements.brokerMessage.innerHTML = broker.last_broker_message ? `<p class="muted">${broker.last_broker_message}</p>` : "";
  elements.envStatus.innerHTML = Object.entries(broker.env_status).map(([key, value]) => `
    <div class="status-row">
      <span>${key.replace("KOTAK_", "")}</span>
      <strong>${value ? "Ready" : "Missing"}</strong>
    </div>
  `).join("");
  if (credentials) {
    elements.environmentInput.value = credentials.environment || "prod";
    if (!elements.consumerKeyInput.value) elements.consumerKeyInput.value = credentials.consumer_key || "";
    if (!elements.mobileInput.value) elements.mobileInput.value = credentials.mobile_number || "";
    if (!elements.uccInput.value) elements.uccInput.value = credentials.ucc || "";
    if (!elements.mpinInput.value) elements.mpinInput.value = credentials.mpin || "";
    if (!elements.totpSecretInput.value) elements.totpSecretInput.value = credentials.totp_secret || "";
  }
  renderConnectionChecklist(payload);
  renderDiagnostics(last_diagnostic);

  elements.heroSummary.textContent = `Support ${metrics.support}, resistance ${metrics.resistance}, max pain ${metrics.max_pain}, signal score ${signal_score}.`;
  elements.signalBadge.textContent = stance;

  const metricCards = [
    ["Reference Spot", number(metrics.spot), "Live or manual anchor"],
    ["ATM Strike", number(metrics.atm, 0), `${metrics.available_strikes} strikes`],
    ["PCR", number(metrics.total_pcr), "Whole chain put/call ratio"],
    ["ATM PCR", number(metrics.atm_pcr), "Near-the-money pressure"],
    ["Max Pain", number(metrics.max_pain, 0), `${number(metrics.max_pain_gap_pct)}% gap`],
    ["Support", number(metrics.support, 0), "PE writers zone"],
    ["Resistance", number(metrics.resistance, 0), "CE writers zone"],
    ["Lot Size", number(metrics.lot_size, 0), "Execution unit"],
  ];
  elements.metricsGrid.innerHTML = metricCards.map(([label, value, delta]) => `
    <article class="metric-card">
      <div class="metric-label">${label}</div>
      <div class="metric-value">${value}</div>
      <div class="metric-delta">${delta}</div>
    </article>
  `).join("");

  renderOiChart(watchlist, metrics);
  renderSignalMeter(signal_score);
  elements.supportResistanceCard.innerHTML = `
    <div class="status-row"><span>Support</span><strong>${number(metrics.support, 0)}</strong></div>
    <div class="status-row"><span>Resistance</span><strong>${number(metrics.resistance, 0)}</strong></div>
    <div class="status-row"><span>Max Pain</span><strong>${number(metrics.max_pain, 0)}</strong></div>
    <div class="status-row"><span>Stance</span><strong>${stance}</strong></div>
  `;

  renderTable(elements.peSupports, pe_supports, [
    { key: "strike", label: "Strike" },
    { key: "open_interest", label: "Open Interest" },
  ]);
  renderTable(elements.ceResistances, ce_resistances, [
    { key: "strike", label: "Strike" },
    { key: "open_interest", label: "Open Interest" },
  ]);

  elements.signalCards.innerHTML = signals.map((signal) => `
    <article class="signal-card">
      <div class="signal-title">${signal.name}</div>
      <div class="metric-value">${signal.bias}</div>
      <div class="metric-delta">${signal.confidence}% confidence</div>
      <p class="muted">${signal.trigger}</p>
      <p>${signal.detail}</p>
    </article>
  `).join("");

  renderTable(elements.ideasTable, ideas, [
    { key: "ptrdsymbol", label: "Symbol" },
    { key: "option_side", label: "Type", render: (row) => asBadge(row.option_side, row.option_side === "CE" ? "sell" : "buy") },
    { key: "strike", label: "Strike" },
    { key: "open_interest", label: "Open Interest" },
    { key: "score", label: "Score" },
  ]);

  elements.alertsList.innerHTML = alerts.length ? alerts.map((alert) => `
    <div class="status-row">
      <span>${asBadge(alert.status, alert.status === "TRIGGERED" ? "error" : alert.status === "SETUP" ? "ok" : "waiting")}</span>
      <strong>${alert.title}</strong>
    </div>
    <p class="muted">${alert.message}</p>
  `).join("") : `<p class="muted">No alerts armed.</p>`;

  renderTable(elements.watchlistTable, watchlist, [
    { key: "ptrdsymbol", label: "Symbol" },
    { key: "option_side", label: "Type", render: (row) => asBadge(row.option_side, row.option_side === "CE" ? "sell" : "buy") },
    { key: "strike", label: "Strike" },
    { key: "open_interest", label: "Open Interest" },
    { key: "instrument_token", label: "Token" },
  ]);

  hydrateStrategies(strategies);
  hydrateOrderSymbols(watchlist);
  hydrateStaged(staged_legs || []);
  updateOrderPreview();
}

function renderDiagnostics(diagnostic, message = "") {
  if (!diagnostic && !message) {
    elements.diagnosticsPanel.innerHTML = "";
    return;
  }
  if (!diagnostic) {
    elements.diagnosticsPanel.innerHTML = `<p class="muted">${message}</p>`;
    return;
  }
  const quote = diagnostic.quote_test || {};
  elements.diagnosticsPanel.innerHTML = `
    ${message ? `<p class="muted">${message}</p>` : ""}
    <div class="status-row"><span>Environment</span><strong>${diagnostic.environment || "-"}</strong></div>
    <div class="status-row"><span>SDK</span><strong>${diagnostic.sdk_installed ? "Installed" : "Missing"}</strong></div>
    <div class="status-row"><span>Missing</span><strong>${diagnostic.missing_fields && diagnostic.missing_fields.length ? diagnostic.missing_fields.join(", ") : "None"}</strong></div>
    <div class="status-row"><span>Client</span><strong>${diagnostic.client_created ? "Created" : "Not created"}</strong></div>
    <div class="status-row"><span>Ready</span><strong>${diagnostic.ready_to_connect ? "Yes" : "No"}</strong></div>
    <div class="status-row"><span>Quote Test</span><strong>${quote.error ? "Failed" : "OK"}</strong></div>
    ${quote.spot ? `<div class="status-row"><span>Live Spot</span><strong>${quote.spot}</strong></div>` : ""}
    ${quote.error ? `<p class="muted">${quote.error}</p>` : ""}
    ${diagnostic.notes && diagnostic.notes.length ? `<div class="stack">${diagnostic.notes.map((note) => `<p class="muted">${note}</p>`).join("")}</div>` : ""}
  `;
}

function renderOiChart(rows, metrics) {
  if (!rows.length) {
    elements.oiChart.innerHTML = `<p class="muted">No OI structure available.</p>`;
    return;
  }
  const grouped = {};
  rows.forEach((row) => {
    if (!grouped[row.strike]) grouped[row.strike] = { strike: row.strike, CE: 0, PE: 0 };
    grouped[row.strike][row.option_side] = row.open_interest;
  });
  const list = Object.values(grouped).sort((a, b) => a.strike - b.strike);
  const maxOi = Math.max(...list.map((row) => Math.max(row.CE, row.PE, 1)));
  elements.oiChart.innerHTML = list.map((row) => {
    const ceWidth = (row.CE / maxOi) * 50;
    const peWidth = (row.PE / maxOi) * 50;
    return `
      <div class="bar-row">
        <span>${row.CE.toFixed(0)}</span>
        <div class="bar-stack">
          <div class="bar-negative" style="width:${ceWidth}%"></div>
          <div class="bar-positive" style="width:${peWidth}%"></div>
        </div>
        <span>${row.strike}</span>
      </div>
    `;
  }).join("");
}

function renderSignalMeter(score) {
  const normalized = ((score + 4) / 8) * 100;
  elements.signalMeter.innerHTML = `
    <div class="meter-track">
      <div class="meter-thumb" style="left: calc(${normalized}% - 12px)"></div>
    </div>
    <strong>Signal Score ${number(score)}</strong>
  `;
}

function hydrateStrategies(strategies) {
  state.strategies = strategies;
  const outlooks = [...new Set(strategies.map((item) => item.outlook))];
  const previousOutlook = elements.strategyOutlookFilter.value;
  elements.strategyOutlookFilter.innerHTML = outlooks.map((outlook) => `<option value="${outlook}" ${outlook === previousOutlook ? "selected" : ""}>${outlook}</option>`).join("");
  if (!elements.strategyOutlookFilter.value && outlooks.length) {
    elements.strategyOutlookFilter.value = outlooks[0];
  }
  syncStrategySelect();
}

function syncStrategySelect() {
  const outlook = elements.strategyOutlookFilter.value;
  const filtered = state.strategies.filter((item) => item.outlook === outlook);
  const previousStrategy = elements.strategySelect.value;
  elements.strategySelect.innerHTML = filtered.map((item) => `<option value="${item.name}" ${item.name === previousStrategy ? "selected" : ""}>${item.name}</option>`).join("");
  if (!elements.strategySelect.value && filtered.length) {
    elements.strategySelect.value = filtered[0].name;
  }
  state.selectedStrategy = filtered.find((item) => item.name === elements.strategySelect.value) || filtered[0] || null;
  renderSelectedStrategy();
}

function renderSelectedStrategy() {
  const strategy = state.selectedStrategy;
  if (!strategy) {
    elements.strategyDetail.innerHTML = `<p class="muted">No strategy available.</p>`;
    elements.strategyLegs.innerHTML = "";
    elements.payoffChart.innerHTML = "";
    return;
  }
  elements.strategyDetail.innerHTML = `
    <p>${strategy.description}</p>
    <div class="status-row"><span>Outlook</span><strong>${strategy.outlook}</strong></div>
    <div class="status-row"><span>Legs</span><strong>${strategy.legs.length}</strong></div>
  `;
  renderTable(elements.strategyLegs, strategy.legs, [
    { key: "action", label: "Action", render: (row) => asBadge(row.action, row.action === "BUY" ? "buy" : "sell") },
    { key: "side", label: "Side" },
    { key: "strike", label: "Strike" },
    { key: "premium", label: "Premium" },
  ]);
  renderPayoff(strategy.payoff);
}

function renderPayoff(points) {
  if (!points || !points.length) {
    elements.payoffChart.innerHTML = "";
    return;
  }
  const width = 800;
  const height = 320;
  const padding = 28;
  const xValues = points.map((point) => point.underlying);
  const yValues = points.map((point) => point.payoff);
  const minX = Math.min(...xValues);
  const maxX = Math.max(...xValues);
  const minY = Math.min(...yValues);
  const maxY = Math.max(...yValues);
  const xScale = (value) => padding + ((value - minX) / Math.max(maxX - minX, 1)) * (width - padding * 2);
  const yScale = (value) => height - padding - ((value - minY) / Math.max(maxY - minY, 1)) * (height - padding * 2);
  const polyline = points.map((point) => `${xScale(point.underlying)},${yScale(point.payoff)}`).join(" ");
  const zeroY = yScale(0);
  elements.payoffChart.innerHTML = `
    <line x1="${padding}" y1="${zeroY}" x2="${width - padding}" y2="${zeroY}" stroke="rgba(255,255,255,0.25)" stroke-dasharray="6 6"></line>
    <polyline fill="none" stroke="url(#payoffGradient)" stroke-width="4" points="${polyline}"></polyline>
    <defs>
      <linearGradient id="payoffGradient" x1="0%" x2="100%" y1="0%" y2="0%">
        <stop offset="0%" stop-color="#ff8f5c"></stop>
        <stop offset="100%" stop-color="#35c2a1"></stop>
      </linearGradient>
    </defs>
  `;
}

function hydrateOrderSymbols(watchlist) {
  const previous = elements.orderSymbol.value;
  elements.orderSymbol.innerHTML = watchlist.map((row) => `<option value="${row.ptrdsymbol}" ${row.ptrdsymbol === previous ? "selected" : ""}>${row.ptrdsymbol}</option>`).join("");
  if (!elements.orderSymbol.value && watchlist.length) {
    elements.orderSymbol.value = watchlist[0].ptrdsymbol;
  }
}

function buildOrderPayload() {
  if (!state.payload) return null;
  const form = document.getElementById("orderForm");
  const data = new FormData(form);
  const symbol = data.get("symbol");
  const row = state.payload.watchlist.find((item) => item.ptrdsymbol === symbol) || state.payload.ideas.find((item) => item.ptrdsymbol === symbol);
  if (!row) return null;
  const lots = parseInt(data.get("lots"), 10);
  const quantity = lots * parseInt(row.lot_size || state.payload.metrics.lot_size, 10);
  return {
    payload: {
      exchange_segment: row.exchange_segment,
      product: data.get("product"),
      price: data.get("order_type") === "MKT" ? "0" : String(parseFloat(data.get("price") || "0")),
      order_type: data.get("order_type"),
      quantity: String(quantity),
      validity: "DAY",
      trading_symbol: symbol,
      transaction_type: data.get("transaction_type"),
      amo: data.get("amo"),
      trigger_price: String(parseFloat(data.get("trigger_price") || "0")),
    },
    instrument_token: row.instrument_token,
  };
}

function updateOrderPreview() {
  const order = buildOrderPayload();
  elements.orderPayloadPreview.textContent = order ? JSON.stringify(order.payload, null, 2) : "Select a symbol to preview payload.";
}

function hydrateStaged(staged) {
  renderTable(elements.stagedBasket, staged, [
    { key: "symbol", label: "Symbol" },
    { key: "action", label: "Action", render: (row) => asBadge(row.action, row.action === "BUY" ? "buy" : "sell") },
    { key: "quantity", label: "Qty" },
    { key: "price", label: "Price" },
    { key: "product", label: "Product" },
  ]);
}

function renderAccountSnapshot(payload) {
  if (payload.status !== "ok") {
    elements.accountSnapshot.innerHTML = `<p class="muted">${payload.message || "Broker not connected."}</p>`;
    return;
  }
  const sections = Object.entries(payload.snapshot).map(([name, block]) => {
    const title = name.replaceAll("_", " ");
    const content = block.records && block.records.length
      ? buildMiniTable(block.records)
      : `<pre class="code-block">${JSON.stringify(block.raw, null, 2)}</pre>`;
    return `<section class="panel"><h4>${title}</h4>${block.error ? `<p class="muted">${block.error}</p>` : content}</section>`;
  });
  elements.accountSnapshot.innerHTML = sections.join("");
}

function buildMiniTable(rows) {
  const keys = Object.keys(rows[0]).slice(0, 6);
  const head = keys.map((key) => `<th>${key}</th>`).join("");
  const body = rows.map((row) => `<tr>${keys.map((key) => `<td>${row[key] ?? ""}</td>`).join("")}</tr>`).join("");
  return `<table class="table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

async function handleLogin() {
  try {
    const result = await fetchJson("/api/login", {
      method: "POST",
      body: JSON.stringify(gatherCredentials()),
    });
    toast(result.message || "Broker connected");
    await loadDashboard({ preserveExpiry: true });
  } catch (error) {
    elements.brokerMessage.innerHTML = `<p class="muted">${error.message}</p>`;
    if (error.data?.diagnostic) {
      renderDiagnostics(error.data.diagnostic, error.message);
    }
    toast(error.message, "error");
  }
}

async function handleGenerateTotp() {
  try {
    const result = await fetchJson("/api/totp", {
      method: "POST",
      body: JSON.stringify(gatherCredentials()),
    });
    elements.totpInput.value = result.totp || "";
    if (state.payload) state.payload.auto_totp = result.totp || "";
    renderConnectionChecklist(state.payload);
    toast(result.message || "Fresh TOTP generated");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function handleDiagnostics() {
  try {
    const result = await fetchJson("/api/diagnostics", {
      method: "POST",
      body: JSON.stringify(gatherCredentials()),
    });
    if (state.payload) {
      state.payload.last_diagnostic = result.diagnostic;
    }
    renderDiagnostics(result.diagnostic, result.message);
    renderConnectionChecklist(state.payload);
    toast(result.message || "Connection test passed");
  } catch (error) {
    if (error.data?.diagnostic) {
      if (state.payload) {
        state.payload.last_diagnostic = error.data.diagnostic;
      }
      renderDiagnostics(error.data.diagnostic, error.message);
      renderConnectionChecklist(state.payload);
    }
    toast(error.message, "error");
  }
}

async function handleLogout() {
  const result = await fetchJson("/api/logout", { method: "POST" });
  toast(result.message || "Broker disconnected");
  await loadDashboard({ preserveExpiry: true });
}

async function handleLiveSpot() {
  const result = await fetchJson("/api/quotes/spot");
  if (result.spot) {
    elements.spotInput.value = result.spot;
    toast(`Reference spot updated to ${result.spot}`);
    await loadDashboard({ expiry: currentExpiry(), spot: result.spot });
  }
  renderTable(elements.quoteFeed, result.records || [], Object.keys((result.records || [])[0] || {}).map((key) => ({ key, label: key })));
}

async function handleWatchlistQuotes() {
  const result = await fetchJson(`/api/quotes/watchlist?expiry=${encodeURIComponent(currentExpiry())}&spot=${encodeURIComponent(currentSpot())}`);
  renderTable(elements.quoteFeed, result.records || [], Object.keys((result.records || [])[0] || {}).map((key) => ({ key, label: key })));
  toast("Watchlist quotes refreshed");
}

async function handleAccountSnapshot() {
  const result = await fetchJson("/api/account");
  renderAccountSnapshot(result);
  toast("Account snapshot loaded");
}

async function handleStageStrategy() {
  const result = await fetchJson("/api/strategy/stage", {
    method: "POST",
    body: JSON.stringify({
      strategy_name: elements.strategySelect.value,
      lots: currentLots(),
      expiry: currentExpiry(),
      spot: currentSpot(),
    }),
  });
  hydrateStaged(result.staged_legs || []);
  toast(result.missing && result.missing.length ? `Staged with missing contracts: ${result.missing.join(", ")}` : "Strategy staged");
  await loadDashboard({ preserveExpiry: true });
}

async function handleClearStaged() {
  await fetchJson("/api/strategy/clear", { method: "POST" });
  toast("Staged basket cleared");
  await loadDashboard({ preserveExpiry: true });
}

async function handleExecuteStaged() {
  if (elements.executionMode.value !== "Live") {
    toast("Execution mode is Paper. Switch to Live first.", "error");
    return;
  }
  const result = await fetchJson("/api/strategy/execute", { method: "POST" });
  toast("Staged basket execution submitted");
  elements.quoteFeed.innerHTML = `<pre class="code-block">${JSON.stringify(result.results, null, 2)}</pre>`;
}

async function handleMarginCheck() {
  const order = buildOrderPayload();
  if (!order) return;
  const result = await fetchJson("/api/orders/margin", {
    method: "POST",
    body: JSON.stringify(order),
  });
  elements.quoteFeed.innerHTML = `<pre class="code-block">${JSON.stringify(result.preview, null, 2)}</pre>`;
  toast("Margin preview loaded");
}

async function handleOrderSubmit(event) {
  event.preventDefault();
  if (elements.executionMode.value !== "Live") {
    toast("Execution mode is Paper. Payload preview only.", "waiting");
    return;
  }
  if (!document.getElementById("liveOrderConfirm").checked) {
    toast("Confirm the live order checkbox first.", "error");
    return;
  }
  const order = buildOrderPayload();
  const result = await fetchJson("/api/orders/place", {
    method: "POST",
    body: JSON.stringify(order),
  });
  elements.quoteFeed.innerHTML = `<pre class="code-block">${JSON.stringify(result.response, null, 2)}</pre>`;
  toast("Order submitted");
}

async function handleModify(event) {
  event.preventDefault();
  if (elements.executionMode.value !== "Live") {
    toast("Execution mode is Paper. Modify request blocked.", "waiting");
    return;
  }
  const formData = new FormData(event.target);
  const payload = Object.fromEntries(formData.entries());
  const result = await fetchJson("/api/orders/modify", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  elements.quoteFeed.innerHTML = `<pre class="code-block">${JSON.stringify(result.response, null, 2)}</pre>`;
  toast("Modify request sent");
}

async function handleCancel(event) {
  event.preventDefault();
  if (elements.executionMode.value !== "Live") {
    toast("Execution mode is Paper. Cancel request blocked.", "waiting");
    return;
  }
  const formData = new FormData(event.target);
  const payload = Object.fromEntries(formData.entries());
  const result = await fetchJson("/api/orders/cancel", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  elements.quoteFeed.innerHTML = `<pre class="code-block">${JSON.stringify(result.response, null, 2)}</pre>`;
  toast("Cancel request sent");
}

async function handleAlertSubmit(event) {
  event.preventDefault();
  const formData = new FormData(event.target);
  const payload = Object.fromEntries(formData.entries());
  payload.threshold = parseFloat(payload.threshold || "0");
  await fetchJson("/api/alerts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  toast("Alert armed");
  event.target.reset();
  await loadDashboard({ preserveExpiry: true });
}

async function handleSearch(event) {
  event.preventDefault();
  const formData = new FormData(event.target);
  const params = new URLSearchParams(formData.entries());
  const result = await fetchJson(`/api/search-scrip?${params.toString()}`);
  renderTable(elements.searchResults, result.records || [], Object.keys((result.records || [])[0] || {}).map((key) => ({ key, label: key })));
}

function wireEvents() {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => setActiveTab(button.dataset.tabTarget));
  });

  elements.refreshDashboardBtn.addEventListener("click", () => loadDashboard({ expiry: currentExpiry(), spot: currentSpot() }));
  elements.liveSpotBtn.addEventListener("click", handleLiveSpot);
  elements.watchlistQuotesBtn.addEventListener("click", handleWatchlistQuotes);
  document.getElementById("watchlistRefreshInlineBtn").addEventListener("click", handleWatchlistQuotes);
  elements.accountSnapshotBtn.addEventListener("click", handleAccountSnapshot);
  document.getElementById("loginBtn").addEventListener("click", handleLogin);
  document.getElementById("diagnosticsBtn").addEventListener("click", handleDiagnostics);
  document.getElementById("totpBtn").addEventListener("click", handleGenerateTotp);
  document.getElementById("logoutBtn").addEventListener("click", handleLogout);
  document.getElementById("stageStrategyBtn").addEventListener("click", handleStageStrategy);
  document.getElementById("clearStagedBtn").addEventListener("click", handleClearStaged);
  document.getElementById("executeStagedBtn").addEventListener("click", handleExecuteStaged);
  document.getElementById("marginCheckBtn").addEventListener("click", handleMarginCheck);
  document.getElementById("orderForm").addEventListener("submit", handleOrderSubmit);
  document.getElementById("modifyForm").addEventListener("submit", handleModify);
  document.getElementById("cancelForm").addEventListener("submit", handleCancel);
  document.getElementById("alertForm").addEventListener("submit", handleAlertSubmit);
  document.getElementById("searchForm").addEventListener("submit", handleSearch);
  elements.strategyOutlookFilter.addEventListener("change", syncStrategySelect);
  elements.strategySelect.addEventListener("change", () => {
    state.selectedStrategy = state.strategies.find((item) => item.name === elements.strategySelect.value) || null;
    renderSelectedStrategy();
  });
  elements.orderSymbol.addEventListener("change", updateOrderPreview);
  document.getElementById("orderForm").addEventListener("input", updateOrderPreview);
  [
    elements.environmentInput,
    elements.consumerKeyInput,
    elements.mobileInput,
    elements.uccInput,
    elements.mpinInput,
    elements.totpInput,
    elements.totpSecretInput,
  ].forEach((field) => field.addEventListener("input", () => renderConnectionChecklist(state.payload)));
}

async function init() {
  wireEvents();
  try {
    await loadDashboard();
    await handleAccountSnapshot().catch(() => {});
  } catch (error) {
    toast(error.message, "error");
  }
}

init();
