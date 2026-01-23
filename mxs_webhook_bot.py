"""
MXS Multi-Timeframe Webhook Trading Bot
Higher TF trend + Lower TF entries
- Lower TF (5M) for entries only
- Higher TF (30M) for exits and trend
- FULL ACCOUNT position sizing
- 2% stop buffer from higher TF swing levels
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
# CONFIGURATION
# =============================================================================
API_KEY = os.environ.get('BLOFIN_API_KEY', 'your-api-key')
API_SECRET = os.environ.get('BLOFIN_API_SECRET', 'your-api-secret')
PASSPHRASE = os.environ.get('BLOFIN_PASSPHRASE', 'your-passphrase')
BASE_URL = "https://openapi.blofin.com"

SYMBOL = "FARTCOIN-USDT"
LEVERAGE = 5
STOP_BUFFER = 0.02     # 2% buffer beyond swing level
MARGIN_MODE = "isolated"
POSITION_MODE = "full_account"  # Full account sizing

# =============================================================================
# STATE PERSISTENCE
# =============================================================================
STATE_FILE = 'bot_state.json'

def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            print(f"[STARTUP] Loaded state: {state}")
            return state
    except:
        print("[STARTUP] No saved state, starting fresh")
        return {
            'trend': None,
            'position': None,
            'entry': None,
            'stop': None,
            'htf_swing_low': None,   # Higher timeframe swing low
            'htf_swing_high': None   # Higher timeframe swing high
        }

def save_state(trend, position, entry, stop, htf_swing_low, htf_swing_high):
    state = {
        'trend': trend,
        'position': position,
        'entry': entry,
        'stop': stop,
        'htf_swing_low': htf_swing_low,
        'htf_swing_high': htf_swing_high
    }
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)
    print(f"[STATE SAVED] {state}")

# Load on startup
_s = load_state()
trend_state = _s.get('trend')
current_position = _s.get('position')
entry_price = _s.get('entry')
stop_price = _s.get('stop')
htf_swing_low = _s.get('htf_swing_low')
htf_swing_high = _s.get('htf_swing_high')

# =============================================================================
# BLOFIN API
# =============================================================================
def get_timestamp():
    return str(int(time.time() * 1000))

def get_nonce():
    return str(uuid.uuid4())

def sign_request(path, method, ts, nonce, body=''):
    msg = path + method.upper() + ts + nonce + body
    mac = hmac.new(bytes(API_SECRET, 'utf-8'), bytes(msg, 'utf-8'), hashlib.sha256)
    return base64.b64encode(bytes(mac.hexdigest(), 'utf-8')).decode()

def api_request(method, endpoint, data=None):
    ts = get_timestamp()
    nonce = get_nonce()
    body = json.dumps(data, separators=(',', ':')) if data else ''
    sig = sign_request(endpoint, method, ts, nonce, body)
    headers = {
        'ACCESS-KEY': API_KEY, 'ACCESS-SIGN': sig, 'ACCESS-TIMESTAMP': ts,
        'ACCESS-PASSPHRASE': PASSPHRASE, 'ACCESS-NONCE': nonce, 'Content-Type': 'application/json'
    }
    try:
        if method == 'GET':
            return requests.get(BASE_URL + endpoint, headers=headers).json()
        return requests.post(BASE_URL + endpoint, headers=headers, data=body).json()
    except Exception as e:
        return {'code': '-1', 'msg': str(e)}

def get_usdt_balance():
    r = api_request('GET', '/api/v1/asset/balances?accountType=futures')
    if r.get('code') == '0':
        for a in r.get('data', []):
            if a.get('currency') == 'USDT':
                return float(a.get('available', 0))
    return 0

def get_positions():
    return api_request('GET', '/api/v1/account/positions')

def set_leverage(symbol, lev):
    return api_request('POST', '/api/v1/account/set-leverage',
                       {'instId': symbol, 'leverage': str(lev), 'marginMode': MARGIN_MODE})

def place_order(symbol, side, size, sl=None):
    data = {'instId': symbol, 'marginMode': MARGIN_MODE, 'positionSide': 'net',
            'side': side, 'orderType': 'market', 'size': str(size)}
    if sl:
        data['slTriggerPrice'] = str(sl)
        data['slOrderPrice'] = '-1'
    return api_request('POST', '/api/v1/trade/order', data)

def close_position(symbol, side):
    return api_request('POST', '/api/v1/trade/close-position',
                       {'instId': symbol, 'marginMode': MARGIN_MODE, 'positionSide': 'net',
                        'side': 'sell' if side == 'buy' else 'buy', 'orderType': 'market',
                        'size': '0', 'reduceOnly': 'true'})

def get_price(symbol):
    try:
        r = requests.get("https://openapi.blofin.com/api/v1/market/tickers", timeout=10).json()
        for t in r.get('data', []):
            if t.get('instId') == symbol:
                return float(t['last'])
    except:
        pass
    return None

# =============================================================================
# POSITION SIZING - FULL ACCOUNT
# =============================================================================
def calc_position_size(balance, entry, stop):
    # Full account position sizing
    position_value = balance * LEVERAGE
    position_size = position_value / entry

    stop_distance = abs(entry - stop)
    stop_pct = (stop_distance / entry) * 100

    print(f"[SIZING] Balance: ${balance:.2f}")
    print(f"[SIZING] Leverage: {LEVERAGE}x")
    print(f"[SIZING] Position Value: ${position_value:.2f}")
    print(f"[SIZING] Entry: ${entry:.4f}, Stop: ${stop:.4f}")
    print(f"[SIZING] Stop Distance: {stop_pct:.2f}%")
    print(f"[SIZING] Position Size: {int(position_size)} contracts")

    return int(position_size)

# =============================================================================
# TRADING
# =============================================================================
def enter_long(price, swing_low):
    global current_position, entry_price, stop_price

    # Stop 2% below swing low (using higher TF swing)
    stop = swing_low * (1 - STOP_BUFFER)

    print(f"\n{'='*50}")
    print(f"ENTERING LONG @ ${price:.4f}")
    print(f"HTF Swing Low: ${swing_low:.4f}")
    print(f"Stop (2% below): ${stop:.4f}")
    print(f"{'='*50}")

    if current_position == 'SHORT':
        close_position(SYMBOL, 'sell')

    bal = get_usdt_balance()
    if bal <= 0:
        return {'error': 'no balance'}

    size = calc_position_size(bal, price, stop)

    if size <= 0:
        return {'error': 'position size too small'}

    set_leverage(SYMBOL, LEVERAGE)
    result = place_order(SYMBOL, 'buy', size, stop)

    if result.get('code') == '0':
        current_position = 'LONG'
        entry_price = price
        stop_price = stop
        save_state(trend_state, current_position, entry_price, stop_price, htf_swing_low, htf_swing_high)
    return result

def enter_short(price, swing_high):
    global current_position, entry_price, stop_price

    # Stop 2% above swing high (using higher TF swing)
    stop = swing_high * (1 + STOP_BUFFER)

    print(f"\n{'='*50}")
    print(f"ENTERING SHORT @ ${price:.4f}")
    print(f"HTF Swing High: ${swing_high:.4f}")
    print(f"Stop (2% above): ${stop:.4f}")
    print(f"{'='*50}")

    if current_position == 'LONG':
        close_position(SYMBOL, 'buy')

    bal = get_usdt_balance()
    if bal <= 0:
        return {'error': 'no balance'}

    size = calc_position_size(bal, price, stop)

    if size <= 0:
        return {'error': 'position size too small'}

    set_leverage(SYMBOL, LEVERAGE)
    result = place_order(SYMBOL, 'sell', size, stop)

    if result.get('code') == '0':
        current_position = 'SHORT'
        entry_price = price
        stop_price = stop
        save_state(trend_state, current_position, entry_price, stop_price, htf_swing_low, htf_swing_high)
    return result

def exit_position(price):
    global current_position, entry_price, stop_price
    print(f"\n=== EXITING {current_position} @ ${price:.4f} ===")

    if current_position == 'LONG':
        close_position(SYMBOL, 'buy')
    elif current_position == 'SHORT':
        close_position(SYMBOL, 'sell')
    else:
        return {'status': 'no position'}

    current_position = None
    entry_price = None
    stop_price = None
    save_state(trend_state, current_position, entry_price, stop_price, htf_swing_low, htf_swing_high)
    return {'status': 'closed'}

# =============================================================================
# WEBHOOK
# =============================================================================
@app.route('/webhook', methods=['POST'])
def webhook():
    global trend_state, current_position, entry_price, stop_price, htf_swing_low, htf_swing_high

    try:
        data = request.json

        # Save raw webhook for debugging
        with open('last_webhook.json', 'w') as f:
            json.dump({'received_at': datetime.now().isoformat(), 'data': data}, f, indent=2)

        signal = data.get('signal', '').upper()
        price = float(data.get('price', 0)) or get_price(SYMBOL)
        swing_low = float(data.get('swing_low', 0)) if data.get('swing_low') else None
        swing_high = float(data.get('swing_high', 0)) if data.get('swing_high') else None

        print(f"\n{'='*60}")
        print(f"SIGNAL: {signal} @ ${price:.4f}")
        print(f"Swing Low: {swing_low}, Swing High: {swing_high}")
        print(f"HTF Swings: Low={htf_swing_low}, High={htf_swing_high}")
        print(f"STATE: trend={trend_state}, pos={current_position}")
        print(f"{'='*60}")

        # =====================================================================
        # 30M (HIGHER TF) - Set trend, store swings, exit on flip
        # =====================================================================
        if signal == '30M_BULL_BREAK':
            old_trend = trend_state
            trend_state = 'BULL'

            # Store higher timeframe swing levels
            if swing_low:
                htf_swing_low = swing_low
            if swing_high:
                htf_swing_high = swing_high

            print(f"30M BULL -> Trend: {old_trend} -> BULL")
            print(f"HTF Swings updated: Low={htf_swing_low}, High={htf_swing_high}")

            # Exit SHORT on trend flip (30M controls exits)
            if current_position == 'SHORT':
                print("30M flipped BULL - CLOSING SHORT")
                exit_position(price)

            save_state(trend_state, current_position, entry_price, stop_price, htf_swing_low, htf_swing_high)
            return jsonify({'status': 'trend_updated', 'trend': 'BULL', 'htf_swing_low': htf_swing_low})

        elif signal == '30M_BEAR_BREAK':
            old_trend = trend_state
            trend_state = 'BEAR'

            # Store higher timeframe swing levels
            if swing_low:
                htf_swing_low = swing_low
            if swing_high:
                htf_swing_high = swing_high

            print(f"30M BEAR -> Trend: {old_trend} -> BEAR")
            print(f"HTF Swings updated: Low={htf_swing_low}, High={htf_swing_high}")

            # Exit LONG on trend flip (30M controls exits)
            if current_position == 'LONG':
                print("30M flipped BEAR - CLOSING LONG")
                exit_position(price)

            save_state(trend_state, current_position, entry_price, stop_price, htf_swing_low, htf_swing_high)
            return jsonify({'status': 'trend_updated', 'trend': 'BEAR', 'htf_swing_high': htf_swing_high})

        # =====================================================================
        # 5M (LOWER TF) - Entries only, NO exits on opposite signal
        # =====================================================================
        elif signal == '5M_BULL_BREAK':
            print(f"5M BULL BREAK - Trend is {trend_state}")

            # Only enter if trend is BULL and not already LONG
            if trend_state == 'BULL' and current_position != 'LONG':
                # Use higher TF swing low for stop
                sl = htf_swing_low if htf_swing_low else swing_low
                if sl:
                    result = enter_long(price, sl)
                    return jsonify({'status': 'LONG_ENTERED', 'stop': sl * (1-STOP_BUFFER), 'result': result})
                else:
                    return jsonify({'status': 'no_entry', 'reason': 'no swing_low available'})

            # Do NOT exit SHORT on 5M bull break - only 30M exits
            return jsonify({'status': 'no_action', 'reason': f'trend={trend_state}, pos={current_position}'})

        elif signal == '5M_BEAR_BREAK':
            print(f"5M BEAR BREAK - Trend is {trend_state}")

            # Only enter if trend is BEAR and not already SHORT
            if trend_state == 'BEAR' and current_position != 'SHORT':
                # Use higher TF swing high for stop
                sh = htf_swing_high if htf_swing_high else swing_high
                if sh:
                    result = enter_short(price, sh)
                    return jsonify({'status': 'SHORT_ENTERED', 'stop': sh * (1+STOP_BUFFER), 'result': result})
                else:
                    return jsonify({'status': 'no_entry', 'reason': 'no swing_high available'})

            # Do NOT exit LONG on 5M bear break - only 30M exits
            return jsonify({'status': 'no_action', 'reason': f'trend={trend_state}, pos={current_position}'})

        return jsonify({'error': f'Unknown signal: {signal}'}), 400

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'error': str(e)}), 500

# =============================================================================
# ENDPOINTS
# =============================================================================
@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        'trend_state': trend_state,
        'current_position': current_position,
        'entry_price': entry_price,
        'stop_price': stop_price,
        'htf_swing_low': htf_swing_low,
        'htf_swing_high': htf_swing_high,
        'current_price': get_price(SYMBOL),
        'balance': get_usdt_balance(),
        'leverage': f"{LEVERAGE}x",
        'margin_mode': MARGIN_MODE,
        'position_mode': 'Full Account',
        'stop_buffer': f"{STOP_BUFFER*100}%"
    })

@app.route('/debug', methods=['GET'])
def debug():
    bal_response = api_request('GET', '/api/v1/asset/balances?accountType=futures')
    return jsonify({'balance_api_response': bal_response})

@app.route('/close', methods=['POST'])
def close_all():
    exit_position(get_price(SYMBOL) or 0)
    return jsonify({'status': 'closed'})

@app.route('/set_trend', methods=['POST'])
def set_trend_endpoint():
    global trend_state
    trend_state = request.json.get('trend', '').upper() or None
    save_state(trend_state, current_position, entry_price, stop_price, htf_swing_low, htf_swing_high)
    return jsonify({'trend': trend_state})

@app.route('/last_webhook', methods=['GET'])
def get_last_webhook():
    try:
        with open('last_webhook.json', 'r') as f:
            return jsonify(json.load(f))
    except:
        return jsonify({'msg': 'No webhook received yet'})

@app.route('/', methods=['GET'])
def home():
    return f'''<h1>MXS Multi-TF Bot</h1>
    <h2>State</h2>
    <ul>
        <li><b>Trend (30M):</b> {trend_state}</li>
        <li><b>Position:</b> {current_position}</li>
        <li><b>Entry:</b> {entry_price}</li>
        <li><b>Stop:</b> {stop_price}</li>
    </ul>
    <h2>HTF Swing Levels (30M)</h2>
    <ul>
        <li><b>Swing Low:</b> {htf_swing_low}</li>
        <li><b>Swing High:</b> {htf_swing_high}</li>
    </ul>
    <h2>Strategy</h2>
    <ul>
        <li>30M sets trend + stores swing levels + exits positions</li>
        <li>5M enters only (no exit on opposite signal)</li>
        <li>Stop: 2% beyond 30M swing level</li>
        <li>Position: FULL ACCOUNT</li>
        <li><b>Leverage: {LEVERAGE}x ({MARGIN_MODE} margin)</b></li>
    </ul>
    <p><a href="/debug">Last webhook</a> | <a href="/status">Status JSON</a></p>'''

if __name__ == '__main__':
    print(f"\n=== MXS BOT STARTED ===")
    print(f"Strategy: 30M trend/exits, 5M entries only")
    print(f"Leverage: {LEVERAGE}x | Margin: {MARGIN_MODE}")
    print(f"Position: FULL ACCOUNT | Stop Buffer: {STOP_BUFFER*100}%")
    print(f"Trend: {trend_state} | Position: {current_position}")
    print(f"HTF Swings: Low={htf_swing_low}, High={htf_swing_high}")
    print(f"===========================\n")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
