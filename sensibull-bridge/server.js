const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const cors = require('cors');
const lib = require('./lib.js');

const SENSIBULL_URL = 'wss://wsrelay.sensibull.com/broker/1?consumerType=platform_pro';
const BRIDGE_PORT = 3001;
const RECONNECT_DELAY = 5000;

const INDEX_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY"];

function getTokenForSymbol(symbol) {
    const instr = lib.instruments.find(i => i.tradingsymbol === symbol);
    return instr ? instr.instrument_token : null;
}

function getSymbolName(token) {
    const instr = lib.instruments.find(i => i.instrument_token === token);
    return instr ? instr.tradingsymbol : String(token);
}

// In-memory state store
const state = {
    underlyingStats: {},
    optionChains: {},
    quotes: {},
    lastUpdate: null,
};

let sensibullWs = null;
let reconnectTimer = null;

function connectSensibull() {
    if (sensibullWs) {
        try { sensibullWs.close(); } catch(e) {}
        sensibullWs = null;
    }

    const ws = new WebSocket(SENSIBULL_URL, {
        headers: {
            'Accept-Encoding': 'gzip, deflate',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/119.0',
            'Origin': 'https://web.sensibull.com'
        },
        handshakeTimeout: 10000
    });

    ws.on('open', () => {
        console.log('[sensibull] Connected');
        reconnectTimer = null;

        const expiry = lib.expiries().shift();
        const tokens = INDEX_SYMBOLS.map(s => getTokenForSymbol(s)).filter(Boolean);

        ws.send(JSON.stringify({
            msgCommand: "subscribe", dataSource: "underlying-stats",
            brokerId: 1, tokens, underlyingExpiry: [], uniqueId: ""
        }));

        ws.send(JSON.stringify({
            msgCommand: "subscribe", dataSource: "quote-binary",
            brokerId: 1, tokens: INDEX_SYMBOLS, underlyingExpiry: [], uniqueId: ""
        }));

        ws.send(JSON.stringify({
            msgCommand: "subscribe", dataSource: "quote-binary",
            brokerId: 1, tokens, underlyingExpiry: [], uniqueId: ""
        }));

        const chainExpiry = lib.expiries().shift();
        const chainInstrs = INDEX_SYMBOLS.map(s => lib.instruments.find(i => i.tradingsymbol === s)).filter(Boolean);
        const chainTokens = chainInstrs.map(i => i.instrument_token);

        // Try multiple subscription formats for option chain
        ws.send(JSON.stringify({
            msgCommand: "subscribe", dataSource: "option-chain",
            brokerId: 1, tokens: [],
            underlyingExpiry: chainInstrs.map(i => ({ underlying: i, expiry: chainExpiry })),
            uniqueId: ""
        }));

        // Also try with just token numbers as underlying
        ws.send(JSON.stringify({
            msgCommand: "subscribe", dataSource: "option-chain",
            brokerId: 1, tokens: [],
            underlyingExpiry: chainTokens.map(t => ({ underlying: t, expiry: chainExpiry })),
            uniqueId: ""
        }));

        // Try with tokens field instead
        ws.send(JSON.stringify({
            msgCommand: "subscribe", dataSource: "option-chain",
            brokerId: 1, tokens: chainTokens, underlyingExpiry: [],
            uniqueId: ""
        }));

        console.log('[sensibull] Subscribed to stats, quotes, chains for', INDEX_SYMBOLS.join(','));
    });

    ws.on('message', (data) => {
        try {
            const raw = new Uint8Array(data);
            if (raw.length <= 2) return;

            const decoded = lib.decodeData(data);
            state.lastUpdate = new Date().toISOString();

            switch (decoded.kind) {
                case 5: // UNDERLYING_STATS
                    Object.assign(state.underlyingStats, decoded.payload);
                    break;
                case 3: // OPTION_CHAIN
                    const chain = decoded.payload;
                    if (chain?.data) {
                        for (const [tk, exps] of Object.entries(chain.data)) {
                            for (const [ex, oc] of Object.entries(exps)) {
                                const key = `${tk}:${ex}`;
                                state.optionChains[key] = oc;
                            }
                        }
                    }
                    break;
                case 1: // QUOTE
                    const p = decoded.payload;
                    if (p?.instrumentToken) {
                        state.quotes[p.instrumentToken] = p;
                    }
                    break;
            }
        } catch (err) {
            // ignore decode errors for now
        }
    });

    ws.on('close', (code, reason) => {
        console.log(`[sensibull] Disconnected: code=${code}`);
        sensibullWs = null;
        if (!reconnectTimer) {
            reconnectTimer = setTimeout(connectSensibull, RECONNECT_DELAY);
        }
    });

    ws.on('error', (err) => {
        console.error('[sensibull] Error:', err.message);
    });

    sensibullWs = ws;
}

// Express app
const app = express();
app.use(cors());

app.get('/api/health', (req, res) => {
    res.json({
        status: sensibullWs?.readyState === WebSocket.OPEN ? 'connected' : 'disconnected',
        lastUpdate: state.lastUpdate,
        stats: Object.keys(state.underlyingStats).length,
        chains: Object.keys(state.optionChains).length,
        quotes: Object.keys(state.quotes).length,
    });
});

app.get('/api/stats', (req, res) => {
    res.json(state.underlyingStats);
});

app.get('/api/stats/:symbol', (req, res) => {
    const token = getTokenForSymbol(req.params.symbol.toUpperCase());
    if (!token) return res.status(404).json({ error: 'Symbol not found' });
    res.json(state.underlyingStats[token] || { error: 'No data yet' });
});

app.get('/api/option-chains', (req, res) => {
    res.json(state.optionChains);
});

app.get('/api/option-chain/:symbol', (req, res) => {
    const sym = req.params.symbol.toUpperCase();
    const token = getTokenForSymbol(sym);
    if (!token) return res.status(404).json({ error: 'Symbol not found' });

    const expiry = lib.expiries().shift();
    const key = `${token}:${expiry}`;
    const data = state.optionChains[key];
    if (!data) return res.json({ error: 'No chain data yet', key });
    res.json({ symbol: sym, token, expiry, ...data });
});

app.get('/api/quotes', (req, res) => {
    const result = {};
    for (const [tk, q] of Object.entries(state.quotes)) {
        result[getSymbolName(parseInt(tk))] = {
            ltp: q.lastPrice,
            change: q.change,
            volume: q.volume,
            oi: q.oi,
            ohlc: q.ohlc,
            timestamp: q.timestamp,
        };
    }
    res.json(result);
});

app.get('/api/quote/:symbol', (req, res) => {
    const token = getTokenForSymbol(req.params.symbol.toUpperCase());
    if (!token) return res.status(404).json({ error: 'Symbol not found' });
    const q = state.quotes[token];
    if (!q) return res.json({ error: 'No quote data yet' });
    res.json(q);
});

const server = http.createServer(app);
server.listen(BRIDGE_PORT, () => {
    console.log(`[bridge] Server running on http://localhost:${BRIDGE_PORT}`);
    connectSensibull();
});
