import json
import os
import requests
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
SIGNALS_FILE = 'signals.json'
COINDCX_TICKER_URL = 'https://api.coindcx.com/exchange/ticker'

def load_data():
    if not os.path.exists(SIGNALS_FILE):
        return {"portfolio": {"cash_balance": 10000.0, "realized_pnl": 0.0, "open_positions": [], "closed_trades": []}, "active_signals": []}
    with open(SIGNALS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(data):
    with open(SIGNALS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def fetch_coindcx_prices():
    try:
        res = requests.get(COINDCX_TICKER_URL, timeout=10)
        if res.status_code == 200:
            raw = res.json()
            return {item.get('market', ''): float(item.get('last_price', 0)) for item in raw if 'market' in item}
    except Exception as e:
        print(f"Error fetching ticker: {e}")
    return {}

def calculate_time_decay(pub_str, exp_str, now_ist):
    try:
        pub_dt = IST.localize(datetime.strptime(pub_str, "%Y-%m-%d %H:%M"))
        exp_dt = IST.localize(datetime.strptime(exp_str, "%Y-%m-%d %H:%M"))
        total = (exp_dt - pub_dt).total_seconds()
        elapsed = (now_ist - pub_dt).total_seconds()
        if total <= 0: return 100.0, True, True
        decay_pct = max(0.0, min(100.0, (elapsed / total) * 100.0))
        return round(decay_pct, 1), decay_pct >= 50.0, decay_pct >= 70.0
    except Exception:
        return 0.0, False, False

def main():
    now_ist = datetime.now(IST)
    now_str = now_ist.strftime('%Y-%m-%d %H:%M:%S IST')
    data = load_data()
    port = data.setdefault("portfolio", {
        "initial_capital": 10000.0,
        "cash_balance": 10000.0,
        "realized_pnl": 0.0,
        "open_positions": [],
        "closed_trades": []
    })

    prices = fetch_coindcx_prices()
    print(f"[{now_str}] Running 24/7 Cloud Automated Execution Engine...")

    # 1. Update live prices for signals
    for sig in data.get('active_signals', []):
        sym = sig['pair'].upper()
        candidates = [sym + 'USDT', sym + 'INR', sym + 'USDC', 'B-' + sym + '_USDT']
        for c in candidates:
            if c in prices and prices[c] > 0:
                sig['current_price'] = prices[c]
                break

    # 2. Check & Update Active Open Positions (SL, TP, and Time-Stop)
    remaining_open = []
    for pos in port.get('open_positions', []):
        sym = pos['pair'].upper()
        # Find current price
        cur_price = pos['current_price']
        for c in [sym + 'USDT', sym + 'INR', sym + 'USDC', 'B-' + sym + '_USDT']:
            if c in prices and prices[c] > 0:
                cur_price = prices[c]
                break
        pos['current_price'] = cur_price

        is_long = (pos['direction'] == 'BUY')
        pnl_pct = (cur_price - pos['entry_price']) / pos['entry_price'] if is_long else (pos['entry_price'] - cur_price) / pos['entry_price']
        pnl_inr = pos['position_value'] * pnl_pct

        closed = False
        reason = ""

        # Check Stop-Loss
        if (is_long and cur_price <= pos['stop_loss']) or (not is_long and cur_price >= pos['stop_loss']):
            closed = True
            reason = "BREAKEVEN_STOP" if pos.get('breakeven_set') else "STOP_LOSS_HIT"
        # Check Take-Profit
        elif (is_long and cur_price >= pos['target']) or (not is_long and cur_price <= pos['target']):
            closed = True
            reason = "TAKE_PROFIT_HIT"

        # Check Breakeven Auto-Migration (if gain > 1.5% and not already set)
        if not closed and not pos.get('breakeven_set') and pnl_pct > 0.015:
            pos['stop_loss'] = pos['entry_price']
            pos['breakeven_set'] = True
            print(f"Breakeven locked for {pos['pair']} at {pos['entry_price']}")

        if closed:
            port['cash_balance'] += (pos['margin'] + pnl_inr)
            port['realized_pnl'] += pnl_inr
            port['closed_trades'].insert(0, {
                "id": pos['id'],
                "pair": pos['pair'],
                "direction": pos['direction'],
                "leverage": pos['leverage'],
                "entry_price": pos['entry_price'],
                "exit_price": cur_price,
                "margin_inr": round(pos['margin'], 2),
                "position_value": round(pos['position_value'], 2),
                "pnl_inr": round(pnl_inr, 2),
                "roi_pct": round(pnl_pct * 100 * pos['leverage'], 2),
                "result": reason,
                "opened_at": pos['opened_at'],
                "closed_at": now_str
            })
            print(f"Closed trade {pos['pair']}: {reason} | PnL: ₹{pnl_inr:.2f}")
        else:
            remaining_open.append(pos)

    port['open_positions'] = remaining_open

    # 3. Check for NEW Automated Entries (Only if cash is available and position not already open)
    open_pairs = {p['pair'] for p in port['open_positions']}

    for sig in data.get('active_signals', []):
        pair = sig['pair']
        if pair in open_pairs:
            continue  # Don't duplicate open positions

        price = sig['current_price']
        in_band = (sig['entry_low'] <= price <= sig['entry_high'])
        _, is_decay_invalid, _ = calculate_time_decay(sig['published_ist'], sig['expiry_ist'], now_ist)

        # Execution Condition: Must be inside entry band and not expired past 50%
        if in_band and not is_decay_invalid:
            # Position Sizing: Risk 1.5% of cash balance
            risk_pct = 0.015
            max_loss = port['cash_balance'] * risk_pct
            sl_dist = abs(price - sig['stop_loss'])
            
            if sl_dist > 0 and port['cash_balance'] > 500:
                sl_pct = sl_dist / price
                pos_val = max_loss / sl_pct
                margin_req = pos_val / (sig.get('leverage') or 10)

                # Ensure margin doesn't exceed 30% of available cash
                if margin_req <= port['cash_balance'] * 0.35:
                    port['cash_balance'] -= margin_req
                    port['open_positions'].append({
                        "id": f"AUTO-{pair}-{int(now_ist.timestamp())}",
                        "pair": pair,
                        "direction": sig['direction'],
                        "leverage": sig.get('leverage', 10),
                        "entry_price": price,
                        "current_price": price,
                        "stop_loss": sig['stop_loss'],
                        "target": sig['target'],
                        "margin": round(margin_req, 2),
                        "position_value": round(pos_val, 2),
                        "opened_at": now_str,
                        "breakeven_set": False
                    })
                    open_pairs.add(pair)
                    print(f"AUTOMATED ORDER EXECUTED: {pair} {sig['direction']} at {price} | Margin: ₹{margin_req:.2f}")

    data['last_updated_ist'] = now_str
    save_data(data)
    print(f"Cycle completed. Cash: ₹{port['cash_balance']:.2f} | Open: {len(port['open_positions'])} | Closed: {len(port['closed_trades'])}")

if __name__ == '__main__':
    main()
