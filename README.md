# MXS Trading Bot - FARTCOIN

## Live Bot
- **URL**: https://mxs-trading-bot.onrender.com
- **Webhook**: https://mxs-trading-bot.onrender.com/webhook
- **GitHub**: https://github.com/colstok/mxs-trading-bot.git

## Current Settings
- Symbol: FARTCOIN-USDT
- Exchange: BloFin DEMO Trading
- Leverage: 3x
- Stop Loss: 10% (= 30% account loss per stop)
- Position Size: 90% of balance
- Margin Mode: Cross

## Signal Logic

### Breaks (Immediate Entry)
- **BULL_BREAK**: Enter LONG immediately, flip if SHORT
- **BEAR_BREAK**: Enter SHORT immediately, flip if LONG

### Continuations (Candle Close, Flip Only)
- **BULL_CONTINUATION**: Only flip SHORT to LONG (skip if LONG or flat)
- **BEAR_CONTINUATION**: Only flip LONG to SHORT (skip if SHORT or flat)
- Continuations are "wrong side" warnings, NOT entry signals
- TradingView alerts set to fire on bar close for continuations

## Backtest Results (4H FARTCOIN, ~13 months)

### Break Signals
| Metric | Value |
|--------|-------|
| Total Trades | 159 |
| Win Rate | **55%** |
| Stops Hit | 18 |
| $2,000 → | $155M+ |

### Continuation Signals (Standalone - DON'T USE)
| Metric | Value |
|--------|-------|
| Total Signals | 185 |
| Win Rate | **36%** (loses money) |
| Stops Hit | 67 |

### Key Finding
- Breaks: 55% win rate, profitable
- Continuations alone: 36% win rate, account destroyer
- Continuations only useful as flip signals when on wrong side

## Entry Method Analysis

### Midpoint vs Candle Close
| Entry Method | Win Rate | Result |
|--------------|----------|--------|
| Midpoint (immediate) | 55% | Best returns |
| Candle Close (confirmed) | 33% | Blows account |

**Conclusion**: Enter immediately on breaks. The 55% win rate already accounts for false signals.

## Big Moves Analysis
- 16 LONG trades with 15%+ gains over 13 months
- ~1-2 big moves per month
- Rest is chop - stops protect capital

## Risk Considerations

### Current Risk (3x leverage, 90% position, 10% stop)
- Each stop = 27% account loss
- 2 consecutive stops = 50%+ drawdown
- Max drawdown in backtest: 93%

### Risk Reduction Options
1. Lower leverage: 2x or 1x
2. Smaller position: 50% instead of 90%
3. Both: 1x leverage + 50% position = ~5% loss per stop

## TradingView Alert Setup

### Webhook URL
```
https://mxs-trading-bot.onrender.com/webhook
```

### Break Alerts (Immediate)
```json
{"signal": "BULL_BREAK", "price": {{close}}}
{"signal": "BEAR_BREAK", "price": {{close}}}
```

### Continuation Alerts (On Bar Close)
```json
{"signal": "BULL_CONTINUATION", "price": {{close}}}
{"signal": "BEAR_CONTINUATION", "price": {{close}}}
```
Set trigger to "Once Per Bar Close" for continuations.

## Lessons Learned (Jan 21, 2026)

1. **Immediate entry beats waiting for confirmation** - 55% vs 33% win rate
2. **10% stop is better than 15%** - 62x better returns due to capital preservation
3. **Continuations are NOT entry signals** - 36% win rate standalone
4. **Continuations useful for flipping** - "you're on wrong side" warning
5. **False signals happen** - 2 in first 12 hours of live trading
6. **Let the stops do their job** - that's what they're for
7. **55% win rate is good** - pros run 50-60%, your winners run bigger than losers

## File Locations
```
C:\Users\Taylor\Desktop\back test\
├── mxs_webhook_bot.py      - Bot code (local copy)
├── BOT_MEMORY.txt          - Bot reference
├── SESSION_BACKUP.txt      - Session backup
├── hybrid_backtest.py      - Backtest scripts
├── continuation_analysis.py
├── all_continuations_analysis.py
└── read this one.csv       - Full data export (in Downloads)

C:\Users\Taylor\Desktop\mxs-bot-deploy\
├── mxs_webhook_bot.py      - Deployed bot code
├── requirements.txt
└── README.md               - This file
```

## Quick Resume Prompt
```
Read C:\Users\Taylor\Desktop\mxs-bot-deploy\README.md to resume our MXS trading bot session.
```
