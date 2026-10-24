import json
import os
import requests
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
SIGNALS_FILE = 'signals.json'

def load_data():
    if not os.path.exists(SIGNALS_FILE):
        return {"portfolio": {"cash_balance": 10000.0, "realized_pnl": 0.0, "open_positions": [], "closed_trades": []}, "active_signals": []}
    with open(SIGNALS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(data):
    with open(SIGNALS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def fetch_all_live_prices(signals):
    price_map = {}
    
    # 1. Fetch CoinDCX Crypto Prices
    try:
        r = requests.get('https://api.coindcx.com/exchange/ticker', timeout=8)
        if r.status_code == 200:
            for item in r.json():
                m = item.get('market', '')
                price_map[m] = float(item.get('last_price', 0))
    except Exception as e:
        print(f"CoinDCX crypto fetch error: {e}")

    # 2. Fetch Global Equities (MSFT, AAPL, META, ORCL) via Yahoo Finance Public API
    stock_syms = [s['pair'].upper() for s in signals if s.get('category') == 'Global']
    if stock_syms:
        try:
            url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={','.join(stock_syms)}"
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 200:
                results = res.json().get('quoteResponse', {}).get('result', [])
                for item in results:
                    sym = item.get('symbol')
                    price = item.get('regularMarketPrice') or item.get('postMarketPrice') or item.get('preMarketPrice')
                    if sym and price:
                        price_map[sym] = float(price)
        except Exception as e:
            print(f"Global stocks fetch error: {e}")

    return price_map

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

    signals = data.get('active_signals', [])
    prices = fetch_all_live_prices(signals)
    print(f"[{now_str}] Polled Market Prices: {prices}")

    # 1. Update prices on all signals
    for sig in signals:
        sym = sig['pair'].upper()
        # Direct stock match or crypto match
        if sym in prices and prices[sym] > 0:
            sig['current_price'] = prices[sym]
        else:
            for c in [sym + 'USDT', sym + 'INR', sym + 'USDC', 'B-' + sym + '_USDT']:
                if c in prices and prices[c] > 0:
                    sig['current_price'] = prices[c]
                    break

    # 2. Check & Update Existing Positions (SL / TP / Breakeven)
    remaining_open = []
    for pos in port.get('open_positions', []):
        sym = pos['pair'].upper()
        cur_price = pos['current_price']
        if sym in prices and prices[sym] > 0:
            cur_price = prices[sym]
        else:
            for c in [sym + 'USDT', sym + 'INR']:
                if c in prices and prices[c] > 0:
                    cur_price = prices[c]
                    break
        pos['current_price'] = cur_price

        is_long = (pos['direction'] == 'BUY')
        pnl_pct = (cur_price - pos['entry_price']) / pos['entry_price'] if is_long else (pos['entry_price'] - cur_price) / pos['entry_price']
        pnl_inr = pos['position_value'] * pnl_pct

        closed = False
        reason = ""

        # Trigger Stop Loss
        if (is_long and cur_price <= pos['stop_loss']) or (not is_long and cur_price >= pos['stop_loss']):
            closed = True
            reason = "BREAKEVEN_STOP" if pos.get('breakeven_set') else "STOP_LOSS_HIT"
        # Trigger Take Profit
        elif (is_long and cur_price >= pos['target']) or (not is_long and cur_price <= pos['target']):
            closed = True
            reason = "TAKE_PROFIT_HIT"

        # Auto-Breakeven lock if profit > 1.5%
        if not closed and not pos.get('breakeven_set') and pnl_pct > 0.015:
            pos['stop_loss'] = pos['entry_price']
            pos['breakeven_set'] = True

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
            print(f"CLOSED: {pos['pair']} via {reason} with PnL: ₹{pnl_inr:.2f}")
        else:
            remaining_open.append(pos)

    port['open_positions'] = remaining_open

    # 3. Automatic Entries (Checks Entry Range & 50% Time Decay)
    open_pairs = {p['pair'] for p in port['open_positions']}

    for sig in signals:
        pair = sig['pair']
        if pair in open_pairs:
            continue

        price = sig['current_price']
        in_band = (sig['entry_low'] <= price <= sig['entry_high'])
        decay_pct, is_decay_invalid, _ = calculate_time_decay(sig['published_ist'], sig['expiry_ist'], now_ist)
        sig['decay_pct'] = decay_pct

        # Execute only if price is in band AND less than 50% of expiry time has passed
        if in_band and not is_decay_invalid and port['cash_balance'] > 500:
            max_loss = port['cash_balance'] * 0.015 # 1.5% fixed risk
            sl_dist = abs(price - sig['stop_loss'])
            
            if sl_dist > 0:
                pos_val = max_loss / (sl_dist / price)
                margin_req = pos_val / (sig.get('leverage') or 10)

                # Cap single trade margin to 35% of cash
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
                    print(f"AUTO-ENTRY EXECUTED: {pair} at {price} (Margin: ₹{margin_req:.2f})")

    data['last_updated_ist'] = now_str
    save_data(data)

if __name__ == '__main__':
    main()