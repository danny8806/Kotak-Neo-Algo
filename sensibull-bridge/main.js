

const WebSocket = require('ws');
const lib = require('./lib.js');
const serverUrl = 'wss://wsrelay.sensibull.com/broker/1?consumerType=platform_pro';

const customHeaders = {
    'Accept-Encoding': 'gzip, deflate',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/119.0',
    'Origin': 'https://web.sensibull.com'
};

// Only subscribe to major indices for testing
const indices = ["NIFTY", "BANKNIFTY", "FINNIFTY"];

function getTokenForSymbol(symbol) {
    const instr = lib.instruments.find(i => i.tradingsymbol === symbol);
    return instr ? instr.instrument_token : null;
}

function getSymbolName(token) {
    const instr = lib.instruments.find(i => i.instrument_token === token);
    return instr ? instr.tradingsymbol : token;
}

const expiry = lib.expiries().shift();
console.log(`Using expiry: ${expiry}`);

const ws = new WebSocket(serverUrl, {
    headers: customHeaders,
    handshakeTimeout: 10000
});

ws.on('open', () => {
    console.log('Connected to Sensibull WebSocket');

    const indexTokens = indices.map(s => getTokenForSymbol(s)).filter(Boolean);
    const indexNames = indices;
    console.log('Index tokens:', indexTokens);

    // Subscribe to underlying stats
    ws.send(JSON.stringify({
        msgCommand: "subscribe", dataSource: "underlying-stats",
        brokerId: 1, tokens: indexTokens, underlyingExpiry: [], uniqueId: ""
    }));
    console.log('Sent underlying-stats');

    // Subscribe to quote-binary (both names and tokens)
    ws.send(JSON.stringify({
        msgCommand: "subscribe", dataSource: "quote-binary",
        brokerId: 1, tokens: indexNames, underlyingExpiry: [], uniqueId: ""
    }));
    ws.send(JSON.stringify({
        msgCommand: "subscribe", dataSource: "quote-binary",
        brokerId: 1, tokens: indexTokens, underlyingExpiry: [], uniqueId: ""
    }));
    console.log('Sent quote-binary');

    const chainTokens = indexTokens;
    const chainInstrs = indices.map(s => lib.instruments.find(i => i.tradingsymbol === s)).filter(Boolean);

    // Try instrument objects
    ws.send(JSON.stringify({
        msgCommand: "subscribe", dataSource: "option-chain",
        brokerId: 1, tokens: [],
        underlyingExpiry: chainInstrs.map(i => ({ underlying: i, expiry })),
        uniqueId: ""
    }));
    console.log('Sent option-chain (instr objs)');

    // Try token numbers as underlying
    ws.send(JSON.stringify({
        msgCommand: "subscribe", dataSource: "option-chain",
        brokerId: 1, tokens: [],
        underlyingExpiry: chainTokens.map(t => ({ underlying: t, expiry })),
        uniqueId: ""
    }));
    console.log('Sent option-chain (token nums)');

    // Try tokens field
    ws.send(JSON.stringify({
        msgCommand: "subscribe", dataSource: "option-chain",
        brokerId: 1, tokens: chainTokens, underlyingExpiry: [],
        uniqueId: ""
    }));
    console.log('Sent option-chain (tokens field)');
});

ws.on('message', (data) => {
    const raw = new Uint8Array(data);
    if (raw.length <= 2) {
        return; // skip ping/pong
    }
    const firstByte = raw[0];
    try {
        const decoded = lib.decodeData(data);
        const kind = decoded.kind;
        if (kind === 5) { // UNDERLYING_STATS
            const tks = Object.keys(decoded.payload || {});
            tks.forEach(tk => {
                const s = decoded.payload[tk]?.underlying_base_stats || {};
                console.log('STATS|' + tk + '|pcr=' + s.total_pcr?.toFixed(4) + '|foi_chg=' + s.future_oi_change);
            });
        } else if (kind === 3) { // OPTION_CHAIN
            const chain = decoded.payload;
            const tks = Object.keys(chain?.data || {});
            tks.forEach(tk => {
                const exps = Object.keys(chain.data[tk] || {});
                exps.forEach(ex => {
                    const oc = chain.data[tk][ex];
                    const strikes = Object.keys(oc?.chain || {});
                    console.log('CHAIN|' + getSymbolName(parseInt(tk)) + '|' + ex + '|strikes=' + strikes.length + '|atm=' + oc?.atm_strike + '|pcr=' + oc?.pcr?.toFixed(4) + '|maxpain=' + oc?.max_pain_strike + '|atm_iv=' + oc?.atm_iv?.toFixed(2));
                });
            });
        } else if (kind === 1) { // QUOTE
            const p = decoded.payload;
            const sym = getSymbolName(p.instrumentToken);
            console.log('QUOTE|' + sym + '|ltp=' + p.lastPrice + '|chg=' + p.change?.toFixed(2) + '|vol=' + p.volume + '|oi=' + p.oi);
        } else if (firstByte === 253 || firstByte === 254) {
            // ping/pong, skip
        } else {
            console.log('OTHER|kind=' + kind + '|byte=' + firstByte + '|len=' + raw.length);
        }
    } catch (err) {
        console.error('Decode error:', err.message);
    }
});

ws.on('close', (code, reason) => {
    console.log(`Connection closed: code=${code} reason=${reason ? reason.toString() : 'none'}`);
});

ws.on('error', (error) => {
    console.error('WebSocket error:', error.message);
});

ws.on('unexpected-response', (req, res) => {
    console.error('Unexpected response:', res.statusCode, res.statusMessage);
});

console.log('Connecting to Sensibull...');
