"""
MXS Multi-Timeframe Webhook Trading Bot
30-min trend filter + 5-min entries
BloFin Demo Trading
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
STOP_LOSS_PCT = 0.10
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
        return {'trend': None, 'position': None, 'entry': None}

def save_state(trend, position, entry):
    state = {'trend': trend, 'position': position, 'entry': entry}
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)
    print(f"[STATE SAVED] {state}")

# Load on startup
_s = load_state()
trend_state = _s.get('trend')
current_position = _s.get('position')
entry_price = _s.get('entry')

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
# TRADING
# =============================================================================
def calc_size(balance, price):
    return int((balance * 0.90 * LEVERAGE) / price)

def enter_long(price):
    global current_position, entry_price
    print(f"\n=== ENTERING LONG @ ${price:.4f} ===")

    if current_position == 'SHORT':
        close_position(SYMBOL, 'sell')

    bal = get_usdt_balance()
    if bal <= 0:
        return {'error': 'no balance'}

    size = calc_size(bal, price)
    sl = price * (1 - STOP_LOSS_PCT)
    set_leverage(SYMBOL, LEVERAGE)
    result = place_order(SYMBOL, 'buy', size, sl)

    if result.get('code') == '0':
        current_position = 'LONG'
        entry_price = price
        save_state(trend_state, current_position, entry_price)
    return result

def enter_short(price):
    global current_position, entry_price
    print(f"\n=== ENTERING SHORT @ ${price:.4f} ===")

    if current_position == 'LONG':
        close_position(SYMBOL, 'buy')

    bal = get_usdt_balance()
    if bal <= 0:
        return {'error': 'no balance'}

    size = calc_size(bal, price)
    sl = price * (1 + STOP_LOSS_PCT)
    set_leverage(SYMBOL, LEVERAGE)
    result = place_order(SYMBOL, 'sell', size, sl)

    if result.get('code') == '0':
        current_position = 'SHORT'
        entry_price = price
        save_state(trend_state, current_position, entry_price)
    return result

def exit_position(price):
    global current_position, entry_price
    print(f"\n=== EXITING {current_position} ===")

    if current_position == 'LONG':
        close_position(SYMBOL, 'buy')
    elif current_position == 'SHORT':
        close_position(SYMBOL, 'sell')
    else:
        return {'status': 'no position'}

    current_position = None
    entry_price = None
    save_state(trend_state, current_position, entry_price)
    return {'status': 'closed'}

# =============================================================================
# WEBHOOK
# =============================================================================
@app.route('/webhook', methods=['POST'])
def webhook():
    global trend_state, current_position, entry_price

    try:
        data = request.json
        signal = data.get('signal', '').upper()
        price = float(data.get('price', 0)) or get_price(SYMBOL)

        print(f"\n{'='*60}")
        print(f"SIGNAL: {signal} @ ${price:.4f}")
        print(f"STATE: trend={trend_state}, pos={current_position}")
        print(f"{'='*60}")

        # 30M - Set trend
        if signal == '30M_BULL_BREAK':
            trend_state = 'BULL'
            save_state(trend_state, current_position, entry_price)
            if current_position == 'SHORT':
                exit_position(price)
            return jsonify({'status': 'trend_updated', 'trend': 'BULL'})

        elif signal == '30M_BEAR_BREAK':
            trend_state = 'BEAR'
            save_state(trend_state, current_position, entry_price)
            if current_position == 'LONG':
                exit_position(price)
            return jsonify({'status': 'trend_updated', 'trend': 'BEAR'})

        # 5M - Entries
        elif signal == '5M_BULL_BREAK':
            if current_position == 'SHORT':
                exit_position(price)
            if trend_state == 'BULL' and current_position != 'LONG':
                result = enter_long(price)
                return jsonify({'status': 'LONG_ENTERED', 'result': result})
            return jsonify({'status': 'no_entry', 'reason': f'trend={trend_state}'})

        elif signal == '5M_BEAR_BREAK':
            if current_position == 'LONG':
                exit_position(price)
            if trend_state == 'BEAR' and current_position != 'SHORT':
                result = enter_short(price)
                return jsonify({'status': 'SHORT_ENTERED', 'result': result})
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
        'current_price': get_price(SYMBOL),
        'balance': get_usdt_balance()
    })

@app.route('/close', methods=['POST'])
def close_all():
    exit_position(get_price(SYMBOL) or 0)
    return jsonify({'status': 'closed'})

@app.route('/set_trend', methods=['POST'])
def set_trend_endpoint():
    global trend_state
    trend_state = request.json.get('trend', '').upper() or None
    save_state(trend_state, current_position, entry_price)
    return jsonify({'trend': trend_state})

@app.route('/', methods=['GET'])
def home():
    return f'''<h1>MXS Multi-TF Bot</h1>
    <p><b>Trend:</b> {trend_state} | <b>Position:</b> {current_position} | <b>Entry:</b> {entry_price}</p>
    <p>30M sets trend, 5M enters if aligned. State persisted to file.</p>'''

if __name__ == '__main__':
    print(f"\n=== MXS BOT STARTED ===")
    print(f"Trend: {trend_state} | Position: {current_position}")
    print(f"===========================\n")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
