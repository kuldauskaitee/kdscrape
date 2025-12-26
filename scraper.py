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
            wait_for_selector="body" # Wait for body to ensure load
        ))
    except Exception as e:
        print(f"DEBUG: Scrapfly failed: {e}")
        return

    soup = BeautifulSoup(result.content, 'html.parser')
    
    # DEBUG: Print page title to ensure we aren't blocked or on 404
    print(f"DEBUG: Page Title: {soup.title.string if soup.title else 'No Title'}")

    # --- AGGRESSIVE SELECTOR STRATEGY ---
    # 1. Look for explicit ad articles
    listings = soup.find_all('article')
    
    # 2. If 0, look for ANY link that goes to a car detail page
    if not listings:
        print("DEBUG: 'article' tag not found. Trying Link Strategy...")
        # Find all <a> tags that have 'details.html?id=' in the href
        listings = soup.select('a[href*="details.html?id="]')

    print(f"DEBUG: Found {len(listings)} potential listings.")

    db = load_db()
    updated = False
    
    # Deduplicate: The Link Strategy might find the same car twice (image link + text link)
    seen_in_run = set()

    for ad in listings:
        # 1. Extract ID
        vid = ad.get('data-ad-id') # If it's an article
        
        # If it's a link (Strategy 2), extract from href
        if not vid and ad.name == 'a' and 'href' in ad.attrs:
             href = ad['href']
             if 'id=' in href:
                 vid = href.split('id=')[1].split('&')[0]

        # Try inner link if ad is an article container
        if not vid:
            link_tag = ad.find('a', href=True)
            if link_tag and 'id=' in link_tag['href']:
                vid = link_tag['href'].split('id=')[1].split('&')[0]
        
        if not vid: continue
        if vid in seen_in_run: continue # Skip duplicate on same page
        seen_in_run.add(vid)

        # 2. Extract Price
        # Try finding a price inside the element
        price_tag = ad.find('span', {'data-testid': re.compile(r'price')})
        if not price_tag:
            # Look wider if we are just on a link element
            # If 'ad' is just an <a> tag, we might need to look at its parent or text
            text = ad.get_text()
            price_match = re.search(r'€\s?[\d\.,]+', text)
            price_str = price_match.group(0) if price_match else "Check Link"
        else:
            price_str = price_tag.get_text(strip=True)
            
        price_val = parse_price(price_str)

        # 3. Extract Title
        title_tag = ad.find('h2')
        if title_tag:
            title = title_tag.get_text(strip=True)
        else:
            # If we matched a link, the title might be the link text
            title = ad.get_text(strip=True) or "Tesla Listing"
            # Clean up title if it captured too much text
            if len(title) > 50: title = title[:50] + "..."

        # 4. Extract Year
        meta_text = ad.get_text()
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
            # Handle format migration
            if isinstance(stored_data, int):
                old_price = stored_data
                orig_date = now_str
                db[vid] = {"price": price_val, "found_at": now_str}
                updated = True
            else:
                old_price = stored_data.get("price", 0)
                orig_date = stored_data.get("found_at", now_str)

            # --- DEBUG: Force check this specific ID ---
            # Remove this if-block later if logs get too spammy
            # print(f"DEBUG: Checking {vid}: {old_price} vs {price_val}")

            # PRICE DROP CHECK
            if old_price > 0 and price_val > 0 and price_val < (old_price - 50):
                print(f"ACTION: Sending Alert for {vid} (Drop: {old_price} -> {price_val})")
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

            # PRICE INCREASE CHECK
            elif price_val > (old_price + 50):
                print(f"DEBUG: Price increased for {vid} ({old_price} -> {price_val})")
                db[vid]["price"] = price_val
                updated = True

    if updated or first_run:
        save_db(db)
        print("DEBUG: Database saved.")
    else:
        print("DEBUG: No changes detected.")

if __name__ == "__main__":
    run()
