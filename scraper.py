import os
import json
import re
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from scrapfly import ScrapflyClient, ScrapeConfig

# --- Secrets from GitHub ---
SAPK = os.getenv('SAPK')
TBK  = os.getenv('TBK')
TCI  = json.loads(os.getenv('TCI', '[]'))
MBL  = os.getenv('MBL')

DB_FILE = "listings_db.json"
scrapfly = ScrapflyClient(key=SAPK)

def p_prc(s):
    if not s: return 0
    # Removes everything except digits (handles € 45.900 -> 45900)
    c = re.sub(r'[^\d]', '', str(s))
    return int(c) if c else 0

def l_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f: return json.load(f)
        except: return {}
    return {}

def s_db(d):
    with open(DB_FILE, 'w') as f: json.dump(d, f, indent=4)

def esc(t):
    # Telegram MarkdownV2 requires escaping special characters
    chars = ['*', '_', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    t = str(t)
    for c in chars: t = t.replace(c, f'\\{c}')
    return t

def s_tel(m):
    for c_id in TCI:
        u = f"https://api.telegram.org/bot{TBK}/sendMessage"
        try:
            requests.post(u, json={"chat_id": c_id, "text": m, "parse_mode": "MarkdownV2"}, timeout=10)
        except Exception as e:
            print(f"DEBUG: Telegram error: {e}")

def run():
    print(f"DEBUG: Starting Scrape at {datetime.now()}")
    
    # Execute scrape using your provided Scrapfly setup
    result = scrapfly.scrape(ScrapeConfig(
        url=MBL,
        tags=["player", "project:default"],
        asp=True,
        render_js=True
    ))

    soup = BeautifulSoup(result.content, 'html.parser')
    
    # mobile.de uses 'article' tags for listings in their current webapp
    listings = soup.find_all('article', {'data-testid': re.compile(r'adListing')})
    
    # Fallback if structure is slightly different
    if not listings:
        listings = soup.select('a[href*="details.html?id="]')

    print(f"DEBUG: Found {len(listings)} potential listings in HTML.")

    db = l_db()
    upd = False
    
    for ad in listings:
        # 1. Get the ID
        vid = ad.get('data-ad-id')
        if not vid:
            # Try to find ID in the link if not in the article tag
            link = ad.find('a', href=True) if ad.name == 'article' else ad
            if link and 'id=' in link['href']:
                vid = link['href'].split('id=')[1].split('&')[0]
        
        if not vid: continue

        # 2. Get the Title
        title_tag = ad.find('h2') or ad.find('div', {'class': re.compile(r'title')})
        ttl = title_tag.get_text(strip=True) if title_tag else "Tesla Listing"

        # 3. Get the Price
        # Look for the span containing the price (usually has 'price' in data-testid)
        price_tag = ad.find('span', {'data-testid': re.compile(r'price')})
        p_str = price_tag.get_text(strip=True) if price_tag else "Price on Request"
        p_val = p_prc(p_str)

        lnk = f"https://www.mobile.de/details.html?id={vid}"

        # 4. Check against Database
        if vid not in db:
            print(f"DEBUG: New entry: {vid} - {ttl}")
            # Format message for Telegram
            msg = f"*New Tesla Found\\!*\n\n{esc(ttl)}\n💰 {esc(p_str)}\n\n[Open Listing]({lnk})"
            s_tel(msg)
            db[vid] = p_val
            upd = True
        else:
            # Price drop check
            old_p = db.get(vid, 0)
            if 0 < p_val < (old_p - 10): # Avoid notifying for 1 EUR changes
                msg = f"*📉 Price Dropped\\!*\n\n{esc(ttl)}\nOld: {old_p} €\nNew: {esc(p_str)}\n\n[Open Listing]({lnk})"
                s_tel(msg)
                db[vid] = p_val
                upd = True

    if upd:
        s_db(db)
        print("DEBUG: Database updated with new listings.")
    else:
        print("DEBUG: No new listings found in this run.")

if __name__ == "__main__":
    run()
