"""
MXS Multi-Timeframe Webhook Trading Bot
- Lower TF (1M) for entries only
- Higher TF (5M) for exits and trend
- ALWAYS checks Blofin for actual position state (survives Render restarts)
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
LEVERAGE = 10
STOP_BUFFER = 0.02
MARGIN_MODE = "isolated"

# =============================================================================
# STATE - trend and swings still stored locally (can't get from Blofin)
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
        return {'trend': None, 'htf_swing_low': None, 'htf_swing_high': None}

def save_state(trend, htf_swing_low, htf_swing_high):
    state = {'trend': trend, 'htf_swing_low': htf_swing_low, 'htf_swing_high': htf_swing_high}
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)
    print(f"[STATE SAVED] {state}")

_s = load_state()
trend_state = _s.get('trend')
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
    """Get actual position from Blofin - this is the source of truth"""
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

def set_margin_mode(symbol, mode):
    return api_request('POST', '/api/v1/account/set-margin-mode', {'instId': symbol, 'marginMode': mode})

def set_position_mode(mode):
    return api_request('POST', '/api/v1/account/set-position-mode', {'positionMode': mode})

def set_leverage(symbol, lev):
    return api_request('POST', '/api/v1/account/set-leverage', {'instId': symbol, 'leverage': str(lev), 'marginMode': MARGIN_MODE})

def place_order(symbol, side, size, sl=None):
    data = {'instId': symbol, 'marginMode': MARGIN_MODE, 'positionSide': 'net',
            'side': side, 'orderType': 'market', 'size': str(size)}
    if sl:
        data['slTriggerPrice'] = str(sl)
        data['slOrderPrice'] = '-1'
    return api_request('POST', '/api/v1/trade/order', data)

def close_position_on_blofin():
    """Close whatever position exists on Blofin"""
    pos = get_blofin_position()
    if pos['side'] == 'LONG':
        return api_request('POST', '/api/v1/trade/close-position',
            {'instId': SYMBOL, 'marginMode': MARGIN_MODE, 'positionSide': 'net', 'side': 'sell', 'orderType': 'market', 'size': '0', 'reduceOnly': 'true'})
    elif pos['side'] == 'SHORT':
        return api_request('POST', '/api/v1/trade/close-position',
            {'instId': SYMBOL, 'marginMode': MARGIN_MODE, 'positionSide': 'net', 'side': 'buy', 'orderType': 'market', 'size': '0', 'reduceOnly': 'true'})
    return {'status': 'no position to close'}

# =============================================================================
# POSITION SIZING
# =============================================================================
def calc_position_size(balance, entry, stop):
    position_value = balance * 0.95 * LEVERAGE
    position_size = position_value / entry
    stop_pct = (abs(entry - stop) / entry) * 100
    print(f"[SIZING] Balance: ${balance:.2f}, Entry: ${entry:.4f}, Stop: ${stop:.4f} ({stop_pct:.2f}%), Size: {int(position_size)}")
    return int(position_size)

# =============================================================================
# TRADING - Always checks Blofin first
# =============================================================================
def enter_long(price, swing_low):
    stop = swing_low * (1 - STOP_BUFFER)
    print(f"\n{'='*50}")
    print(f"ENTERING LONG @ ${price:.4f}, Stop: ${stop:.4f}")
    print(f"{'='*50}")

    # Check Blofin for actual position
    blofin_pos = get_blofin_position()
    if blofin_pos['side'] == 'SHORT':
        print("[BLOFIN] Closing existing SHORT first")
        close_position_on_blofin()
    elif blofin_pos['side'] == 'LONG':
        print("[BLOFIN] Already LONG, skipping")
        return {'status': 'already_long'}

    bal = get_usdt_balance()
    if bal <= 0:
        return {'error': 'no balance'}

    size = calc_position_size(bal, price, stop)
    if size <= 0:
        return {'error': 'position size too small'}

    set_position_mode('net_mode')
    set_margin_mode(SYMBOL, MARGIN_MODE)
    set_leverage(SYMBOL, LEVERAGE)
    result = place_order(SYMBOL, 'buy', size, stop)
    print(f"[ORDER] Result: {result}")
    return result

def enter_short(price, swing_high):
    stop = swing_high * (1 + STOP_BUFFER)
    print(f"\n{'='*50}")
    print(f"ENTERING SHORT @ ${price:.4f}, Stop: ${stop:.4f}")
    print(f"{'='*50}")

    # Check Blofin for actual position
    blofin_pos = get_blofin_position()
    if blofin_pos['side'] == 'LONG':
        print("[BLOFIN] Closing existing LONG first")
        close_position_on_blofin()
    elif blofin_pos['side'] == 'SHORT':
        print("[BLOFIN] Already SHORT, skipping")
        return {'status': 'already_short'}

    bal = get_usdt_balance()
    if bal <= 0:
        return {'error': 'no balance'}

    size = calc_position_size(bal, price, stop)
    if size <= 0:
        return {'error': 'position size too small'}

    set_position_mode('net_mode')
    set_margin_mode(SYMBOL, MARGIN_MODE)
    set_leverage(SYMBOL, LEVERAGE)
    result = place_order(SYMBOL, 'sell', size, stop)
    print(f"[ORDER] Result: {result}")
    return result

# =============================================================================
# WEBHOOK
# =============================================================================
@app.route('/webhook', methods=['POST'])
def webhook():
    global trend_state, htf_swing_low, htf_swing_high

    try:
        data = request.json
        with open('last_webhook.json', 'w') as f:
            json.dump({'received_at': datetime.now().isoformat(), 'data': data}, f, indent=2)

        signal = data.get('signal', '').upper()
        price = float(data.get('price', 0)) or get_price(SYMBOL)
        swing_low = float(data.get('swing_low', 0)) if data.get('swing_low') else None
        swing_high = float(data.get('swing_high', 0)) if data.get('swing_high') else None

        # Always check Blofin for actual position
        blofin_pos = get_blofin_position()

        print(f"\n{'='*60}")
        print(f"SIGNAL: {signal} @ ${price:.4f}")
        print(f"Swing Low: {swing_low}, Swing High: {swing_high}")
        print(f"HTF Swings: Low={htf_swing_low}, High={htf_swing_high}")
        print(f"TREND: {trend_state}")
        print(f"BLOFIN POSITION: {blofin_pos['side']} (size: {blofin_pos['size']})")
        print(f"{'='*60}")

        # =====================================================================
        # 5M SIGNALS - Trend + Exits
        # =====================================================================
        if signal == '5M_BULL_BREAK':
            old_trend = trend_state
            trend_state = 'BULL'
            if swing_low:
                htf_swing_low = swing_low
            if swing_high:
                htf_swing_high = swing_high

            print(f"5M BULL -> Trend: {old_trend} -> BULL")
            save_state(trend_state, htf_swing_low, htf_swing_high)

            # Exit SHORT if we have one (check Blofin, not internal state)
            if blofin_pos['side'] == 'SHORT':
                print("5M flipped BULL - CLOSING SHORT on Blofin")
                result = close_position_on_blofin()
                return jsonify({'status': 'trend_bull_closed_short', 'close_result': result})

            return jsonify({'status': 'trend_updated', 'trend': 'BULL'})

        elif signal == '5M_BEAR_BREAK':
            old_trend = trend_state
            trend_state = 'BEAR'
            if swing_low:
                htf_swing_low = swing_low
            if swing_high:
                htf_swing_high = swing_high

            print(f"5M BEAR -> Trend: {old_trend} -> BEAR")
            save_state(trend_state, htf_swing_low, htf_swing_high)

            # Exit LONG if we have one (check Blofin, not internal state)
            if blofin_pos['side'] == 'LONG':
                print("5M flipped BEAR - CLOSING LONG on Blofin")
                result = close_position_on_blofin()
                return jsonify({'status': 'trend_bear_closed_long', 'close_result': result})

            return jsonify({'status': 'trend_updated', 'trend': 'BEAR'})

        # =====================================================================
        # 1M SIGNALS - Entries only
        # =====================================================================
        elif signal == '1M_BULL_BREAK' or signal == '1M_BULL_CONTINUATION':
            print(f"1M BULL signal - Trend is {trend_state}, Blofin pos: {blofin_pos['side']}")

            if trend_state == 'BULL' and blofin_pos['side'] != 'LONG':
                sl = htf_swing_low if htf_swing_low else swing_low
                if sl:
                    result = enter_long(price, sl)
                    return jsonify({'status': 'LONG_ENTERED', 'stop': sl * (1-STOP_BUFFER), 'result': result})
                else:
                    return jsonify({'status': 'no_entry', 'reason': 'no swing_low'})

            return jsonify({'status': 'no_action', 'reason': f'trend={trend_state}, blofin_pos={blofin_pos["side"]}'})

        elif signal == '1M_BEAR_BREAK' or signal == '1M_BEAR_CONTINUATION':
            print(f"1M BEAR signal - Trend is {trend_state}, Blofin pos: {blofin_pos['side']}")

            if trend_state == 'BEAR' and blofin_pos['side'] != 'SHORT':
                sh = htf_swing_high if htf_swing_high else swing_high
                if sh:
                    result = enter_short(price, sh)
                    return jsonify({'status': 'SHORT_ENTERED', 'stop': sh * (1+STOP_BUFFER), 'result': result})
                else:
                    return jsonify({'status': 'no_entry', 'reason': 'no swing_high'})

            return jsonify({'status': 'no_action', 'reason': f'trend={trend_state}, blofin_pos={blofin_pos["side"]}'})

        return jsonify({'error': f'Unknown signal: {signal}'}), 400

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'error': str(e)}), 500

# =============================================================================
# ENDPOINTS
# =============================================================================
@app.route('/status', methods=['GET'])
def status():
    blofin_pos = get_blofin_position()
    return jsonify({
        'trend_state': trend_state,
        'htf_swing_low': htf_swing_low,
        'htf_swing_high': htf_swing_high,
        'blofin_position': blofin_pos['side'],
        'blofin_size': blofin_pos['size'],
        'blofin_entry': blofin_pos['entry'],
        'current_price': get_price(SYMBOL),
        'balance': get_usdt_balance(),
        'leverage': f"{LEVERAGE}x",
        'margin_mode': MARGIN_MODE
    })

@app.route('/positions', methods=['GET'])
def positions():
    return jsonify({'positions': api_request('GET', '/api/v1/account/positions')})

@app.route('/orders', methods=['GET'])
def orders():
    pending = api_request('GET', '/api/v1/trade/orders-pending?instId=FARTCOIN-USDT')
    tpsl = api_request('GET', '/api/v1/trade/orders-tpsl-pending?instId=FARTCOIN-USDT')
    return jsonify({'pending_orders': pending, 'tpsl_orders': tpsl})

@app.route('/close', methods=['POST'])
def close_all():
    result = close_position_on_blofin()
    return jsonify({'status': 'closed', 'result': result})

@app.route('/set_trend', methods=['POST'])
def set_trend_endpoint():
    global trend_state
    trend_state = request.json.get('trend', '').upper() or None
    save_state(trend_state, htf_swing_low, htf_swing_high)
    return jsonify({'trend': trend_state})

@app.route('/set_swings', methods=['POST'])
def set_swings_endpoint():
    global htf_swing_low, htf_swing_high
    data = request.json
    if 'htf_swing_low' in data:
        htf_swing_low = float(data['htf_swing_low']) if data['htf_swing_low'] else None
    if 'htf_swing_high' in data:
        htf_swing_high = float(data['htf_swing_high']) if data['htf_swing_high'] else None
    save_state(trend_state, htf_swing_low, htf_swing_high)
    return jsonify({'htf_swing_low': htf_swing_low, 'htf_swing_high': htf_swing_high})

@app.route('/last_webhook', methods=['GET'])
def get_last_webhook():
    try:
        with open('last_webhook.json', 'r') as f:
            return jsonify(json.load(f))
    except:
        return jsonify({'msg': 'No webhook received yet'})

@app.route('/', methods=['GET'])
def home():
    blofin_pos = get_blofin_position()
    return f'''<h1>MXS Bot - 1M/5M</h1>
    <h2>Blofin Position (Source of Truth)</h2>
    <ul>
        <li><b>Position:</b> {blofin_pos['side']} ({blofin_pos['size']} contracts)</li>
        <li><b>Entry:</b> ${blofin_pos['entry']:.4f}</li>
    </ul>
    <h2>Bot State</h2>
    <ul>
        <li><b>Trend:</b> {trend_state}</li>
        <li><b>HTF Swing Low:</b> {htf_swing_low}</li>
        <li><b>HTF Swing High:</b> {htf_swing_high}</li>
    </ul>
    <h2>Strategy</h2>
    <ul>
        <li>5M: Sets trend, exits positions on flip</li>
        <li>1M: Entries only (breaks + continuations)</li>
        <li>Stop: 2% beyond 5M swing</li>
        <li>Leverage: {LEVERAGE}x isolated</li>
    </ul>
    <p><a href="/status">Status JSON</a> | <a href="/positions">Positions</a> | <a href="/orders">Orders</a></p>'''

if __name__ == '__main__':
    print(f"\n=== MXS BOT STARTED ===")
    print(f"Strategy: 5M trend/exits, 1M entries")
    print(f"Leverage: {LEVERAGE}x | Stop Buffer: {STOP_BUFFER*100}%")
    print(f"Trend: {trend_state}")
    print(f"HTF Swings: Low={htf_swing_low}, High={htf_swing_high}")
    blofin_pos = get_blofin_position()
    print(f"BLOFIN POSITION: {blofin_pos['side']}")
    print(f"===========================\n")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
