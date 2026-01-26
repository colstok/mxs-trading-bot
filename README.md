# MXS Trading Bot - FARTCOIN 1M/5M Strategy

## Live Bot
- **URL**: https://mxs-trading-bot.onrender.com
- **Webhook**: https://mxs-trading-bot.onrender.com/webhook
- **GitHub**: https://github.com/colstok/mxs-trading-bot.git

## Current Strategy: 1M/5M Multi-Timeframe

### Timeframes
- **5M (Higher TF)**: Sets trend direction, triggers exits on trend flip
- **1M (Lower TF)**: Entry signals only (breaks + continuations)

### Entry Rules
- Only enter when 1M signal aligns with 5M trend
- 1M_BULL_BREAK or 1M_BULL_CONTINUATION → LONG (if 5M trend is BULL)
- 1M_BEAR_BREAK or 1M_BEAR_CONTINUATION → SHORT (if 5M trend is BEAR)

### Exit Rules
- 5M_BULL_BREAK → Close any SHORT position
- 5M_BEAR_BREAK → Close any LONG position
- Stop loss: 2% beyond 5M swing level

## Current Settings
- **Symbol**: FARTCOIN-USDT
- **Exchange**: BloFin
- **Leverage**: 5x
- **Margin Mode**: Isolated
- **Position Size**: 95% of balance
- **Stop Buffer**: 2% beyond HTF swing

## Backtest Results (12.7 days)

| Strategy | Return | Trades | Win% | Max DD |
|----------|--------|--------|------|--------|
| 1M/5M | +3,905% | 224 | 50.0% | 21.8% |
| 1M/10M | +1,594% | 94 | 54.3% | 25.3% |

**1M/5M chosen for**: Higher returns, lower drawdown, faster trend detection

## TradingView Alert Setup

### Webhook URL (same for all alerts)
```
https://mxs-trading-bot.onrender.com/webhook
```

### 5M Chart Alerts (HTF - Trend & Exits)

**5M Bull Break:**
```json
{"signal": "5M_BULL_BREAK", "price": {{close}}, "swing_low": {{plot_2}}, "swing_high": {{plot_7}}}
```

**5M Bear Break:**
```json
{"signal": "5M_BEAR_BREAK", "price": {{close}}, "swing_low": {{plot_2}}, "swing_high": {{plot_7}}}
```

### 1M Chart Alerts (LTF - Entries)

**1M Bull Break:**
```json
{"signal": "1M_BULL_BREAK", "price": {{close}}, "swing_low": {{plot_2}}, "swing_high": {{plot_7}}}
```

**1M Bear Break:**
```json
{"signal": "1M_BEAR_BREAK", "price": {{close}}, "swing_low": {{plot_2}}, "swing_high": {{plot_7}}}
```

**1M Bull Continuation:**
```json
{"signal": "1M_BULL_CONTINUATION", "price": {{close}}, "swing_low": {{plot_2}}, "swing_high": {{plot_7}}}
```

**1M Bear Continuation:**
```json
{"signal": "1M_BEAR_CONTINUATION", "price": {{close}}, "swing_low": {{plot_2}}, "swing_high": {{plot_7}}}
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | Bot status + Blofin position |
| `/positions` | GET | Raw Blofin positions |
| `/orders` | GET | Pending orders & TP/SL |
| `/set_trend` | POST | Manually set trend (BULL/BEAR/null) |
| `/set_swings` | POST | Manually set HTF swing levels |
| `/close` | POST | Close current position |
| `/webhook` | POST | Receive TradingView alerts |

### Manual Trend Control
```bash
# Set trend to BULL
curl -X POST https://mxs-trading-bot.onrender.com/set_trend -H "Content-Type: application/json" -d '{"trend": "BULL"}'

# Set trend to BEAR
curl -X POST https://mxs-trading-bot.onrender.com/set_trend -H "Content-Type: application/json" -d '{"trend": "BEAR"}'

# Disable bot (null trend)
curl -X POST https://mxs-trading-bot.onrender.com/set_trend -H "Content-Type: application/json" -d '{"trend": ""}'

# Set swing levels
curl -X POST https://mxs-trading-bot.onrender.com/set_swings -H "Content-Type: application/json" -d '{"htf_swing_low": 0.2997, "htf_swing_high": 0.3050}'
```

## How It Works

1. **5M signal fires** → Sets trend to BULL or BEAR, stores swing levels, closes opposite position if exists
2. **1M signal fires** → If aligned with 5M trend and not already in position, enters trade with stop 2% beyond 5M swing
3. **Bot checks Blofin directly** → Survives Render restarts, always uses actual position state

## Risk Notes

- **Max losing streak in backtest**: 6 trades
- **Drawdowns happen**: 21.8% max DD means expect to be down ~20% at some point
- **50% win rate**: Half your trades lose, winners must be bigger than losers
- **Leverage amplifies everything**: 5x means a 5% move = 25% account change

## File Locations
```
C:\Users\Taylor\Desktop\mxs-bot-deploy\
├── mxs_webhook_bot.py      - Bot code (deployed)
├── requirements.txt
└── README.md               - This file

C:\Users\Taylor\Desktop\back test\
├── MXS Degen Bot.bat       - Shortcut to start Claude Code
├── prove_it_README.md      - Detailed 5M/30M backtest (198 trades)
└── Various backtest scripts
```

## Quick Commands

Check status:
```bash
curl https://mxs-trading-bot.onrender.com/status
```

Stop the bot:
```bash
curl -X POST https://mxs-trading-bot.onrender.com/set_trend -H "Content-Type: application/json" -d '{"trend": ""}'
```
