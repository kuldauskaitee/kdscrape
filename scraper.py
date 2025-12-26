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
    for c_id in TCI:
        u = f"https://api.telegram.org/bot{TBK}/sendMessage"
        try: requests.post(u, json={"chat_id": c_id, "text": m, "parse_mode": "MarkdownV2"}, timeout=10)
        except: pass

def is_r(d_s):
    if not d_s: return False
    try:
        c_d = d_s.split(',')[0].strip()
        l_d = datetime.strptime(c_d, "%d.%m.%Y").date()
        y = datetime.now().date() - timedelta(days=1)
        return l_d >= y
    except: return False

def run():
    res: SA = cl.scrape(SG(
        url=MBL,
        tags=["player", "project:default"],
        asp=True,
        render_js=True,
        country="de"
    ))
    
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});', res.content)
    if not match: return

    data = json.loads(match.group(1))
    items = data.get('search', {}).get('srp', {}).get('data', {}).get('searchResults', {}).get('items', [])
    if not items:
        items = data.get('search', {}).get('srp', {}).get('searchResults', {}).get('items', [])

    db = l_db()
    upd = False
    
    for i in items:
        vid = str(i.get('id', ''))
        if not vid: continue
        
        ttl = i.get('title', 'Unknown')
        p_d = i.get('price', {}).get('gross', 'N/A')
        p_v = p_prc(p_d)
        
        a = i.get('attr', {})
        reg = a.get('fr', 'N/A')
        ml = a.get('ml', 'N/A')
        ons = i.get('onlineSince', '')
        r_u = i.get('relativeUrl', '')
        lnk = f"https://suchen.mobile.de{r_u}"

        if vid not in db:
            if is_r(ons):
                msg = (f"*New Listing\\!*\n\n*{esc(ttl)}*\n"
                       f"Price: {esc(str(p_d))}\n"
                       f"Reg: {esc(str(reg))} | {esc(str(ml))}\n"
                       f"[Link]({lnk})")
                s_tel(msg)
            db[vid] = p_v
            upd = True
        else:
            old = db.get(vid, 0)
            if 0 < p_v < old:
                diff = old - p_v
                msg = (f"*📉 Price Drop\\!*\n\n*{esc(ttl)}*\n"
                       f"Old: € {old:,.0f}\nNew: {esc(str(p_d))}\n"
                       f"Saved: € {diff:,.0f}\n[Link]({lnk})")
                s_tel(msg)
                db[vid] = p_v
                upd = True

    if upd: s_db(db)

if __name__ == "__main__":
    run()
