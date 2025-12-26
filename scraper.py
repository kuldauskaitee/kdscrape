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
    
    if not listings:
        listings = soup.select('.c-result-list__item')

    print(f"DEBUG: Found {len(listings)} potential listings in HTML.")

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
            text = ad.get_text()
            price_match = re.search(r'€\s?[\d\.,]+', text)
            price_str = price_match.group(0) if price_match else "Unknown"
        else:
            price_str = price_tag.get_text(strip=True)
            
        price_val = parse_price(price_str)

        # 3. Extract Title
        title_tag = ad.find('h2')
        title = title_tag.get_text(strip=True) if title_tag else "Tesla Listing"
        
        # 4. Extract Year
        meta_text = ""
        meta_divs = ad.find_all('div', {'class': re.compile(r'subtitle|vehicle-data')})
        for m in meta_divs: meta_text += " " + m.get_text(strip=True)
        year_match = re.search(r'20[1-3][0-9]', meta_text)
        year_str = year_match.group(0) if year_match else "N/A"

        link = f"https://suchen.mobile.de/fahrzeuge/details.html?id={vid}&lang=en"

        # --- LOGIC HANDLING ---

        # If New Car
        if vid not in db:
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

        # If Existing Car (Check Price)
        else:
            stored_data = db[vid]
            # Handle format migration (int -> dict)
            if isinstance(stored_data, int):
                old_price = stored_data
                orig_date = now_str
                db[vid] = {"price": price_val, "found_at": now_str} # Upgrade DB format
                updated = True
            else:
                old_price = stored_data.get("price", 0)
                orig_date = stored_data.get("found_at", now_str)

            # --- DEBUG LOGGING FOR YOU ---
            # This will show you exactly what the bot sees
            if old_price != price_val:
                print(f"DEBUG: Price diff for {vid} | Old: {old_price} | New: {price_val}")

            # PRICE DROP CHECK
            # Must be valid prices, and drop > 50 EUR
            if old_price > 0 and price_val > 0 and price_val < (old_price - 50):
                print(f"ACTION: Sending Alert for {vid}")
                msg = (
                    f"*📉 Price Drop\\!*\n\n"
                    f"{escape_md(title)}\n"
                    f"Old: ~{old_price} €~\n"
                    f"New: *{escape_md(price_str)}*\n"
                    f"📅 Found: {escape_md(orig_date)}\n\n"
                    f"[Open Listing]({link})"
                )
                send_telegram(msg)
                db[vid]["price"] = price_val
                updated = True

            # PRICE INCREASE CHECK (Update DB but don't spam Telegram)
            elif price_val > (old_price + 50):
                print(f"DEBUG: Price increased for {vid}. Updating DB.")
                db[vid]["price"] = price_val
                updated = True

    if updated or first_run:
        save_db(db)
        print("DEBUG: Database saved.")
    else:
        print("DEBUG: No changes detected.")

if __name__ == "__main__":
    run()
