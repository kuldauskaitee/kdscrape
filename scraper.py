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
    # Remove all non-digits (handle € 45.900 -> 45900)
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
    # Escape MarkdownV2 special characters
    chars = ['*', '_', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    t = str(t)
    for c in chars: t = t.replace(c, f'\\{c}')
    return t

def send_telegram(msg):
    if not TBK or not TCI: return
    
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
    now_str = datetime.now().strftime("%Y-%m-%d")
    print(f"DEBUG: Starting Scrape at {datetime.now()}")
    
    # Check if this is the first run to avoid spamming
    first_run = not os.path.exists(DB_FILE)
    
    try:
        result = scrapfly.scrape(ScrapeConfig(
            url=MBL,
            tags=["player", "project:default"],
            asp=True,
            render_js=True,
            wait_for_selector="article"
        ))
    except Exception as e:
        print(f"DEBUG: Scrapfly failed: {e}")
        return

    soup = BeautifulSoup(result.content, 'html.parser')
    listings = soup.find_all('article')
    
    # Fallback
    if not listings:
        listings = soup.select('.c-result-list__item')

    print(f"DEBUG: Found {len(listings)} potential listings.")

    db = load_db()
    updated = False
    
    for ad in listings:
        # 1. Extract ID
        vid = ad.get('data-ad-id')
        if not vid:
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
        
        # 4. Extract "First Registration" (Year) if possible
        # usually in a div like "2022 • 30.000 km"
        meta_text = ""
        meta_divs = ad.find_all('div', {'class': re.compile(r'subtitle|vehicle-data')})
        for m in meta_divs:
            meta_text += " " + m.get_text(strip=True)
        
        # Try to find a year (e.g. 2020, 2021) in text
        year_match = re.search(r'20[1-3][0-9]', meta_text)
        year_str = year_match.group(0) if year_match else "N/A"

        link = f"https://suchen.mobile.de/fahrzeuge/details.html?id={vid}&lang=en"

        # --- LOGIC HANDLING ---

        # Case A: New Car (ID not in DB)
        if vid not in db:
            # Create new record
            db[vid] = {
                "price": price_val,
                "found_at": now_str,
                "title": title
            }
            updated = True
            
            if not first_run:
                print(f"DEBUG: New listing {vid}")
                msg = (
                    f"*🆕 New Tesla Found\\!*\n\n"
                    f"{escape_md(title)}\n"
                    f"🗓 Year: {escape_md(year_str)}\n"
                    f"💰 *{escape_md(price_str)}*\n"
                    f"📅 Found: {escape_md(now_str)}\n\n"
                    f"[Open Listing]({link})"
                )
                send_telegram(msg)

        # Case B: Car exists, check Price Drop
        else:
            # Handle legacy DB format (if old json exists with just integers)
            stored_data = db[vid]
            if isinstance(stored_data, int):
                old_price = stored_data
                # Convert to new format
                db[vid] = {"price": price_val, "found_at": now_str}
            else:
                old_price = stored_data.get("price", 0)

            # Check drop (Must be at least 50 EUR difference to trigger)
            if old_price > 0 and price_val < (old_price - 50):
                print(f"DEBUG: Price drop on {vid}: {old_price} -> {price_val}")
                
                # Get the original found date if available
                orig_date = stored_data.get("found_at", now_str) if isinstance(stored_data, dict) else now_str
                
                msg = (
                    f"*📉 Price Drop\\!*\n\n"
                    f"{escape_md(title)}\n"
                    f"Old: ~{old_price} €~\n"
                    f"New: *{escape_md(price_str)}*\n"
                    f"📅 Found: {escape_md(orig_date)}\n\n"
                    f"[Open Listing]({link})"
                )
                send_telegram(msg)
                
                # Update DB with new price
                db[vid]["price"] = price_val
                updated = True
            
            # If price increased or stayed same, do nothing (but ensure DB format is current)
            if isinstance(stored_data, int):
                updated = True # We updated the format

    if updated or first_run:
        save_db(db)
        print("DEBUG: Database saved.")
    else:
        print("DEBUG: No changes detected.")

if __name__ == "__main__":
    run()
