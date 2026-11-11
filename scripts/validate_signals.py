#!/usr/bin/env python3
"""Validate research-only NSE JSON candidate lists. No broker functionality."""
import argparse
import json
import re
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
SYMBOL_RE = re.compile(r'^(?=[A-Z0-9&._-]*[A-Z])[A-Z0-9][A-Z0-9&._-]{0,29}$')
TOP_KEYS = {'schema_version', 'status', 'market', 'timezone', 'session_date',
            'news_cutoff_at', 'generated_at', 'execution_enabled', 'signals',
            'reason', 'sample'}
SIGNAL_KEYS = {'symbol', 'side', 'rank', 'reason', 'news_summary',
               'first_available_at', 'sources', 'confidence'}
SOURCE_KEYS = {'url', 'title', 'published_at'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def aware_time(raw, field):
    require(isinstance(raw, str), f'{field} must be an ISO timestamp')
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(f'Invalid timestamp for {field}: {raw}') from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            f'{field} requires timezone information')
    return parsed.astimezone(IST)


def validate(data, *, allow_sample=False, expected_date=None, allowed_symbols=None,
             strict_final=False):
    require(isinstance(data, dict), 'Root must be an object')
    require(not (set(data) - TOP_KEYS), 'Unexpected top-level fields')
    for k in ('schema_version', 'status', 'market', 'timezone', 'session_date',
              'news_cutoff_at', 'generated_at', 'execution_enabled', 'signals'):
        require(k in data, f'Missing required field: {k}')
    require(data['schema_version'] == '1.0', 'Unsupported schema version')
    require(data['market'] == 'NSE', 'Market must be NSE')
    require(data['timezone'] == 'Asia/Kolkata', 'Timezone must be Asia/Kolkata')
    require(data['execution_enabled'] is False, 'Live execution must remain disabled')
    require(data['status'] in ('READY', 'NO_TRADE', 'UNAVAILABLE'), 'Invalid status')
    require(data.get('sample', False) is not True or allow_sample,
            'Example/sample reports cannot be published')
    require(isinstance(data['signals'], list), 'signals must be an array')
    require(len(data['signals']) <= 10, 'Maximum ten Nifty 500 candidates')

    session_date = data['session_date']
    if session_date is None:
        require(data['status'] == 'UNAVAILABLE' and not data['signals'],
                'Null session is valid only for empty unavailable bootstrap')
        require(data['news_cutoff_at'] is None and data['generated_at'] is None,
                'Bootstrap timestamps must be null')
        return data
    require(isinstance(session_date, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', session_date),
            'session_date must be YYYY-MM-DD')
    try:
        day = date.fromisoformat(session_date)
    except ValueError as exc:
        raise ValueError('Invalid session_date') from exc
    if expected_date is not None:
        require(session_date == expected_date, 'Source report date differs from publishing session')
    cutoff = aware_time(data['news_cutoff_at'], 'news_cutoff_at')
    generated = aware_time(data['generated_at'], 'generated_at')
    require(cutoff.date() == day,
            'News cutoff must be on the session date')
    require(generated >= cutoff,
            'Report must be generated on the session date at or after its declared cutoff')
    require(generated <= datetime.now(IST) + timedelta(minutes=5),
            'Report generated_at is in the future')
    require(generated.date() == day,
            'Report must be generated on the session date')
    if strict_final:
        # The daily VPS FINAL producer freezes evidence at 09:00 and must commit
        # before 09:12. Dates/times are already normalised to IST.
        require(dtime(9,0) <= cutoff.timetz().replace(tzinfo=None) < dtime(9,12),
                'Final research cutoff must be the actual morning decision-attempt time before 09:12 IST')
        require(generated.timetz().replace(tzinfo=None) < dtime(9, 12),
                'Final report must be generated strictly before 09:12 IST')
    require(data['status'] == 'READY' or not data['signals'],
            'NO_TRADE and UNAVAILABLE reports may not contain candidates')
    require(data['status'] != 'READY' or len(data['signals']) > 0,
            'READY requires at least one candidate')
    if data['status'] != 'READY':
        require(isinstance(data.get('reason'), str) and len(data['reason']) >= 5,
                'Non-ready reports must explain the reason')

    seen_symbols = set()
    ranks = {'LONG': [], 'SHORT': []}
    for i, signal in enumerate(data['signals']):
        prefix = f'signals[{i}]'
        require(isinstance(signal, dict), f'{prefix} must be an object')
        require(set(signal).issubset(SIGNAL_KEYS), f'{prefix} has unexpected fields')
        for key in ('symbol', 'side', 'rank', 'reason', 'news_summary', 'first_available_at', 'sources'):
            require(key in signal, f'Missing {prefix}.{key}')
        symbol = signal['symbol']
        require(isinstance(symbol, str) and SYMBOL_RE.fullmatch(symbol),
                f'Invalid NSE symbol: {symbol}')
        if allowed_symbols is not None:
            require(symbol in allowed_symbols,
                    f'{symbol} is outside the current official Nifty 500 universe')
        require(symbol not in seen_symbols, f'Duplicate symbol {symbol}')
        seen_symbols.add(symbol)
        side = signal['side']
        require(side in ranks, f'Invalid side for {symbol}')
        require(type(signal['rank']) is int and 1 <= signal['rank'] <= 5,
                f'Rank must be 1–5 for {symbol}')
        ranks[side].append(signal['rank'])
        for key in ('reason', 'news_summary'):
            require(isinstance(signal[key], str) and len(signal[key].strip()) >= 15,
                    f'{symbol}.{key} must explain the event')
        require(aware_time(signal['first_available_at'], f'{symbol}.first_available_at') <= cutoff,
                f'{symbol}: event was not available by the declared cutoff')
        if 'confidence' in signal:
            score = signal['confidence']
            require(score is None or (type(score) in (int, float) and 0 <= score <= 1),
                    f'{symbol}: confidence must be null or 0–1')
        sources = signal['sources']
        require(isinstance(sources, list) and len(sources) > 0,
                f'{symbol} requires a cited source')
        for source in sources:
            require(isinstance(source, dict) and set(source) == SOURCE_KEYS,
                    f'{symbol}: source requires url, title and published_at')
            link = source['url']
            parsed = urlparse(link) if isinstance(link, str) else None
            require(parsed is not None and parsed.scheme == 'https' and bool(parsed.netloc),
                    f'{symbol}: source must be a secure https URL')
            require(allow_sample or parsed.netloc not in ('example.com', 'www.example.com'),
                    f'{symbol}: placeholder sources are forbidden')
            require(isinstance(source['title'], str) and len(source['title'].strip()) >= 3,
                    f'{symbol}: invalid source title')
            require(aware_time(source['published_at'], f'{symbol}.source.published_at') <= cutoff,
                    f'{symbol}: source was published after research cutoff')
    for side, values in ranks.items():
        require(sorted(values) == list(range(1, len(values) + 1)),
                f'{side} ranks must start at 1 and be sequential')
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=Path)
    parser.add_argument('--allow-sample', action='store_true')
    parser.add_argument('--session-date')
    parser.add_argument('--universe', type=Path,
                        help='Official Nifty 500 universe JSON snapshot')
    args = parser.parse_args()
    payload = json.loads(args.path.read_text(encoding='utf-8'))
    universe = None
    if args.universe:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from live_desk.universe import load_nifty500_symbols
        universe = load_nifty500_symbols(args.universe)
    validate(payload, allow_sample=args.allow_sample, expected_date=args.session_date,
             allowed_symbols=universe)
    print(f"VALID: {payload['status']} {payload['session_date']} ({len(payload['signals'])} candidates)")


if __name__ == '__main__':
    main()
