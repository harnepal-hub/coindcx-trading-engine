import json, os, requests
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
URL = 'https://api.coindcx.com/exchange/ticker'

def main():
    if not os.path.exists('signals.json'): return
    with open('signals.json') as f: data = json.load(f)
    try:
        r = requests.get(URL, timeout=10).json()
        prices = {x['market']: float(x.get('last_price', 0)) for x in r if 'market' in x}
    except: prices = {}
    now = datetime.now(IST)
    for s in data.get('active_signals', []):
        if s['pair'] in prices and prices[s['pair']] > 0:
            s['current_price'] = prices[s['pair']]
    data['last_updated_ist'] = now.strftime('%Y-%m-%d %H:%M:%S IST')
    with open('signals.json', 'w') as f: json.dump(data, f, indent=2)

if __name__ == '__main__': main()
