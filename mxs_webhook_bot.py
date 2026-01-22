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

# Load .env file
load_dotenv()

app = Flask(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================
API_KEY = os.environ.get('BLOFIN_API_KEY', 'your-api-key')
API_SECRET = os.environ.get('BLOFIN_API_SECRET', 'your-api-secret')
PASSPHRASE = os.environ.get('BLOFIN_PASSPHRASE', 'your-passphrase')

# Demo trading base URL
BASE_URL = "https://demo-trading-openapi.blofin.com"

# Strategy settings
SYMBOL = "FARTCOIN-USDT"
LEVERAGE = 3
STOP_LOSS_PCT = 0.10  # 10% stop loss
MARGIN_MODE = "cross"

# =============================================================================
# MULTI-TIMEFRAME STATE
# =============================================================================
trend_state = None      # 'BULL' or 'BEAR' or None (from 30-min)
current_position = None # 'LONG' or 'SHORT' or None
entry_price = None

# =============================================================================
# BLOFIN API FUNCTIONS
# =============================================================================

def get_timestamp():
    return str(int(time.time() * 1000))

def get_nonce():
    return str(uuid.uuid4())

def sign_request(request_path, method, timestamp, nonce, body=''):
    message = request_path + method.upper() + timestamp + nonce + body
    mac = hmac.new(
        bytes(API_SECRET, encoding='utf-8'),
        bytes(message, encoding='utf-8'),
        digestmod=hashlib.sha256
    )
    hex_digest = mac.hexdigest()
    return base64.b64encode(bytes(hex_digest, encoding='utf-8')).decode()

def api_request(method, endpoint, data=None):
    timestamp = get_timestamp()
    nonce = get_nonce()
    body = json.dumps(data, separators=(',', ':')) if data else ''
    signature = sign_request(endpoint, method, timestamp, nonce, body)

    headers = {
        'ACCESS-KEY': API_KEY,
        'ACCESS-SIGN': signature,
        'ACCESS-TIMESTAMP': timestamp,
        'ACCESS-PASSPHRASE': PASSPHRASE,
        'ACCESS-NONCE': nonce,
        'Content-Type': 'application/json'
    }

    url = BASE_URL + endpoint

    try:
        if method == 'GET':
            response = requests.get(url, headers=headers)
        elif method == 'POST':
            response = requests.post(url, headers=headers, data=body)
        return response.json()
    except Exception as e:
        print(f"API Error: {e}")
        return {'code': '-1', 'msg': str(e)}

def get_account_balance():
    return api_request('GET', '/api/v1/account/balance')

def get_usdt_balance():
    result = get_account_balance()
    if result.get('code') == '0':
        data = result.get('data', {})
        details = data.get('details', [])
        for asset in details:
            if asset.get('currency') == 'USDT':
                return float(asset.get('available', 0))
    return 0

def get_positions():
    return api_request('GET', '/api/v1/account/positions')

def set_leverage(symbol, leverage):
    data = {
        'instId': symbol,
        'leverage': str(leverage),
        'marginMode': MARGIN_MODE
    }
    return api_request('POST', '/api/v1/account/set-leverage', data)

def place_order(symbol, side, size, order_type='market', sl_trigger=None):
    data = {
        'instId': symbol,
        'marginMode': MARGIN_MODE,
        'positionSide': 'net',
        'side': side,
        'orderType': order_type,
        'size': str(size)
    }
    if sl_trigger:
        data['slTriggerPrice'] = str(sl_trigger)
        data['slOrderPrice'] = '-1'
    return api_request('POST', '/api/v1/trade/order', data)

def close_position(symbol, side):
    close_side = 'sell' if side == 'buy' else 'buy'
    data = {
        'instId': symbol,
        'marginMode': MARGIN_MODE,
        'positionSide': 'net',
        'side': close_side,
        'orderType': 'market',
        'size': '0',
        'reduceOnly': 'true'
    }
    return api_request('POST', '/api/v1/trade/close-position', data)

def get_current_price(symbol):
    try:
        url = "https://openapi.blofin.com/api/v1/market/tickers"
        response = requests.get(url, timeout=10)
        result = response.json()
        if result.get('code') == '0' and result.get('data'):
            for ticker in result['data']:
                if ticker.get('instId') == symbol:
                    return float(ticker['last'])
    except Exception as e:
        print(f"Error getting price: {e}")
    return None

# =============================================================================
# TRADING FUNCTIONS
# =============================================================================

def calculate_position_size(balance, price, leverage):
    usable_balance = balance * 0.90
    notional = usable_balance * leverage
    size = notional / price
    return int(size)

def execute_long(price):
    """Enter LONG position"""
    global current_position, entry_price

    print(f"\n{'='*50}")
    print(f"ENTERING LONG")
    print(f"Price: ${price:.4f}")
    print(f"{'='*50}")

    # Close SHORT if exists
    if current_position == 'SHORT':
        print("Closing existing SHORT...")
        result = close_position(SYMBOL, 'sell')
        print(f"Close result: {result}")

    usdt_balance = get_usdt_balance()
    if usdt_balance <= 0:
        print("No USDT balance")
        return {'error': 'No balance'}

    print(f"Balance: ${usdt_balance:.2f}")

    size = calculate_position_size(usdt_balance, price, LEVERAGE)
    stop_loss = price * (1 - STOP_LOSS_PCT)

    print(f"Size: {size} contracts")
    print(f"Stop: ${stop_loss:.4f}")

    set_leverage(SYMBOL, LEVERAGE)

    result = place_order(
        symbol=SYMBOL,
        side='buy',
        size=size,
        sl_trigger=stop_loss
    )

    print(f"Order result: {result}")

    if result.get('code') == '0':
        current_position = 'LONG'
        entry_price = price
        log_trade('LONG', price, stop_loss, size)

    return result

def execute_short(price):
    """Enter SHORT position"""
    global current_position, entry_price

    print(f"\n{'='*50}")
    print(f"ENTERING SHORT")
    print(f"Price: ${price:.4f}")
    print(f"{'='*50}")

    # Close LONG if exists
    if current_position == 'LONG':
        print("Closing existing LONG...")
        result = close_position(SYMBOL, 'buy')
        print(f"Close result: {result}")

    usdt_balance = get_usdt_balance()
    if usdt_balance <= 0:
        print("No USDT balance")
        return {'error': 'No balance'}

    print(f"Balance: ${usdt_balance:.2f}")

    size = calculate_position_size(usdt_balance, price, LEVERAGE)
    stop_loss = price * (1 + STOP_LOSS_PCT)

    print(f"Size: {size} contracts")
    print(f"Stop: ${stop_loss:.4f}")

    set_leverage(SYMBOL, LEVERAGE)

    result = place_order(
        symbol=SYMBOL,
        side='sell',
        size=size,
        sl_trigger=stop_loss
    )

    print(f"Order result: {result}")

    if result.get('code') == '0':
        current_position = 'SHORT'
        entry_price = price
        log_trade('SHORT', price, stop_loss, size)

    return result

def exit_position(price):
    """Exit current position without entering new one"""
    global current_position, entry_price

    if current_position == 'LONG':
        print("Exiting LONG (no new entry - trend not aligned)")
        result = close_position(SYMBOL, 'buy')
        print(f"Close result: {result}")
    elif current_position == 'SHORT':
        print("Exiting SHORT (no new entry - trend not aligned)")
        result = close_position(SYMBOL, 'sell')
        print(f"Close result: {result}")
    else:
        return {'status': 'no position'}

    current_position = None
    entry_price = None
    return {'status': 'closed'}

def log_trade(direction, trade_price, stop_loss, size):
    trade = {
        'timestamp': datetime.now().isoformat(),
        'direction': direction,
        'entry_price': trade_price,
        'stop_loss': stop_loss,
        'size': size,
        'leverage': LEVERAGE,
        'symbol': SYMBOL,
        'trend_state': trend_state
    }

    log_file = 'trade_log.json'

    try:
        with open(log_file, 'r') as f:
            trades = json.load(f)
    except:
        trades = []

    trades.append(trade)

    with open(log_file, 'w') as f:
        json.dump(trades, f, indent=2)

    print(f"Trade logged")

# =============================================================================
# WEBHOOK ENDPOINT
# =============================================================================

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Multi-Timeframe Strategy:

    30M signals update trend state (no direct trades):
      - 30M_BULL_BREAK -> trend_state = BULL
      - 30M_BEAR_BREAK -> trend_state = BEAR

    5M signals trigger trades IF aligned with trend:
      - 5M_BULL_BREAK -> Enter LONG only if trend_state == BULL
      - 5M_BEAR_BREAK -> Enter SHORT only if trend_state == BEAR

    5M opposite signals always EXIT current position:
      - 5M_BEAR_BREAK while LONG -> EXIT (but only enter SHORT if trend is BEAR)
      - 5M_BULL_BREAK while SHORT -> EXIT (but only enter LONG if trend is BULL)
    """
    global trend_state, current_position, entry_price

    try:
        data = request.json
        print(f"\n{'='*60}")
        print(f"WEBHOOK: {datetime.now()}")
        print(f"Data: {data}")
        print(f"Current State: trend={trend_state}, position={current_position}")
        print(f"{'='*60}")

        signal = data.get('signal', '').upper()
        price = float(data.get('price', 0))
        if price == 0:
            price = get_current_price(SYMBOL)
            if not price:
                return jsonify({'error': 'Could not get price'}), 400

        # =====================================================================
        # 30-MINUTE SIGNALS - Update trend state only
        # =====================================================================

        if signal == '30M_BULL_BREAK':
            old_trend = trend_state
            trend_state = 'BULL'
            print(f"30M BULL BREAK -> Trend updated: {old_trend} -> BULL")

            # If we're SHORT, exit (trend flipped against us)
            if current_position == 'SHORT':
                print("Trend flipped to BULL while SHORT - exiting position")
                exit_position(price)

            return jsonify({
                'status': 'trend_updated',
                'trend': 'BULL',
                'position': current_position
            })

        elif signal == '30M_BEAR_BREAK':
            old_trend = trend_state
            trend_state = 'BEAR'
            print(f"30M BEAR BREAK -> Trend updated: {old_trend} -> BEAR")

            # If we're LONG, exit (trend flipped against us)
            if current_position == 'LONG':
                print("Trend flipped to BEAR while LONG - exiting position")
                exit_position(price)

            return jsonify({
                'status': 'trend_updated',
                'trend': 'BEAR',
                'position': current_position
            })

        # =====================================================================
        # 5-MINUTE SIGNALS - Trade entries (filtered by trend)
        # =====================================================================

        elif signal == '5M_BULL_BREAK':
            print(f"5M BULL BREAK @ ${price:.4f}")

            # Exit SHORT if we have one
            if current_position == 'SHORT':
                print("Exiting SHORT on 5M bull break")
                exit_position(price)

            # Only enter LONG if trend is BULL
            if trend_state == 'BULL':
                if current_position != 'LONG':
                    print("Trend is BULL -> ENTERING LONG")
                    result = execute_long(price)
                    return jsonify({
                        'status': 'LONG_ENTERED',
                        'trend': trend_state,
                        'price': price,
                        'result': result
                    })
                else:
                    print("Already LONG, skipping")
                    return jsonify({'status': 'already_long'})
            else:
                print(f"Trend is {trend_state}, NOT BULL -> NO ENTRY (staying flat)")
                return jsonify({
                    'status': 'no_entry',
                    'reason': f'Trend is {trend_state}, not BULL',
                    'position': current_position
                })

        elif signal == '5M_BEAR_BREAK':
            print(f"5M BEAR BREAK @ ${price:.4f}")

            # Exit LONG if we have one
            if current_position == 'LONG':
                print("Exiting LONG on 5M bear break")
                exit_position(price)

            # Only enter SHORT if trend is BEAR
            if trend_state == 'BEAR':
                if current_position != 'SHORT':
                    print("Trend is BEAR -> ENTERING SHORT")
                    result = execute_short(price)
                    return jsonify({
                        'status': 'SHORT_ENTERED',
                        'trend': trend_state,
                        'price': price,
                        'result': result
                    })
                else:
                    print("Already SHORT, skipping")
                    return jsonify({'status': 'already_short'})
            else:
                print(f"Trend is {trend_state}, NOT BEAR -> NO ENTRY (staying flat)")
                return jsonify({
                    'status': 'no_entry',
                    'reason': f'Trend is {trend_state}, not BEAR',
                    'position': current_position
                })

        else:
            return jsonify({'error': f'Unknown signal: {signal}'}), 400

    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 500

# =============================================================================
# STATUS & CONTROL ENDPOINTS
# =============================================================================

@app.route('/status', methods=['GET'])
def status():
    balance = get_account_balance()
    positions = get_positions()
    current_price = get_current_price(SYMBOL)

    return jsonify({
        'status': 'running',
        'trend_state': trend_state,
        'current_position': current_position,
        'entry_price': entry_price,
        'current_price': current_price,
        'balance': balance,
        'positions': positions,
        'settings': {
            'symbol': SYMBOL,
            'leverage': LEVERAGE,
            'stop_loss_pct': STOP_LOSS_PCT
        }
    })

@app.route('/close', methods=['POST'])
def close_all():
    global current_position, entry_price
    result = exit_position(get_current_price(SYMBOL) or 0)
    current_position = None
    entry_price = None
    return jsonify({'status': 'closed', 'result': result})

@app.route('/set_trend', methods=['POST'])
def set_trend():
    """Manually set trend state (for testing)"""
    global trend_state
    data = request.json
    new_trend = data.get('trend', '').upper()
    if new_trend in ['BULL', 'BEAR', 'NONE']:
        trend_state = new_trend if new_trend != 'NONE' else None
        return jsonify({'status': 'ok', 'trend': trend_state})
    return jsonify({'error': 'Invalid trend, use BULL, BEAR, or NONE'}), 400

@app.route('/', methods=['GET'])
def home():
    return f'''
    <h1>MXS Multi-Timeframe Bot</h1>
    <h2>Current State</h2>
    <ul>
        <li><b>Trend (30M):</b> {trend_state}</li>
        <li><b>Position:</b> {current_position}</li>
        <li><b>Entry Price:</b> {entry_price}</li>
    </ul>
    <h2>Strategy</h2>
    <ul>
        <li>30M Bull/Bear Break -> Sets trend direction (no trade)</li>
        <li>5M Bull Break -> Enter LONG only if 30M trend is BULL</li>
        <li>5M Bear Break -> Enter SHORT only if 30M trend is BEAR</li>
        <li>5M opposite signal -> Always exits position</li>
    </ul>
    <h2>Signals</h2>
    <ul>
        <li>30M_BULL_BREAK - Update trend to BULL</li>
        <li>30M_BEAR_BREAK - Update trend to BEAR</li>
        <li>5M_BULL_BREAK - Entry signal for LONG</li>
        <li>5M_BEAR_BREAK - Entry signal for SHORT</li>
    </ul>
    <h2>Endpoints</h2>
    <ul>
        <li>POST /webhook - Receive signals</li>
        <li>GET /status - Check status</li>
        <li>POST /close - Close positions</li>
        <li>POST /set_trend - Manually set trend</li>
    </ul>
    <p>Symbol: {SYMBOL} | Leverage: {LEVERAGE}x | Stop: {STOP_LOSS_PCT*100}%</p>
    '''

# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    print("""
    ===================================================================
         MXS MULTI-TIMEFRAME BOT - 30M Trend + 5M Entry
    ===================================================================
      30M_BULL_BREAK / 30M_BEAR_BREAK -> Set trend (no trade)
      5M_BULL_BREAK -> LONG if trend is BULL
      5M_BEAR_BREAK -> SHORT if trend is BEAR
    -------------------------------------------------------------------
      Symbol: FARTCOIN-USDT | Leverage: 3x | Stop: 10%
    ===================================================================
    """)

    if API_KEY == 'your-api-key':
        print("\n[WARNING] API credentials not set!")
        print("Set: BLOFIN_API_KEY, BLOFIN_API_SECRET, BLOFIN_PASSPHRASE\n")

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
