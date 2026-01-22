"""
MXS Multi-Timeframe Webhook Trading Bot
30-min trend filter + 5-min entries
1% risk per trade, stops at swing levels
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
BASE_URL = "https://demo-trading-openapi.blofin.com"

SYMBOL = "FARTCOIN-USDT"
LEVERAGE = 3
RISK_PER_TRADE = 0.01  # 1% of account per trade
STOP_BUFFER = 0.01     # 1% buffer beyond swing level
MARGIN_MODE = "cross"

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
        return {'trend': None, 'position': None, 'entry': None, 'stop': None}

def save_state(trend, position, entry, stop=None):
    state = {'trend': trend, 'position': position, 'entry': entry, 'stop': stop}
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)
    print(f"[STATE SAVED] {state}")

# Load on startup
_s = load_state()
trend_state = _s.get('trend')
current_position = _s.get('position')
entry_price = _s.get('entry')
stop_price = _s.get('stop')

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
    r = api_request('GET', '/api/v1/account/balance')
    if r.get('code') == '0':
        for a in r.get('data', {}).get('details', []):
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
# POSITION SIZING WITH 1% RISK
# =============================================================================
def calc_position_size(balance, entry, stop):
    """
    Calculate position size for 1% account risk
    Risk Amount = Balance * 1%
    Position Size = Risk Amount / |Entry - Stop|
    """
    risk_amount = balance * RISK_PER_TRADE
    stop_distance = abs(entry - stop)

    if stop_distance == 0:
        print("[ERROR] Stop distance is 0, using fallback")
        return int((balance * 0.10 * LEVERAGE) / entry)  # Fallback to 10% of account

    # Position size in units
    position_size = risk_amount / stop_distance

    # With leverage, we need less margin
    # But position size stays the same (we're sizing based on risk, not margin)

    print(f"[SIZING] Balance: ${balance:.2f}")
    print(f"[SIZING] Risk (1%): ${risk_amount:.2f}")
    print(f"[SIZING] Entry: ${entry:.4f}, Stop: ${stop:.4f}")
    print(f"[SIZING] Stop Distance: ${stop_distance:.4f} ({(stop_distance/entry)*100:.2f}%)")
    print(f"[SIZING] Position Size: {int(position_size)} contracts")

    return int(position_size)

# =============================================================================
# TRADING
# =============================================================================
def enter_long(price, swing_low):
    global current_position, entry_price, stop_price

    # Stop 1% below swing low
    stop = swing_low * (1 - STOP_BUFFER)

    print(f"\n{'='*50}")
    print(f"ENTERING LONG @ ${price:.4f}")
    print(f"Swing Low: ${swing_low:.4f}")
    print(f"Stop (1% below): ${stop:.4f}")
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
        save_state(trend_state, current_position, entry_price, stop_price)
    return result

def enter_short(price, swing_high):
    global current_position, entry_price, stop_price

    # Stop 1% above swing high
    stop = swing_high * (1 + STOP_BUFFER)

    print(f"\n{'='*50}")
    print(f"ENTERING SHORT @ ${price:.4f}")
    print(f"Swing High: ${swing_high:.4f}")
    print(f"Stop (1% above): ${stop:.4f}")
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
        save_state(trend_state, current_position, entry_price, stop_price)
    return result

def exit_position(price):
    global current_position, entry_price, stop_price
    print(f"\n=== EXITING {current_position} ===")

    if current_position == 'LONG':
        close_position(SYMBOL, 'buy')
    elif current_position == 'SHORT':
        close_position(SYMBOL, 'sell')
    else:
        return {'status': 'no position'}

    current_position = None
    entry_price = None
    stop_price = None
    save_state(trend_state, current_position, entry_price, stop_price)
    return {'status': 'closed'}

# =============================================================================
# WEBHOOK
# =============================================================================
@app.route('/webhook', methods=['POST'])
def webhook():
    global trend_state, current_position, entry_price, stop_price

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
        print(f"STATE: trend={trend_state}, pos={current_position}")
        print(f"{'='*60}")

        # 30M - Set trend
        if signal == '30M_BULL_BREAK':
            trend_state = 'BULL'
            save_state(trend_state, current_position, entry_price, stop_price)
            if current_position == 'SHORT':
                exit_position(price)
            return jsonify({'status': 'trend_updated', 'trend': 'BULL'})

        elif signal == '30M_BEAR_BREAK':
            trend_state = 'BEAR'
            save_state(trend_state, current_position, entry_price, stop_price)
            if current_position == 'LONG':
                exit_position(price)
            return jsonify({'status': 'trend_updated', 'trend': 'BEAR'})

        # 5M - Entries with swing-based stops
        elif signal == '5M_BULL_BREAK':
            if current_position == 'SHORT':
                exit_position(price)

            if trend_state == 'BULL' and current_position != 'LONG':
                if swing_low:
                    result = enter_long(price, swing_low)
                    return jsonify({'status': 'LONG_ENTERED', 'stop': swing_low * 0.99, 'result': result})
                else:
                    return jsonify({'status': 'no_entry', 'reason': 'missing swing_low'})
            return jsonify({'status': 'no_entry', 'reason': f'trend={trend_state}'})

        elif signal == '5M_BEAR_BREAK':
            if current_position == 'LONG':
                exit_position(price)

            if trend_state == 'BEAR' and current_position != 'SHORT':
                if swing_high:
                    result = enter_short(price, swing_high)
                    return jsonify({'status': 'SHORT_ENTERED', 'stop': swing_high * 1.01, 'result': result})
                else:
                    return jsonify({'status': 'no_entry', 'reason': 'missing swing_high'})
            return jsonify({'status': 'no_entry', 'reason': f'trend={trend_state}'})

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
        'current_price': get_price(SYMBOL),
        'balance': get_usdt_balance(),
        'risk_per_trade': f"{RISK_PER_TRADE*100}%",
        'stop_buffer': f"{STOP_BUFFER*100}%"
    })

@app.route('/close', methods=['POST'])
def close_all():
    exit_position(get_price(SYMBOL) or 0)
    return jsonify({'status': 'closed'})

@app.route('/set_trend', methods=['POST'])
def set_trend_endpoint():
    global trend_state
    trend_state = request.json.get('trend', '').upper() or None
    save_state(trend_state, current_position, entry_price, stop_price)
    return jsonify({'trend': trend_state})

@app.route('/debug', methods=['GET'])
def get_debug():
    try:
        with open('last_webhook.json', 'r') as f:
            return jsonify(json.load(f))
    except:
        return jsonify({'msg': 'No webhook received yet'})

@app.route('/', methods=['GET'])
def home():
    return f'''<h1>MXS Multi-TF Bot</h1>
    <p><b>Trend:</b> {trend_state} | <b>Position:</b> {current_position}</p>
    <p><b>Entry:</b> {entry_price} | <b>Stop:</b> {stop_price}</p>
    <p><b>Risk:</b> {RISK_PER_TRADE*100}% per trade | <b>Stop Buffer:</b> {STOP_BUFFER*100}% beyond swing</p>
    <p>30M sets trend, 5M enters if aligned. Stops at swing levels.</p>
    <p><a href="/debug">View last webhook</a> | <a href="/status">Status JSON</a></p>'''

if __name__ == '__main__':
    print(f"\n=== MXS BOT STARTED ===")
    print(f"Risk: {RISK_PER_TRADE*100}% per trade")
    print(f"Stop Buffer: {STOP_BUFFER*100}% beyond swing")
    print(f"Trend: {trend_state} | Position: {current_position}")
    print(f"===========================\n")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
