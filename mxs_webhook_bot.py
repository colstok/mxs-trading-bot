"""
MXS Webhook Trading Bot - FILE-BASED STATE PERSISTENCE
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
API_KEY = os.environ.get('BLOFIN_API_KEY', '')
API_SECRET = os.environ.get('BLOFIN_API_SECRET', '')
PASSPHRASE = os.environ.get('BLOFIN_PASSPHRASE', '')
BASE_URL = "https://openapi.blofin.com"

SYMBOL = "FARTCOIN-USDT"
LEVERAGE = 10
STOP_BUFFER = 0.02
MARGIN_MODE = "isolated"

# State file path - will persist between requests
STATE_FILE = '/tmp/mxs_bot_state.json'

# =============================================================================
# STATE - File-based persistence for Render
# =============================================================================
class BotState:
    def __init__(self):
        self.trend = None
        self.htf_swing_low = None
        self.htf_swing_high = None
        self.last_signal = None
        self.last_signal_time = None
        self.signal_log = []
        self._load()  # Load state from file on init

    def _load(self):
        """Load state from file if it exists"""
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    self.trend = data.get('trend')
                    self.htf_swing_low = data.get('htf_swing_low')
                    self.htf_swing_high = data.get('htf_swing_high')
                    self.last_signal = data.get('last_signal')
                    self.last_signal_time = data.get('last_signal_time')
                    self.signal_log = data.get('signal_log', [])
                    print(f"[STATE] LOADED FROM FILE: trend={self.trend}")
        except Exception as e:
            print(f"[STATE] Failed to load state: {e}")

    def _save(self):
        """Save state to file"""
        try:
            data = {
                'trend': self.trend,
                'htf_swing_low': self.htf_swing_low,
                'htf_swing_high': self.htf_swing_high,
                'last_signal': self.last_signal,
                'last_signal_time': self.last_signal_time,
                'signal_log': self.signal_log[-100:]
            }
            with open(STATE_FILE, 'w') as f:
                json.dump(data, f)
            print(f"[STATE] SAVED TO FILE: trend={self.trend}")
        except Exception as e:
            print(f"[STATE] Failed to save state: {e}")

    def set_trend(self, trend, swing_low=None, swing_high=None):
        old_trend = self.trend
        self.trend = trend
        if swing_low:
            self.htf_swing_low = swing_low
        if swing_high:
            self.htf_swing_high = swing_high
        self.log(f"TREND CHANGED: {old_trend} -> {trend}")
        print(f"[STATE] TREND SET: {old_trend} -> {trend}")
        print(f"[STATE] Swings: low={self.htf_swing_low}, high={self.htf_swing_high}")
        self._save()  # Persist to file immediately

    def log(self, msg):
        entry = {'time': datetime.now().isoformat(), 'msg': msg}
        self.signal_log.append(entry)
        if len(self.signal_log) > 100:
            self.signal_log = self.signal_log[-100:]
        self._save()  # Persist logs too

    def to_dict(self):
        return {
            'trend': self.trend,
            'htf_swing_low': self.htf_swing_low,
            'htf_swing_high': self.htf_swing_high,
            'last_signal': self.last_signal,
            'last_signal_time': self.last_signal_time,
            'recent_logs': self.signal_log[-10:]
        }

# Single global state instance - loads from file on startup
STATE = BotState()

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

def close_position_on_blofin():
    pos = get_blofin_position()
    print(f"[CLOSE] Attempting to close position: {pos}")
    if pos['side'] == 'LONG':
        result = api_request('POST', '/api/v1/trade/close-position',
            {'instId': SYMBOL, 'marginMode': MARGIN_MODE, 'positionSide': 'net'})
        print(f"[CLOSE] Result: {result}")
        return result
    elif pos['side'] == 'SHORT':
        result = api_request('POST', '/api/v1/trade/close-position',
            {'instId': SYMBOL, 'marginMode': MARGIN_MODE, 'positionSide': 'net'})
        print(f"[CLOSE] Result: {result}")
        return result
    return {'status': 'no position'}

def place_order(symbol, side, size, sl=None):
    data = {'instId': symbol, 'marginMode': MARGIN_MODE, 'positionSide': 'net',
            'side': side, 'orderType': 'market', 'size': str(size)}
    if sl:
        data['slTriggerPrice'] = str(sl)
        data['slOrderPrice'] = '-1'
    return api_request('POST', '/api/v1/trade/order', data)

# =============================================================================
# TRADING
# =============================================================================
def enter_long(price, swing_low):
    stop = swing_low * (1 - STOP_BUFFER)
    print(f"\n{'='*50}")
    print(f"ENTERING LONG @ ${price:.4f}, Stop: ${stop:.4f}")
    print(f"{'='*50}")

    blofin_pos = get_blofin_position()
    if blofin_pos['side'] == 'SHORT':
        print("[CLOSE SHORT FIRST]")
        close_position_on_blofin()
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

    api_request('POST', '/api/v1/account/set-leverage', {'instId': SYMBOL, 'leverage': str(LEVERAGE), 'marginMode': MARGIN_MODE})
    result = place_order(SYMBOL, 'buy', size, stop)
    STATE.log(f"LONG ENTERED: size={size}, stop={stop}, result={result}")
    return result

def enter_short(price, swing_high):
    stop = swing_high * (1 + STOP_BUFFER)
    print(f"\n{'='*50}")
    print(f"ENTERING SHORT @ ${price:.4f}, Stop: ${stop:.4f}")
    print(f"{'='*50}")

    blofin_pos = get_blofin_position()
    if blofin_pos['side'] == 'LONG':
        print("[CLOSE LONG FIRST]")
        close_position_on_blofin()
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

    api_request('POST', '/api/v1/account/set-leverage', {'instId': SYMBOL, 'leverage': str(LEVERAGE), 'marginMode': MARGIN_MODE})
    result = place_order(SYMBOL, 'sell', size, stop)
    STATE.log(f"SHORT ENTERED: size={size}, stop={stop}, result={result}")
    return result

# =============================================================================
# WEBHOOK - Main signal handler
# =============================================================================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)

        signal = str(data.get('signal', '')).upper().strip()
        price = float(data.get('price', 0)) or get_price(SYMBOL)
        swing_low = float(data.get('swing_low')) if data.get('swing_low') else None
        swing_high = float(data.get('swing_high')) if data.get('swing_high') else None

        STATE.last_signal = signal
        STATE.last_signal_time = datetime.now().isoformat()
        STATE._save()  # Persist immediately

        blofin_pos = get_blofin_position()

        print(f"\n{'='*60}")
        print(f"WEBHOOK RECEIVED")
        print(f"Signal: {signal}")
        print(f"Price: {price}")
        print(f"Swing Low: {swing_low}, Swing High: {swing_high}")
        print(f"Current Trend: {STATE.trend}")
        print(f"Blofin Position: {blofin_pos}")
        print(f"{'='*60}")

        STATE.log(f"SIGNAL: {signal} | price={price} | trend={STATE.trend} | pos={blofin_pos['side']}")

        # =================================================================
        # 5M SIGNALS - Set trend and exit positions
        # =================================================================
        if '5M' in signal and ('BULL' in signal):
            STATE.set_trend('BULL', swing_low=swing_low, swing_high=swing_high)

            if blofin_pos['side'] == 'SHORT':
                print(">>> 5M BULL - CLOSING SHORT <<<")
                result = close_position_on_blofin()
                return jsonify({'action': 'CLOSED_SHORT', 'trend': 'BULL', 'result': str(result)})

            return jsonify({'action': 'TREND_SET', 'trend': 'BULL'})

        elif '5M' in signal and ('BEAR' in signal):
            STATE.set_trend('BEAR', swing_low=swing_low, swing_high=swing_high)

            if blofin_pos['side'] == 'LONG':
                print(">>> 5M BEAR - CLOSING LONG <<<")
                result = close_position_on_blofin()
                return jsonify({'action': 'CLOSED_LONG', 'trend': 'BEAR', 'result': str(result)})

            return jsonify({'action': 'TREND_SET', 'trend': 'BEAR'})

        # =================================================================
        # 1M SIGNALS - Entries only (must match trend)
        # =================================================================
        elif '1M' in signal and ('BULL' in signal):
            print(f"1M BULL - Trend={STATE.trend}, Pos={blofin_pos['side']}")

            if STATE.trend != 'BULL':
                return jsonify({'action': 'NO_ENTRY', 'reason': f'trend is {STATE.trend}, need BULL'})

            if blofin_pos['side'] == 'LONG':
                return jsonify({'action': 'NO_ENTRY', 'reason': 'already LONG'})

            sl = STATE.htf_swing_low or swing_low
            if not sl:
                return jsonify({'action': 'NO_ENTRY', 'reason': 'no swing_low for stop'})

            result = enter_long(price, sl)
            return jsonify({'action': 'LONG_ENTERED', 'stop': sl * (1-STOP_BUFFER), 'result': str(result)})

        elif '1M' in signal and ('BEAR' in signal):
            print(f"1M BEAR - Trend={STATE.trend}, Pos={blofin_pos['side']}")

            if STATE.trend != 'BEAR':
                return jsonify({'action': 'NO_ENTRY', 'reason': f'trend is {STATE.trend}, need BEAR'})

            if blofin_pos['side'] == 'SHORT':
                return jsonify({'action': 'NO_ENTRY', 'reason': 'already SHORT'})

            sh = STATE.htf_swing_high or swing_high
            if not sh:
                return jsonify({'action': 'NO_ENTRY', 'reason': 'no swing_high for stop'})

            result = enter_short(price, sh)
            return jsonify({'action': 'SHORT_ENTERED', 'stop': sh * (1+STOP_BUFFER), 'result': str(result)})

        else:
            STATE.log(f"UNKNOWN SIGNAL: {signal}")
            return jsonify({'error': f'Unknown signal: {signal}'}), 400

    except Exception as e:
        print(f"[ERROR] {e}")
        STATE.log(f"ERROR: {e}")
        return jsonify({'error': str(e)}), 500

# =============================================================================
# ENDPOINTS
# =============================================================================
@app.route('/status', methods=['GET'])
def status():
    blofin_pos = get_blofin_position()
    return jsonify({
        'trend': STATE.trend,
        'htf_swing_low': STATE.htf_swing_low,
        'htf_swing_high': STATE.htf_swing_high,
        'blofin_position': blofin_pos['side'],
        'blofin_size': blofin_pos['size'],
        'last_signal': STATE.last_signal,
        'last_signal_time': STATE.last_signal_time,
        'recent_logs': STATE.signal_log[-10:]
    })

@app.route('/set_trend', methods=['POST'])
def set_trend_endpoint():
    data = request.get_json(force=True)
    trend = str(data.get('trend', '')).upper().strip()
    if trend in ['BULL', 'BEAR']:
        STATE.set_trend(trend)
        return jsonify({'trend': STATE.trend, 'status': 'ok'})
    return jsonify({'error': 'trend must be BULL or BEAR'}), 400

@app.route('/set_swings', methods=['POST'])
def set_swings_endpoint():
    data = request.get_json(force=True)
    if 'swing_low' in data:
        STATE.htf_swing_low = float(data['swing_low'])
    if 'swing_high' in data:
        STATE.htf_swing_high = float(data['swing_high'])
    STATE.log(f"SWINGS SET: low={STATE.htf_swing_low}, high={STATE.htf_swing_high}")
    return jsonify({'swing_low': STATE.htf_swing_low, 'swing_high': STATE.htf_swing_high})

@app.route('/close', methods=['POST'])
def close_endpoint():
    result = close_position_on_blofin()
    STATE.log(f"MANUAL CLOSE: {result}")
    return jsonify({'result': str(result)})

@app.route('/logs', methods=['GET'])
def logs_endpoint():
    return jsonify({'logs': STATE.signal_log})

@app.route('/reset', methods=['POST'])
def reset_endpoint():
    """Reset all state - use with caution"""
    old_trend = STATE.trend
    STATE.trend = None
    STATE.htf_swing_low = None
    STATE.htf_swing_high = None
    STATE.last_signal = None
    STATE.last_signal_time = None
    STATE.signal_log = []
    STATE._save()
    STATE.log(f"STATE RESET (was trend={old_trend})")
    return jsonify({'status': 'reset', 'old_trend': old_trend})

@app.route('/state_file', methods=['GET'])
def state_file_endpoint():
    """Show raw state file contents for debugging"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return jsonify({'file_exists': True, 'contents': json.load(f)})
        return jsonify({'file_exists': False})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/', methods=['GET'])
def home():
    blofin_pos = get_blofin_position()
    logs_html = '<br>'.join([f"{l['time']}: {l['msg']}" for l in STATE.signal_log[-20:]])
    state_file_exists = os.path.exists(STATE_FILE)
    return f'''<html><head><meta http-equiv="refresh" content="5"></head><body>
    <h1>MXS Bot</h1>
    <h2>STATE (file: {STATE_FILE}, exists: {state_file_exists})</h2>
    <ul>
        <li><b style="font-size:24px;color:{'green' if STATE.trend == 'BULL' else 'red' if STATE.trend == 'BEAR' else 'gray'}">TREND: {STATE.trend}</b></li>
        <li>Swing Low: {STATE.htf_swing_low}</li>
        <li>Swing High: {STATE.htf_swing_high}</li>
        <li>Last Signal: {STATE.last_signal} @ {STATE.last_signal_time}</li>
    </ul>
    <h2>BLOFIN POSITION</h2>
    <ul>
        <li><b>{blofin_pos['side']}</b> - {blofin_pos['size']} @ ${blofin_pos['entry']:.4f}</li>
    </ul>
    <h2>RECENT LOGS</h2>
    <pre style="background:#111;color:#0f0;padding:10px;font-size:12px;">{logs_html}</pre>
    <p><a href="/status">Status JSON</a> | <a href="/logs">All Logs</a> | <a href="/state_file">State File</a></p>
    </body></html>'''

if __name__ == '__main__':
    print(f"\n=== MXS BOT STARTED ===")
    print(f"Trend: {STATE.trend}")
    print(f"===========================\n")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
