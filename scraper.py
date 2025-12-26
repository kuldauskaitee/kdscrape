import os
import json
import re
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from scrapfly import ScrapflyClient, ScrapeConfig

# --- Secrets ---
SAPK = os.getenv('SAPK')
TBK  = os.getenv('TBK')
TCI  = json.loads(os.getenv('TCI', '[]'))
MBL  = os.getenv('MBL')

DB_FILE = "listings.json"
scrapfly = ScrapflyClient(key=SAPK)

def parse_price(s):
    if not s: return 0
    # Clean string: "€ 45.900" -> 45900
    c = re.sub(r'[^\d]', '', str(s))
    return int(c) if c else 0

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f: return json.load(f)
        except: return {}
    return {}

def save_db(d):
    with open(DB_FILE, 'w') as f: json.dump(d, f, indent=4)

def escape_md(t):
    chars = ['*', '_', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    t = str(t)
    for c in chars: t = t.replace(c, f'\\{c}')
    return t

def send_telegram(msg):
    if not TBK or not TCI: return
    url = f"https://api.telegram.org/bot{TBK}/sendMessage"
    for chat_id in TCI:
        try:
            requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "MarkdownV2", "disable_web_page_preview": False})
        except Exception as e:
            print(f"DEBUG: Telegram error: {e}")

def run():
    now_str = datetime.now().strftime("%Y-%m-%d")
    print(f"DEBUG: Starting Scrape at {datetime.now()}")
    
    first_run = not os.path.exists(DB_FILE)
    
    try:
        # Standard scrape (No country lock, as requested)
        result = scrapfly.scrape(ScrapeConfig(
            url=MBL,
            tags=["player", "project:default"],
            asp=True,
            render_js=True
        ))
    except Exception as e:
        print(f"DEBUG: Scrapfly failed: {e}")
        return

    soup = BeautifulSoup(result.content, 'html.parser')
    
    # --- SELECTOR STRATEGY (Restored from your working version) ---
    # 1. Primary: Mobile.de standard ad listing tag
    listings = soup.find_all('article', {'data-testid': re.compile(r'adListing')})
    
    # 2. Fallback: Any link with a listing ID
    if not listings:
        listings = soup.select('a[href*="details.html?id="]')

    print(f"DEBUG: Found {len(listings)} potential listings.")

    db = load_db()
    updated = False
    
    for ad in listings:
        # --- DATA EXTRACTION ---
        vid = ad.get('data-ad-id')
        if not vid:
            # Try to fish ID out of the link
            link_elem = ad.find('a', href=True) if ad.name == 'article' else ad
            if link_elem and 'id=' in link_elem.get('href', ''):
                vid = link_elem['href'].split('id=')[1].split('&')[0]
        
        if not vid: continue

        # Price
        price_tag = ad.find('span', {'data-testid': re.compile(r'price')})
        if price_tag:
            price_str = price_tag.get_text(strip=True)
        else:
            p_match = re.search(r'€\s?[\d\.,]+', ad.get_text())
            price_str = p_match.group(0) if p_match else "0"
        
        price_val = parse_price(price_str)

        # Title
        t_tag = ad.find('h2') or ad.find('div', {'class': re.compile(r'title')})
        title = t_tag.get_text(strip=True) if t_tag else "Tesla Listing"
        
        # Year
        y_match = re.search(r'20[1-3][0-9]', ad.get_text())
        year_str = y_match.group(0) if y_match else "N/A"

        link = f"https://suchen.mobile.de/fahrzeuge/details.html?id={vid}&lang=en"

        # --- LOGIC ---
        
        # NEW CAR
        if vid not in db:
            db[vid] = {"price": price_val, "found_at": now_str, "title": title}
            updated = True
            
            # Send alert only if it's not the initial database build
            if not first_run:
                print(f"DEBUG: New Car {vid}")
                msg = f"*🆕 New Tesla Found\\!*\n\n{escape_md(title)}\n🗓 Year: {escape_md(year_str)}\n💰 *{escape_md(price_str)}*\n📅 Found: {escape_md(now_str)}\n\n[Open Listing]({link})"
                send_telegram(msg)

        # EXISTING CAR (The part that was failing)
        else:
            stored_data = db[vid]
            
            # 1. Normalize Old Price
            if isinstance(stored_data, int):
                old_p = stored_data
                orig_d = now_str
            else:
                old_p = stored_data.get("price", 0)
                orig_d = stored_data.get("found_at", now_str)

            # 2. DEBUG PRINT (This will prove if it's checking)
            if old_p != price_val:
                print(f"CHECK: {vid} | DB: {old_p} -> Web: {price_val}")

            # 3. Price Drop Logic
            #    Triggers if current price is lower than DB price by > 50
            if old_p > 0 and price_val > 0 and price_val < (old_p - 50):
                print(f"ACTION: Sending Drop Alert for {vid}")
                msg = f"*📉 Price Drop\\!*\n\n{escape_md(title)}\nOld: ~{old_p} €~\nNew: *{escape_md(price_str)}*\n📅 Found: {escape_md(orig_d)}\n\n[Open Listing]({link})"
                send_telegram(msg)
                
                # Update DB
                if isinstance(db[vid], dict):
                    db[vid]["price"] = price_val
                else:
                    db[vid] = {"price": price_val, "found_at": now_str}
                updated = True

            # 4. Price Increase Logic (Update DB silently)
            elif price_val > (old_p + 50):
                print(f"DEBUG: Price increased {vid}. Updating DB.")
                if isinstance(db[vid], dict):
                    db[vid]["price"] = price_val
                else:
                    db[vid] = {"price": price_val, "found_at": now_str}
                updated = True

    if updated or first_run:
        save_db(db)
        print("DEBUG: Database saved.")
    else:
        print("DEBUG: No changes detected.")

if __name__ == "__main__":
    run()
