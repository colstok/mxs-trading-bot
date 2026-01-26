"""
MXS Volatile Coin Strategy - 30M/4H Webhook Trading Bot
- 30M (LTF) for entries (breaks + continuations)
- 4H (HTF) for trend and trailing stops
- 3x leverage, 0.5% stop buffer, 1% max stop cap
- Deviation required for breaks, not for continuations
"""

import os
import json
import hmac
import hashlib
import base64
import time
import requests
import uuid
from flask import Flask, request, jsonify
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# =============================================================================
# CONFIGURATION - MXS Volatile Strategy
# =============================================================================
API_KEY = os.environ.get('BLOFIN_API_KEY', '')
API_SECRET = os.environ.get('BLOFIN_API_SECRET', '')
PASSPHRASE = os.environ.get('BLOFIN_PASSPHRASE', '')
BASE_URL = "https://openapi.blofin.com"

SYMBOL = "FARTCOIN-USDT"
LEVERAGE = 3              # 3x leverage (confirmed in backtest)
STOP_BUFFER = 0.005       # 0.5% buffer on HTF swings
MAX_STOP_PCT = 0.01       # 1% max stop cap (critical for volatile coins)
MARGIN_MODE = "isolated"

# =============================================================================
# STATE PERSISTENCE
# =============================================================================
STATE_FILE = 'bot_state.json'

def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            print(f"[STARTUP] Loaded state from file: {state}")
            return state
    except Exception as e:
        print(f"[STARTUP] No saved state ({e}), starting fresh")
        return {
            'htf_trend': None,
            'ltf_trend': None,
            'had_deviation': False,
            'position': None,
            'entry': None,
            'stop': None,
            'htf_swing_low': None,
            'htf_swing_high': None,
            'signal_log': []
        }

def save_state():
    state = {
        'htf_trend': htf_trend,
        'ltf_trend': ltf_trend,
        'had_deviation': had_deviation,
        'position': current_position,
        'entry': entry_price,
        'stop': stop_price,
        'htf_swing_low': htf_swing_low,
        'htf_swing_high': htf_swing_high,
        'signal_log': signal_log[-50:]
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
        print(f"[STATE SAVED] htf={htf_trend}, ltf={ltf_trend}, dev={had_deviation}, pos={current_position}")
    except Exception as e:
        print(f"[STATE SAVE ERROR] {e}")

def log_signal(msg):
    global signal_log
    entry = {'time': datetime.now().isoformat(), 'msg': msg}
    signal_log.append(entry)
    if len(signal_log) > 50:
        signal_log = signal_log[-50:]
    print(f"[LOG] {msg}")

# Load state at startup
_s = load_state()
htf_trend = _s.get('htf_trend')
ltf_trend = _s.get('ltf_trend')
had_deviation = _s.get('had_deviation', False)
current_position = _s.get('position')
entry_price = _s.get('entry')
stop_price = _s.get('stop')
htf_swing_low = _s.get('htf_swing_low')
htf_swing_high = _s.get('htf_swing_high')
signal_log = _s.get('signal_log', [])

print(f"[INIT] HTF: {htf_trend}, LTF: {ltf_trend}, Deviation: {had_deviation}, Position: {current_position}")

# =============================================================================
# BLOFIN API
# =============================================================================
def sign_request(path, method, ts, nonce, body=''):
    msg = path + method.upper() + ts + nonce + body
    mac = hmac.new(bytes(API_SECRET, 'utf-8'), bytes(msg, 'utf-8'), hashlib.sha256)
    return base64.b64encode(bytes(mac.hexdigest(), 'utf-8')).decode()

def api_request(method, endpoint, data=None):
    ts = str(int(time.time() * 1000))
    nonce = str(uuid.uuid4())
    body = json.dumps(data, separators=(',', ':')) if data else ''
    sig = sign_request(endpoint, method, ts, nonce, body)
    headers = {
        'ACCESS-KEY': API_KEY, 'ACCESS-SIGN': sig, 'ACCESS-TIMESTAMP': ts,
        'ACCESS-PASSPHRASE': PASSPHRASE, 'ACCESS-NONCE': nonce, 'Content-Type': 'application/json'
    }
    try:
        if method == 'GET':
            return requests.get(BASE_URL + endpoint, headers=headers, timeout=10).json()
        return requests.post(BASE_URL + endpoint, headers=headers, data=body, timeout=10).json()
    except Exception as e:
        print(f"[API ERROR] {e}")
        return {'code': '-1', 'msg': str(e)}

def get_usdt_balance():
    r = api_request('GET', '/api/v1/asset/balances?accountType=futures')
    if r.get('code') == '0':
        for a in r.get('data', []):
            if a.get('currency') == 'USDT':
                return float(a.get('available', 0))
    return 0

def get_blofin_position():
    r = api_request('GET', '/api/v1/account/positions')
    if r.get('code') == '0':
        for pos in r.get('data', []):
            if pos.get('instId') == SYMBOL:
                positions = float(pos.get('positions', 0))
                if positions > 0:
                    return {'side': 'LONG', 'size': positions, 'entry': float(pos.get('averagePrice', 0))}
                elif positions < 0:
                    return {'side': 'SHORT', 'size': abs(positions), 'entry': float(pos.get('averagePrice', 0))}
    return {'side': None, 'size': 0, 'entry': 0}

def get_price(symbol):
    try:
        r = requests.get("https://openapi.blofin.com/api/v1/market/tickers", timeout=10).json()
        for t in r.get('data', []):
            if t.get('instId') == symbol:
                return float(t['last'])
    except:
        pass
    return None

def close_position():
    print(f"[CLOSE] Closing position...")
    result = api_request('POST', '/api/v1/trade/close-position',
        {'instId': SYMBOL, 'marginMode': MARGIN_MODE, 'positionSide': 'net'})
    print(f"[CLOSE] Result: {result}")
    return result

def place_order(side, size, sl=None):
    data = {'instId': SYMBOL, 'marginMode': MARGIN_MODE, 'positionSide': 'net',
            'side': side, 'orderType': 'market', 'size': str(size)}
    if sl:
        data['slTriggerPrice'] = str(sl)
        data['slOrderPrice'] = '-1'
    return api_request('POST', '/api/v1/trade/order', data)

def update_stop_loss(new_stop):
    """Update stop loss on existing position"""
    # Cancel existing SL and place new one
    # This is a simplified version - may need adjustment based on Blofin API
    print(f"[TRAIL] Updating stop to {new_stop}")
    # For now, we'll track it in state and rely on the next entry to set it
    # A full implementation would use Blofin's modify order or algo order endpoints
    return {'status': 'stop_updated', 'new_stop': new_stop}

# =============================================================================
# STOP CALCULATION WITH 1% CAP
# =============================================================================
def calculate_stop(entry_px, swing_px, direction):
    """
    Calculate stop price with buffer and 1% max cap.
    - direction: 'LONG' or 'SHORT'
    - Returns capped stop price
    """
    if direction == 'LONG':
        # Stop below swing low
        raw_stop = swing_px * (1 - STOP_BUFFER)
        stop_distance_pct = (entry_px - raw_stop) / entry_px

        # Cap at 1% max
        if stop_distance_pct > MAX_STOP_PCT:
            capped_stop = entry_px * (1 - MAX_STOP_PCT)
            print(f"[STOP CAP] Raw stop {raw_stop:.4f} ({stop_distance_pct*100:.1f}%) -> Capped to {capped_stop:.4f} ({MAX_STOP_PCT*100}%)")
            return capped_stop
        return raw_stop
    else:
        # Stop above swing high
        raw_stop = swing_px * (1 + STOP_BUFFER)
        stop_distance_pct = (raw_stop - entry_px) / entry_px

        # Cap at 1% max
        if stop_distance_pct > MAX_STOP_PCT:
            capped_stop = entry_px * (1 + MAX_STOP_PCT)
            print(f"[STOP CAP] Raw stop {raw_stop:.4f} ({stop_distance_pct*100:.1f}%) -> Capped to {capped_stop:.4f} ({MAX_STOP_PCT*100}%)")
            return capped_stop
        return raw_stop

# =============================================================================
# TRADING
# =============================================================================
def enter_long(price, swing_low):
    global current_position, entry_price, stop_price, had_deviation

    stop = calculate_stop(price, swing_low, 'LONG')

    print(f"\n{'='*50}")
    print(f"ENTERING LONG @ ${price:.6f}")
    print(f"Swing Low: ${swing_low:.6f}")
    print(f"Stop: ${stop:.6f} ({((price-stop)/price)*100:.2f}% risk)")
    print(f"{'='*50}")

    blofin_pos = get_blofin_position()
    if blofin_pos['side'] == 'SHORT':
        print("[CLOSE SHORT FIRST]")
        close_position()
        time.sleep(0.5)
    elif blofin_pos['side'] == 'LONG':
        print("[ALREADY LONG]")
        return {'status': 'already_long'}

    bal = get_usdt_balance()
    if bal <= 0:
        return {'error': 'no balance'}

    position_value = bal * 0.95 * LEVERAGE
    size = int(position_value / price)
    if size <= 0:
        return {'error': 'size too small'}

    api_request('POST', '/api/v1/account/set-leverage',
                {'instId': SYMBOL, 'leverage': str(LEVERAGE), 'marginMode': MARGIN_MODE})
    result = place_order('buy', size, stop)

    if result.get('code') == '0':
        current_position = 'LONG'
        entry_price = price
        stop_price = stop
        had_deviation = False  # Reset after entry
        log_signal(f"LONG ENTERED: size={size}, entry={price:.6f}, stop={stop:.6f}")
        save_state()

    return result

def enter_short(price, swing_high):
    global current_position, entry_price, stop_price, had_deviation

    stop = calculate_stop(price, swing_high, 'SHORT')

    print(f"\n{'='*50}")
    print(f"ENTERING SHORT @ ${price:.6f}")
    print(f"Swing High: ${swing_high:.6f}")
    print(f"Stop: ${stop:.6f} ({((stop-price)/price)*100:.2f}% risk)")
    print(f"{'='*50}")

    blofin_pos = get_blofin_position()
    if blofin_pos['side'] == 'LONG':
        print("[CLOSE LONG FIRST]")
        close_position()
        time.sleep(0.5)
    elif blofin_pos['side'] == 'SHORT':
        print("[ALREADY SHORT]")
        return {'status': 'already_short'}

    bal = get_usdt_balance()
    if bal <= 0:
        return {'error': 'no balance'}

    position_value = bal * 0.95 * LEVERAGE
    size = int(position_value / price)
    if size <= 0:
        return {'error': 'size too small'}

    api_request('POST', '/api/v1/account/set-leverage',
                {'instId': SYMBOL, 'leverage': str(LEVERAGE), 'marginMode': MARGIN_MODE})
    result = place_order('sell', size, stop)

    if result.get('code') == '0':
        current_position = 'SHORT'
        entry_price = price
        stop_price = stop
        had_deviation = False  # Reset after entry
        log_signal(f"SHORT ENTERED: size={size}, entry={price:.6f}, stop={stop:.6f}")
        save_state()

    return result

def exit_position(price, reason):
    global current_position, entry_price, stop_price
    print(f"\n=== EXITING {current_position} @ ${price:.6f} ({reason}) ===")

    close_position()
    log_signal(f"EXIT {current_position}: price={price:.6f}, reason={reason}")

    current_position = None
    entry_price = None
    stop_price = None
    save_state()

# =============================================================================
# WEBHOOK - 30M/4H Strategy
# =============================================================================
@app.route('/webhook', methods=['POST'])
def webhook():
    global htf_trend, ltf_trend, had_deviation, current_position, entry_price, stop_price
    global htf_swing_low, htf_swing_high

    raw_data = request.get_data(as_text=True)
    print(f"\n{'='*60}")
    print(f"[WEBHOOK] Raw: {raw_data[:500]}")

    # Fix TradingView double-brace issue
    if raw_data.startswith('{{'):
        raw_data = raw_data[1:]

    try:
        data = json.loads(raw_data)
    except Exception as e:
        log_signal(f"JSON PARSE ERROR: {e}")
        return jsonify({'error': 'Invalid JSON'}), 400

    signal = str(data.get('signal', '')).upper().strip()
    price = float(data.get('price', 0)) or get_price(SYMBOL)
    swing_low = float(data.get('swing_low')) if data.get('swing_low') else None
    swing_high = float(data.get('swing_high')) if data.get('swing_high') else None

    blofin_pos = get_blofin_position()

    print(f"[SIGNAL] {signal} @ ${price:.6f}")
    print(f"[STATE] htf={htf_trend}, ltf={ltf_trend}, deviation={had_deviation}, pos={blofin_pos['side']}")
    print(f"[SWINGS] low={swing_low}, high={swing_high}")
    print(f"[HTF SWINGS] low={htf_swing_low}, high={htf_swing_high}")

    log_signal(f"RECV: {signal} | price={price:.6f} | htf={htf_trend} | ltf={ltf_trend} | dev={had_deviation}")

    # =========================================================================
    # 4H_UPDATE - Just update swings, no trend change (from Reclaim, Zone Cross, etc.)
    # =========================================================================
    if '4H' in signal and 'UPDATE' in signal:
        if swing_low:
            htf_swing_low = swing_low
        if swing_high:
            htf_swing_high = swing_high

        log_signal(f"4H UPDATE: swings updated - low={htf_swing_low}, high={htf_swing_high}")

        # Trail stop for existing LONG (move stop up to new swing low)
        if blofin_pos['side'] == 'LONG' and htf_swing_low and entry_price:
            new_stop = calculate_stop(entry_price, htf_swing_low, 'LONG')
            if stop_price and new_stop > stop_price:
                stop_price = new_stop
                log_signal(f"TRAIL LONG: stop raised to {new_stop:.6f}")

        # Trail stop for existing SHORT (move stop down to new swing high)
        elif blofin_pos['side'] == 'SHORT' and htf_swing_high and entry_price:
            new_stop = calculate_stop(entry_price, htf_swing_high, 'SHORT')
            if stop_price and new_stop < stop_price:
                stop_price = new_stop
                log_signal(f"TRAIL SHORT: stop lowered to {new_stop:.6f}")

        save_state()
        return jsonify({'action': 'SWINGS_UPDATED', 'htf_swing_low': htf_swing_low, 'htf_swing_high': htf_swing_high})

    # =========================================================================
    # 4H (HTF) SIGNALS - Set trend, store swings, trail stops, exit on flip
    # Only flip trend on STRUCTURE BREAKS (signal must contain BREAK)
    # =========================================================================
    elif '4H' in signal and 'BULL' in signal and 'BREAK' in signal:
        old_trend = htf_trend
        htf_trend = 'BULL'
        had_deviation = False  # Reset deviation on HTF change

        if swing_low:
            htf_swing_low = swing_low
        if swing_high:
            htf_swing_high = swing_high

        log_signal(f"4H BULL: htf {old_trend} -> BULL, deviation reset, swings: low={htf_swing_low}, high={htf_swing_high}")

        # Exit SHORT on HTF flip to BULL
        if blofin_pos['side'] == 'SHORT':
            exit_position(price, '4H_BULL_FLIP')

        # Trail stop for existing LONG (move stop up to new swing low)
        elif blofin_pos['side'] == 'LONG' and htf_swing_low and entry_price:
            new_stop = calculate_stop(entry_price, htf_swing_low, 'LONG')
            if stop_price and new_stop > stop_price:
                stop_price = new_stop
                log_signal(f"TRAIL LONG: stop raised to {new_stop:.6f}")
                # Note: Would need to update actual exchange stop here

        save_state()
        return jsonify({'action': 'HTF_BULL', 'htf_swing_low': htf_swing_low, 'htf_swing_high': htf_swing_high})

    elif '4H' in signal and 'BEAR' in signal and 'BREAK' in signal:
        old_trend = htf_trend
        htf_trend = 'BEAR'
        had_deviation = False  # Reset deviation on HTF change

        if swing_low:
            htf_swing_low = swing_low
        if swing_high:
            htf_swing_high = swing_high

        log_signal(f"4H BEAR: htf {old_trend} -> BEAR, deviation reset, swings: low={htf_swing_low}, high={htf_swing_high}")

        # Exit LONG on HTF flip to BEAR
        if blofin_pos['side'] == 'LONG':
            exit_position(price, '4H_BEAR_FLIP')

        # Trail stop for existing SHORT (move stop down to new swing high)
        elif blofin_pos['side'] == 'SHORT' and htf_swing_high and entry_price:
            new_stop = calculate_stop(entry_price, htf_swing_high, 'SHORT')
            if stop_price and new_stop < stop_price:
                stop_price = new_stop
                log_signal(f"TRAIL SHORT: stop lowered to {new_stop:.6f}")
                # Note: Would need to update actual exchange stop here

        save_state()
        return jsonify({'action': 'HTF_BEAR', 'htf_swing_low': htf_swing_low, 'htf_swing_high': htf_swing_high})

    # =========================================================================
    # 4H OTHER SIGNALS (Imbalance, Reclaim, Zone Cross, etc.) - Update swings only, NO trend flip
    # =========================================================================
    elif '4H' in signal and 'BREAK' not in signal and 'UPDATE' not in signal:
        # This catches: Imbalance, Reclaim, Zone Cross, etc.
        if swing_low:
            htf_swing_low = swing_low
        if swing_high:
            htf_swing_high = swing_high

        log_signal(f"4H OTHER ({signal}): swings updated - low={htf_swing_low}, high={htf_swing_high} (NO TREND CHANGE)")

        # Trail stop for existing positions
        if blofin_pos['side'] == 'LONG' and htf_swing_low and entry_price:
            new_stop = calculate_stop(entry_price, htf_swing_low, 'LONG')
            if stop_price and new_stop > stop_price:
                stop_price = new_stop
                log_signal(f"TRAIL LONG: stop raised to {new_stop:.6f}")

        elif blofin_pos['side'] == 'SHORT' and htf_swing_high and entry_price:
            new_stop = calculate_stop(entry_price, htf_swing_high, 'SHORT')
            if stop_price and new_stop < stop_price:
                stop_price = new_stop
                log_signal(f"TRAIL SHORT: stop lowered to {new_stop:.6f}")

        save_state()
        return jsonify({'action': 'HTF_SWING_UPDATE', 'signal': signal, 'htf_swing_low': htf_swing_low, 'htf_swing_high': htf_swing_high})

    # =========================================================================
    # 30M (LTF) BREAK SIGNALS - Require deviation
    # =========================================================================
    elif '30M' in signal and 'BULL' in signal and 'CONT' not in signal:
        old_ltf = ltf_trend
        ltf_trend = 'BULL'

        # Check for deviation: LTF was BEAR while HTF was BULL
        if old_ltf == 'BEAR' and htf_trend == 'BULL':
            had_deviation = True
            log_signal(f"DEVIATION DETECTED: LTF was BEAR, now BULL, HTF is BULL")

        print(f"30M BULL BREAK - htf={htf_trend}, deviation={had_deviation}, blofin={blofin_pos['side']}")

        # Entry conditions: HTF is BULL + had deviation + not already long
        if htf_trend != 'BULL':
            log_signal(f"NO ENTRY: HTF is {htf_trend}, need BULL")
            save_state()
            return jsonify({'action': 'NO_ENTRY', 'reason': f'htf is {htf_trend}'})

        if not had_deviation:
            log_signal(f"NO ENTRY: No deviation yet")
            save_state()
            return jsonify({'action': 'NO_ENTRY', 'reason': 'no deviation'})

        if blofin_pos['side'] == 'LONG':
            save_state()
            return jsonify({'action': 'NO_ENTRY', 'reason': 'already LONG'})

        if not htf_swing_low:
            save_state()
            return jsonify({'action': 'NO_ENTRY', 'reason': 'no HTF swing_low for stop'})

        result = enter_long(price, htf_swing_low)
        return jsonify({'action': 'LONG_ENTERED', 'type': 'BREAK', 'result': str(result)})

    elif '30M' in signal and 'BEAR' in signal and 'CONT' not in signal:
        old_ltf = ltf_trend
        ltf_trend = 'BEAR'

        # Check for deviation: LTF was BULL while HTF was BEAR
        if old_ltf == 'BULL' and htf_trend == 'BEAR':
            had_deviation = True
            log_signal(f"DEVIATION DETECTED: LTF was BULL, now BEAR, HTF is BEAR")

        print(f"30M BEAR BREAK - htf={htf_trend}, deviation={had_deviation}, blofin={blofin_pos['side']}")

        # Entry conditions: HTF is BEAR + had deviation + not already short
        if htf_trend != 'BEAR':
            log_signal(f"NO ENTRY: HTF is {htf_trend}, need BEAR")
            save_state()
            return jsonify({'action': 'NO_ENTRY', 'reason': f'htf is {htf_trend}'})

        if not had_deviation:
            log_signal(f"NO ENTRY: No deviation yet")
            save_state()
            return jsonify({'action': 'NO_ENTRY', 'reason': 'no deviation'})

        if blofin_pos['side'] == 'SHORT':
            save_state()
            return jsonify({'action': 'NO_ENTRY', 'reason': 'already SHORT'})

        if not htf_swing_high:
            save_state()
            return jsonify({'action': 'NO_ENTRY', 'reason': 'no HTF swing_high for stop'})

        result = enter_short(price, htf_swing_high)
        return jsonify({'action': 'SHORT_ENTERED', 'type': 'BREAK', 'result': str(result)})

    # =========================================================================
    # 30M (LTF) CONTINUATION SIGNALS - No deviation needed
    # =========================================================================
    elif '30M' in signal and 'BULL' in signal and 'CONT' in signal:
        print(f"30M BULL CONT - htf={htf_trend}, blofin={blofin_pos['side']}")

        if htf_trend != 'BULL':
            log_signal(f"NO ENTRY: HTF is {htf_trend}, need BULL for continuation")
            return jsonify({'action': 'NO_ENTRY', 'reason': f'htf is {htf_trend}'})

        if blofin_pos['side'] == 'LONG':
            return jsonify({'action': 'NO_ENTRY', 'reason': 'already LONG'})

        if not htf_swing_low:
            return jsonify({'action': 'NO_ENTRY', 'reason': 'no HTF swing_low for stop'})

        result = enter_long(price, htf_swing_low)
        return jsonify({'action': 'LONG_ENTERED', 'type': 'CONTINUATION', 'result': str(result)})

    elif '30M' in signal and 'BEAR' in signal and 'CONT' in signal:
        print(f"30M BEAR CONT - htf={htf_trend}, blofin={blofin_pos['side']}")

        if htf_trend != 'BEAR':
            log_signal(f"NO ENTRY: HTF is {htf_trend}, need BEAR for continuation")
            return jsonify({'action': 'NO_ENTRY', 'reason': f'htf is {htf_trend}'})

        if blofin_pos['side'] == 'SHORT':
            return jsonify({'action': 'NO_ENTRY', 'reason': 'already SHORT'})

        if not htf_swing_high:
            return jsonify({'action': 'NO_ENTRY', 'reason': 'no HTF swing_high for stop'})

        result = enter_short(price, htf_swing_high)
        return jsonify({'action': 'SHORT_ENTERED', 'type': 'CONTINUATION', 'result': str(result)})

    else:
        log_signal(f"UNKNOWN SIGNAL: {signal}")
        return jsonify({'error': f'Unknown signal: {signal}'}), 400

# =============================================================================
# ENDPOINTS
# =============================================================================
@app.route('/status', methods=['GET'])
def status():
    blofin_pos = get_blofin_position()
    return jsonify({
        'htf_trend': htf_trend,
        'ltf_trend': ltf_trend,
        'had_deviation': had_deviation,
        'position': current_position,
        'blofin_position': blofin_pos['side'],
        'blofin_size': blofin_pos['size'],
        'entry_price': entry_price,
        'stop_price': stop_price,
        'htf_swing_low': htf_swing_low,
        'htf_swing_high': htf_swing_high,
        'config': {
            'symbol': SYMBOL,
            'leverage': LEVERAGE,
            'stop_buffer': STOP_BUFFER,
            'max_stop_pct': MAX_STOP_PCT
        },
        'recent_logs': signal_log[-10:]
    })

@app.route('/set_trend', methods=['POST'])
def set_trend_endpoint():
    global htf_trend, ltf_trend, had_deviation, htf_swing_low, htf_swing_high
    data = request.get_json(force=True)

    if data.get('htf_trend'):
        htf_trend = str(data['htf_trend']).upper()
    if data.get('ltf_trend'):
        ltf_trend = str(data['ltf_trend']).upper()
    if data.get('had_deviation') is not None:
        had_deviation = bool(data['had_deviation'])
    if data.get('swing_low'):
        htf_swing_low = float(data['swing_low'])
    if data.get('swing_high'):
        htf_swing_high = float(data['swing_high'])

    log_signal(f"MANUAL SET: htf={htf_trend}, ltf={ltf_trend}, dev={had_deviation}")
    save_state()
    return jsonify({'status': 'ok', 'htf_trend': htf_trend, 'ltf_trend': ltf_trend, 'had_deviation': had_deviation})

@app.route('/close', methods=['POST'])
def close_endpoint():
    exit_position(get_price(SYMBOL) or 0, 'MANUAL')
    return jsonify({'status': 'closed'})

@app.route('/logs', methods=['GET'])
def logs_endpoint():
    return jsonify({'logs': signal_log})

@app.route('/reset', methods=['POST'])
def reset_endpoint():
    global htf_trend, ltf_trend, had_deviation, current_position, entry_price, stop_price
    global htf_swing_low, htf_swing_high, signal_log

    htf_trend = None
    ltf_trend = None
    had_deviation = False
    current_position = None
    entry_price = None
    stop_price = None
    htf_swing_low = None
    htf_swing_high = None
    signal_log = []
    save_state()
    return jsonify({'status': 'reset'})

@app.route('/', methods=['GET'])
def home():
    blofin_pos = get_blofin_position()
    logs_html = '<br>'.join([f"{l['time']}: {l['msg']}" for l in signal_log[-20:]])
    htf_color = 'green' if htf_trend == 'BULL' else 'red' if htf_trend == 'BEAR' else 'gray'
    ltf_color = 'green' if ltf_trend == 'BULL' else 'red' if ltf_trend == 'BEAR' else 'gray'
    dev_color = 'yellow' if had_deviation else 'gray'

    return f'''<html><head>
    <title>MXS Bot - 30M/4H</title>
    <meta http-equiv="refresh" content="5">
    <style>body{{background:#111;color:#eee;font-family:monospace;padding:20px;}}
    .tag{{padding:2px 8px;border-radius:3px;margin:2px;}}</style>
    </head><body>
    <h1>MXS Volatile Strategy - 30M/4H</h1>
    <p><b>Config:</b> {SYMBOL} | {LEVERAGE}x leverage | {STOP_BUFFER*100}% buffer | {MAX_STOP_PCT*100}% max stop</p>
    <h2>
        HTF: <span style="color:{htf_color}">{htf_trend or 'NONE'}</span> |
        LTF: <span style="color:{ltf_color}">{ltf_trend or 'NONE'}</span> |
        Deviation: <span style="color:{dev_color}">{had_deviation}</span>
    </h2>
    <p>HTF Swing Low: {htf_swing_low}</p>
    <p>HTF Swing High: {htf_swing_high}</p>
    <h3>Position: {blofin_pos['side'] or 'FLAT'} ({blofin_pos['size']} @ ${blofin_pos['entry']:.6f})</h3>
    <p>Entry: {entry_price} | Stop: {stop_price}</p>
    <h3>Recent Logs</h3>
    <pre style="background:#222;padding:10px;color:#0f0;max-height:400px;overflow:auto;">{logs_html or 'No logs yet'}</pre>
    <p><a href="/status">Status JSON</a> | <a href="/logs">All Logs</a></p>
    </body></html>'''

if __name__ == '__main__':
    print(f"\n{'='*60}")
    print(f"MXS VOLATILE STRATEGY BOT - 30M/4H")
    print(f"{'='*60}")
    print(f"Symbol: {SYMBOL}")
    print(f"Leverage: {LEVERAGE}x")
    print(f"Stop Buffer: {STOP_BUFFER*100}%")
    print(f"Max Stop Cap: {MAX_STOP_PCT*100}%")
    print(f"{'='*60}")
    print(f"HTF Trend: {htf_trend}")
    print(f"LTF Trend: {ltf_trend}")
    print(f"Deviation: {had_deviation}")
    print(f"Position: {current_position}")
    print(f"{'='*60}\n")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
