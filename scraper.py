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

# Store data in a JSON file (aligned with YML)
DB_FILE = "listings.json"
scrapfly = ScrapflyClient(key=SAPK)

def parse_price(s):
    if not s: return 0
    # Handles € 45.900 -> 45900
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
    # Escape MarkdownV2 characters for Telegram
    chars = ['*', '_', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    t = str(t)
    for c in chars: t = t.replace(c, f'\\{c}')
    return t

def send_telegram(msg):
    if not TBK or not TCI:
        print("DEBUG: Telegram secrets missing.")
        return
    
    url = f"https://api.telegram.org/bot{TBK}/sendMessage"
    for chat_id in TCI:
        try:
            requests.post(url, json={
                "chat_id": chat_id, 
                "text": msg, 
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": False
            }, timeout=10)
        except Exception as e:
            print(f"DEBUG: Telegram error: {e}")

def run():
    print(f"DEBUG: Starting Scrape at {datetime.now()}")
    
    # Check if this is the very first run (no DB file)
    first_run = not os.path.exists(DB_FILE)
    
    try:
        result = scrapfly.scrape(ScrapeConfig(
            url=MBL,
            tags=["player", "project:default"],
            asp=True,
            render_js=True,
            # Wait slightly for dynamic content
            wait_for_selector="article"
        ))
    except Exception as e:
        print(f"DEBUG: Scrapfly failed: {e}")
        return

    soup = BeautifulSoup(result.content, 'html.parser')
    
    # Find listings (mobile.de usually uses 'article' or specific classes)
    listings = soup.find_all('article')
    
    # Fallback for different layouts
    if not listings:
        listings = soup.select('.c-result-list__item')

    print(f"DEBUG: Found {len(listings)} potential listings.")

    db = load_db()
    updated = False
    
    for ad in listings:
        # 1. Extract ID
        vid = ad.get('data-ad-id')
        if not vid:
            # Try finding ID in anchor link
            link_tag = ad.find('a', href=True)
            if link_tag and 'id=' in link_tag['href']:
                vid = link_tag['href'].split('id=')[1].split('&')[0]
        
        if not vid: continue

        # 2. Extract Price
        price_tag = ad.find('span', {'data-testid': re.compile(r'price')})
        if not price_tag:
            # Fallback text search
            text = ad.get_text()
            price_match = re.search(r'€\s?[\d\.,]+', text)
            price_str = price_match.group(0) if price_match else "Unknown Price"
        else:
            price_str = price_tag.get_text(strip=True)
            
        price_val = parse_price(price_str)

        # 3. Extract Title
        title_tag = ad.find('h2')
        title = title_tag.get_text(strip=True) if title_tag else "Tesla Listing"

        # 4. Correct Link Format (suchen.mobile.de)
        link = f"https://suchen.mobile.de/fahrzeuge/details.html?id={vid}&lang=en"

        # 5. Logic: New vs Existing
        if vid not in db:
            # Add to DB
            db[vid] = price_val
            updated = True
            
            # ONLY send notification if this is NOT the first run
            # This prevents spamming 20 old cars on startup
            if not first_run:
                print(f"DEBUG: New listing found: {vid}")
                msg = (
                    f"*🆕 New Tesla Found\\!*\n\n"
                    f"{escape_md(title)}\n"
                    f"💰 *{escape_md(price_str)}*\n\n"
                    f"[Open Listing]({link})"
                )
                send_telegram(msg)
            else:
                print(f"DEBUG: First run - Silently saving {vid}")

        else:
            # Check for Price Drop
            old_price = db.get(vid, 0)
            # Notify if price dropped by at least 100 EUR
            if 0 < price_val < (old_price - 100):
                print(f"DEBUG: Price drop on {vid}")
                msg = (
                    f"*📉 Price Drop\\!*\n\n"
                    f"{escape_md(title)}\n"
                    f"Old: ~{old_price} €~\n"
                    f"New: *{escape_md(price_str)}*\n\n"
                    f"[Open Listing]({link})"
                )
                send_telegram(msg)
                db[vid] = price_val
                updated = True

    if updated or first_run:
        save_db(db)
        print("DEBUG: Database saved.")
    else:
        print("DEBUG: No changes detected.")

if __name__ == "__main__":
    run()
