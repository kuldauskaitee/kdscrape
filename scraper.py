import os
import json
import re
import requests
from datetime import datetime, timedelta
from scrapfly import ScrapflyClient as SC, ScrapeConfig as SG, ScrapeApiResponse as SA

SAPK = os.getenv('SAPK')
TBK  = os.getenv('TBK')
TCI  = json.loads(os.getenv('TCI', '[]'))
MBL  = os.getenv('MBL')

DB_FILE = "listings_db.json"
cl = SC(key=SAPK)

def p_prc(s):
    if not s: return 0
    try:
        c = re.sub(r'[^\d]', '', str(s))
        return int(c) if c else 0
    except: return 0

def l_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f: return json.load(f)
        except: return {}
    return {}

def s_db(d):
    with open(DB_FILE, 'w') as f: json.dump(d, f, indent=4)

def esc(t):
    chars = ['*', '_', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    t = str(t)
    for c in chars: t = t.replace(c, f'\\{c}')
    return t

def s_tel(m):
    print(f"DEBUG: Attempting to send Telegram message to {len(TCI)} IDs")
    for c_id in TCI:
        u = f"https://api.telegram.org/bot{TBK}/sendMessage"
        try: 
            r = requests.post(u, json={"chat_id": c_id, "text": m, "parse_mode": "MarkdownV2"}, timeout=10)
            print(f"DEBUG: Telegram response: {r.status_code}")
        except Exception as e: 
            print(f"DEBUG: Telegram Error: {e}")

def run():
    print(f"DEBUG: Starting Scrape at {datetime.now()}")
    if not MBL:
        print("DEBUG: ERROR - MBL URL is empty! Check your Secrets.")
        return

    try:
        res: SA = cl.scrape(SG(
            url=MBL,
            tags=["player", "project:default"],
            asp=True,
            render_js=True,
            country="de"
        ))
        print(f"DEBUG: Scrapfly Success. Status: {res.status_code}")
    except Exception as e:
        print(f"DEBUG: Scrapfly Error: {e}")
        return
    
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});', res.content)
    if not match:
        print("DEBUG: Could not find car data on the page. Mobile.de might have changed their layout.")
        return

    data = json.loads(match.group(1))
    # Trying different data paths for Mobile.de
    items = (data.get('search', {}).get('srp', {}).get('data', {}).get('searchResults', {}).get('items', []) or 
             data.get('search', {}).get('srp', {}).get('searchResults', {}).get('items', []))
    
    print(f"DEBUG: Found {len(items)} total items on page.")

    db = l_db()
    upd = False
    
    for i in items:
        vid = str(i.get('id', ''))
        if not vid: continue
        
        ttl = i.get('title', 'Unknown')
        p_d = i.get('price', {}).get('gross', 'N/A')
        p_v = p_prc(p_d)
        
        r_u = i.get('relativeUrl', '')
        lnk = f"https://suchen.mobile.de{r_u}"

        if vid not in db:
            print(f"DEBUG: Found New Car: {ttl}")
            msg = f"*New Listing\\!*\n\n*{esc(ttl)}*\nPrice: {esc(str(p_d))}\n[Link]({lnk})"
            s_tel(msg)
            db[vid] = p_v
            upd = True
            
    if upd: 
        print("DEBUG: Saving new data to listings_db.json")
        s_db(db)
    else:
        print("DEBUG: No new cars found to save.")

if __name__ == "__main__":
    run()
