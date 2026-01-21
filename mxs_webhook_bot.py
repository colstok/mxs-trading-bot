"""
MXS Bull/Bear Break Webhook Trading Bot
BloFin Demo Trading
"""

import os
import json
import hmac
import hashlib
import base64
import time
import requests
import threading
from flask import Flask, request, jsonify
from datetime import datetime
from dotenv import load_dotenv

# Load .env file
load_dotenv()

app = Flask(__name__)

# =============================================================================
# CONFIGURATION - UPDATE THESE WITH YOUR BLOFIN DEMO API CREDENTIALS
# =============================================================================
API_KEY = os.environ.get('BLOFIN_API_KEY', 'your-api-key')
API_SECRET = os.environ.get('BLOFIN_API_SECRET', 'your-api-secret')
PASSPHRASE = os.environ.get('BLOFIN_PASSPHRASE', 'your-passphrase')

# Demo trading base URL
BASE_URL = "https://demo-trading-openapi.blofin.com"

# Strategy settings
SYMBOL = "FARTCOIN-USDT"  # BloFin format
LEVERAGE = 3
STOP_LOSS_PCT = 0.10  # 10% price-based stop loss
MARGIN_MODE = "cross"  # or "isolated"

# Webhook secret (optional - for verifying TradingView requests)
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', '')

# =============================================================================
# BLOFIN API FUNCTIONS
# =============================================================================

import uuid

def get_timestamp():
    return str(int(time.time() * 1000))

def get_nonce():
    return str(uuid.uuid4())

def sign_request(request_path, method, timestamp, nonce, body=''):
    """
    BloFin signature: requestPath + method + timestamp + nonce + body
    Then: HMAC-SHA256 -> hex -> bytes -> Base64
    """
    message = request_path + method.upper() + timestamp + nonce + body

    mac = hmac.new(
        bytes(API_SECRET, encoding='utf-8'),
        bytes(message, encoding='utf-8'),
        digestmod=hashlib.sha256
    )

    hex_digest = mac.hexdigest()
    return base64.b64encode(bytes(hex_digest, encoding='utf-8')).decode()

def api_request(method, endpoint, data=None):
    """Make authenticated request to BloFin API"""
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
    """Get futures account balance"""
    result = api_request('GET', '/api/v1/account/balance')
    return result

def get_usdt_balance():
    """Get available USDT balance"""
    result = get_account_balance()
    if result.get('code') == '0':
        data = result.get('data', {})
        details = data.get('details', [])
        for asset in details:
            if asset.get('currency') == 'USDT':
                return float(asset.get('available', 0))
    return 0

def get_positions():
    """Get current open positions"""
    result = api_request('GET', '/api/v1/account/positions')
    return result

def set_leverage(symbol, leverage):
    """Set leverage for a symbol"""
    data = {
        'instId': symbol,
        'leverage': str(leverage),
        'marginMode': MARGIN_MODE
    }
    result = api_request('POST', '/api/v1/account/set-leverage', data)
    return result

def place_order(symbol, side, size, order_type='market', price=None, sl_trigger=None, sl_price=None):
    """
    Place a futures order
    side: 'buy' or 'sell'
    """
    data = {
        'instId': symbol,
        'marginMode': MARGIN_MODE,
        'positionSide': 'net',  # one-way mode
        'side': side,
        'orderType': order_type,
        'size': str(size)
    }

    if price and order_type == 'limit':
        data['price'] = str(price)

    # Add stop loss if provided
    if sl_trigger:
        data['slTriggerPrice'] = str(sl_trigger)
        data['slOrderPrice'] = '-1'  # market execution

    result = api_request('POST', '/api/v1/trade/order', data)
    return result

def close_position(symbol, side):
    """Close an open position"""
    # To close: if we're long, we sell; if we're short, we buy
    close_side = 'sell' if side == 'buy' else 'buy'

    data = {
        'instId': symbol,
        'marginMode': MARGIN_MODE,
        'positionSide': 'net',
        'side': close_side,
        'orderType': 'market',
        'size': '0',  # Will be filled by reduceOnly
        'reduceOnly': 'true'
    }

    result = api_request('POST', '/api/v1/trade/close-position', data)
    return result

def get_current_price(symbol):
    """Get current market price from LIVE API (public endpoint)"""
    try:
        # Use live API tickers endpoint (demo doesn't have market data)
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
# TRADING LOGIC
# =============================================================================

current_position = None  # 'LONG', 'SHORT', or None
entry_price = None  # Price we entered at
monitor_thread = None  # Background monitoring thread
CHECK_INTERVAL = 1800  # 30 minutes in seconds

def start_position_monitor():
    """Start the background thread that checks position every 30 min"""
    global monitor_thread

    if monitor_thread is not None and monitor_thread.is_alive():
        return  # Already running

    monitor_thread = threading.Thread(target=position_monitor_loop, daemon=True)
    monitor_thread.start()
    print(f"[MONITOR] Started position monitoring (every {CHECK_INTERVAL//60} min)")

def position_monitor_loop():
    """Background loop that checks if breakout is still valid"""
    global current_position, entry_price

    while True:
        time.sleep(CHECK_INTERVAL)  # Wait 30 minutes

        if current_position is None or entry_price is None:
            print(f"[MONITOR] No position to check")
            continue

        current_price = get_current_price(SYMBOL)
        if current_price is None:
            print(f"[MONITOR] Could not get current price")
            continue

        print(f"\n{'='*50}")
        print(f"[MONITOR] 30-MIN CHECK - {datetime.now()}")
        print(f"Position: {current_position}")
        print(f"Entry Price: ${entry_price:.4f}")
        print(f"Current Price: ${current_price:.4f}")

        # Check if breakout failed
        if current_position == 'LONG':
            if current_price < entry_price:
                # Price fell back below entry - breakout failed!
                pct_change = ((current_price - entry_price) / entry_price) * 100
                print(f"[MONITOR] BREAKOUT FAILED! Price is {pct_change:.2f}% below entry")
                print(f"[MONITOR] Flipping from LONG to SHORT")
                execute_bear_break(current_price)
            else:
                pct_change = ((current_price - entry_price) / entry_price) * 100
                print(f"[MONITOR] Breakout holding. Price is +{pct_change:.2f}% from entry")

        elif current_position == 'SHORT':
            if current_price > entry_price:
                # Price rose back above entry - breakout failed!
                pct_change = ((current_price - entry_price) / entry_price) * 100
                print(f"[MONITOR] BREAKOUT FAILED! Price is +{pct_change:.2f}% above entry")
                print(f"[MONITOR] Flipping from SHORT to LONG")
                execute_bull_break(current_price)
            else:
                pct_change = ((entry_price - current_price) / entry_price) * 100
                print(f"[MONITOR] Breakout holding. Price is +{pct_change:.2f}% in profit")

        print(f"{'='*50}\n")

def calculate_position_size(balance, price, leverage):
    """Calculate position size based on balance"""
    # Use 90% of balance to leave margin buffer
    usable_balance = balance * 0.90
    # Position size in contracts (use integer for most coins)
    notional = usable_balance * leverage
    size = notional / price
    return int(size)  # BloFin requires integer contract sizes

def execute_bull_break(price):
    """Execute LONG entry on Bull Break signal"""
    global current_position, entry_price

    print(f"\n{'='*50}")
    print(f"BULL BREAK SIGNAL - Going LONG")
    print(f"Price: ${price:.4f}")
    print(f"{'='*50}")

    # Close any existing SHORT position
    if current_position == 'SHORT':
        print("Closing existing SHORT position...")
        result = close_position(SYMBOL, 'sell')
        print(f"Close result: {result}")

    # Get account balance
    usdt_balance = get_usdt_balance()
    if usdt_balance <= 0:
        print("No USDT balance available")
        return

    print(f"Available balance: ${usdt_balance:.2f}")

    # Calculate position size
    size = calculate_position_size(usdt_balance, price, LEVERAGE)

    # Calculate stop loss (15% below entry)
    stop_loss = price * (1 - STOP_LOSS_PCT)

    print(f"Position size: {size} contracts")
    print(f"Stop loss: ${stop_loss:.4f} (-{STOP_LOSS_PCT*100}%)")

    # Set leverage
    set_leverage(SYMBOL, LEVERAGE)

    # Place LONG order with stop loss
    result = place_order(
        symbol=SYMBOL,
        side='buy',
        size=size,
        order_type='market',
        sl_trigger=stop_loss
    )

    print(f"Order result: {result}")

    if result.get('code') == '0':
        current_position = 'LONG'
        entry_price = price
        log_trade('LONG', price, stop_loss, size)
        start_position_monitor()  # Start 30-min checks

    return result

def execute_bear_break(price):
    """Execute SHORT entry on Bear Break signal"""
    global current_position, entry_price

    print(f"\n{'='*50}")
    print(f"BEAR BREAK SIGNAL - Going SHORT")
    print(f"Price: ${price:.4f}")
    print(f"{'='*50}")

    # Close any existing LONG position
    if current_position == 'LONG':
        print("Closing existing LONG position...")
        result = close_position(SYMBOL, 'buy')
        print(f"Close result: {result}")

    # Get account balance
    usdt_balance = get_usdt_balance()
    if usdt_balance <= 0:
        print("No USDT balance available")
        return

    print(f"Available balance: ${usdt_balance:.2f}")

    # Calculate position size
    size = calculate_position_size(usdt_balance, price, LEVERAGE)

    # Calculate stop loss (15% above entry)
    stop_loss = price * (1 + STOP_LOSS_PCT)

    print(f"Position size: {size} contracts")
    print(f"Stop loss: ${stop_loss:.4f} (+{STOP_LOSS_PCT*100}%)")

    # Set leverage
    set_leverage(SYMBOL, LEVERAGE)

    # Place SHORT order with stop loss
    result = place_order(
        symbol=SYMBOL,
        side='sell',
        size=size,
        order_type='market',
        sl_trigger=stop_loss
    )

    print(f"Order result: {result}")

    if result.get('code') == '0':
        current_position = 'SHORT'
        entry_price = price
        log_trade('SHORT', price, stop_loss, size)
        start_position_monitor()  # Start 30-min checks

    return result

def log_trade(direction, trade_price, stop_loss, size):
    """Log trade to file"""
    trade = {
        'timestamp': datetime.now().isoformat(),
        'direction': direction,
        'entry_price': trade_price,
        'stop_loss': stop_loss,
        'size': size,
        'leverage': LEVERAGE,
        'symbol': SYMBOL
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

    print(f"Trade logged to {log_file}")

# =============================================================================
# WEBHOOK ENDPOINTS
# =============================================================================

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Receive TradingView webhook alerts - ENTERS IMMEDIATELY on signal

    Expected payload:
    {
        "signal": "BULL_BREAK" or "BEAR_BREAK" or "BULL_CONTINUATION" or "BEAR_CONTINUATION",
        "price": 0.3456
    }

    Strategy:
    - Bull Break -> Enter LONG immediately (flips if in SHORT)
    - Bear Break -> Enter SHORT immediately (flips if in LONG)
    - Bull Continuation -> Enter LONG only if flat (no position)
    - Bear Continuation -> Enter SHORT only if flat (no position)
    - 30-minute monitor checks if breakout is still valid
    """
    try:
        data = request.json
        print(f"\n{'='*50}")
        print(f"WEBHOOK RECEIVED: {datetime.now()}")
        print(f"Data: {data}")
        print(f"{'='*50}")

        signal = data.get('signal', '').upper()

        # Get price from webhook or fetch current
        price = float(data.get('price', 0) or data.get('close', 0))
        if price == 0:
            price = get_current_price(SYMBOL)
            if not price:
                return jsonify({'error': 'Could not get price'}), 400

        print(f"Signal: {signal}")
        print(f"Entry Price: ${price:.4f}")

        if signal == 'BULL_BREAK':
            print("BULL BREAK - Entering LONG immediately")
            print("(30-min monitor will check if breakout holds)")
            result = execute_bull_break(price)
            return jsonify({'status': 'LONG opened', 'entry_price': price, 'result': result})

        elif signal == 'BEAR_BREAK':
            print("BEAR BREAK - Entering SHORT immediately")
            print("(30-min monitor will check if breakout holds)")
            result = execute_bear_break(price)
            return jsonify({'status': 'SHORT opened', 'entry_price': price, 'result': result})

        elif signal == 'BULL_CONTINUATION':
            # Skip if already LONG, otherwise enter/flip to LONG
            if current_position == 'LONG':
                print("BULL CONTINUATION - Skipping, already LONG")
                return jsonify({'status': 'skipped', 'reason': 'Already in LONG position'})
            if current_position == 'SHORT':
                print("BULL CONTINUATION - Flipping from SHORT to LONG")
            else:
                print("BULL CONTINUATION - Entering LONG (was flat)")
            result = execute_bull_break(price)
            return jsonify({'status': 'LONG opened on continuation', 'entry_price': price, 'result': result})

        elif signal == 'BEAR_CONTINUATION':
            # Skip if already SHORT, otherwise enter/flip to SHORT
            if current_position == 'SHORT':
                print("BEAR CONTINUATION - Skipping, already SHORT")
                return jsonify({'status': 'skipped', 'reason': 'Already in SHORT position'})
            if current_position == 'LONG':
                print("BEAR CONTINUATION - Flipping from LONG to SHORT")
            else:
                print("BEAR CONTINUATION - Entering SHORT (was flat)")
            result = execute_bear_break(price)
            return jsonify({'status': 'SHORT opened on continuation', 'entry_price': price, 'result': result})

        else:
            return jsonify({'error': f'Unknown signal: {signal}'}), 400

    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/status', methods=['GET'])
def status():
    """Check bot status and current position"""
    balance = get_account_balance()
    positions = get_positions()
    current_price = get_current_price(SYMBOL)

    return jsonify({
        'status': 'running',
        'current_position': current_position,
        'entry_price': entry_price,
        'current_price': current_price,
        'monitor_active': monitor_thread is not None and monitor_thread.is_alive() if monitor_thread else False,
        'balance': balance,
        'positions': positions,
        'settings': {
            'symbol': SYMBOL,
            'leverage': LEVERAGE,
            'stop_loss_pct': STOP_LOSS_PCT,
            'margin_mode': MARGIN_MODE
        }
    })

@app.route('/close', methods=['POST'])
def close_all():
    """Manually close all positions"""
    global current_position, entry_price

    if current_position == 'LONG':
        result = close_position(SYMBOL, 'buy')
    elif current_position == 'SHORT':
        result = close_position(SYMBOL, 'sell')
    else:
        result = {'msg': 'No position to close'}

    current_position = None
    entry_price = None
    return jsonify({'status': 'closed', 'result': result})

@app.route('/check', methods=['GET', 'POST'])
def check_position():
    """
    Check if breakout is still valid - call this via cron every 30 min
    If LONG and price < entry -> flip to SHORT
    If SHORT and price > entry -> flip to LONG
    """
    global current_position, entry_price

    if current_position is None or entry_price is None:
        return jsonify({'status': 'no position', 'action': 'none'})

    current_price = get_current_price(SYMBOL)
    if current_price is None:
        return jsonify({'error': 'Could not get price'}), 500

    result = {
        'position': current_position,
        'entry_price': entry_price,
        'current_price': current_price,
        'action': 'none'
    }

    # Check if breakout failed
    if current_position == 'LONG' and current_price < entry_price:
        pct_change = ((current_price - entry_price) / entry_price) * 100
        print(f"[CHECK] BREAKOUT FAILED! LONG but price {pct_change:.2f}% below entry")
        print(f"[CHECK] Flipping from LONG to SHORT")
        execute_bear_break(current_price)
        result['action'] = 'flipped to SHORT'
        result['reason'] = f'Price {pct_change:.2f}% below entry'

    elif current_position == 'SHORT' and current_price > entry_price:
        pct_change = ((current_price - entry_price) / entry_price) * 100
        print(f"[CHECK] BREAKOUT FAILED! SHORT but price +{pct_change:.2f}% above entry")
        print(f"[CHECK] Flipping from SHORT to LONG")
        execute_bull_break(current_price)
        result['action'] = 'flipped to LONG'
        result['reason'] = f'Price +{pct_change:.2f}% above entry'

    else:
        if current_position == 'LONG':
            pct_change = ((current_price - entry_price) / entry_price) * 100
            result['status'] = f'Breakout holding, +{pct_change:.2f}% from entry'
        else:
            pct_change = ((entry_price - current_price) / entry_price) * 100
            result['status'] = f'Breakout holding, +{pct_change:.2f}% in profit'

    return jsonify(result)

@app.route('/', methods=['GET'])
def home():
    return '''
    <h1>MXS Webhook Trading Bot</h1>
    <p>Endpoints:</p>
    <ul>
        <li>POST /webhook - Receive TradingView alerts</li>
        <li>GET /status - Check bot status</li>
        <li>POST /close - Close all positions</li>
        <li>GET /check - Check if breakout still valid (for cron)</li>
    </ul>
    <p>Strategy: MXS Bull/Bear Break + Continuations</p>
    <p>Signals: BULL_BREAK, BEAR_BREAK, BULL_CONTINUATION, BEAR_CONTINUATION</p>
    <p>Symbol: FARTCOIN-USDT</p>
    <p>Leverage: 3x</p>
    <p>Stop Loss: 10%</p>
    '''

# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    print("""
    ===================================================================
              MXS WEBHOOK TRADING BOT - BLOFIN DEMO
    ===================================================================
      Strategy: MXS Bull/Bear Break
      Symbol: FARTCOIN-USDT
      Leverage: 3x
      Stop Loss: 10% (30% account loss)
    -------------------------------------------------------------------
      Webhook URL: http://your-server:5000/webhook
      Status URL: http://your-server:5000/status
    ===================================================================
    """)

    # Check if credentials are set
    if API_KEY == 'your-api-key':
        print("\n[WARNING] API credentials not set!")
        print("Set environment variables:")
        print("  BLOFIN_API_KEY")
        print("  BLOFIN_API_SECRET")
        print("  BLOFIN_PASSPHRASE")
        print("\nOr edit the config at the top of this file.\n")

    # Run Flask server (use PORT env var for cloud hosting)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
