import os
import json
import re
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from scrapfly import ScrapflyClient, ScrapeConfig

SAPK = os.getenv('SAPK')
TBK  = os.getenv('TBK')
TCI  = json.loads(os.getenv('TCI', '[]'))
MBL  = os.getenv('MBL')

DB_FILE = "listings.json"
scrapfly = ScrapflyClient(key=SAPK)

def parse_price(s):
    if not s: return 0
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

def get_real_time():
    return datetime.now(timezone.utc) + timedelta(hours=2)

def is_recent_upload(ad_soup):
    """
    Parses 'Ad online since' or 'Inserat online seit' text.
    Returns: (bool is_recent, str reason)
    """
    now = get_real_time()
    today = now.date()
    yesterday = today - timedelta(days=1)
    
    text = ad_soup.get_text(" ", strip=True)

    # Regex to find: "Ad online since 12/26/2025" or "Inserat online seit 26.12.2025"
    # Matches: Keyword + junk space + Date
    # Date pattern: Digits + separator + Digits + separator + Digits
    date_pattern = re.search(r'(?:Ad online since|Inserat online seit|Online since|Eingestellt am).*?(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})', text, re.IGNORECASE)
    
    if date_pattern:
        date_str = date_pattern.group(1)
        parsed_date = None
        
        # Try US Format first (MM/DD/YYYY) - Common if using US proxy/GitHub
        try:
            if "/" in date_str:
                parsed_date = datetime.strptime(date_str, "%m/%d/%Y").date()
        except: pass
        
        # Try German/EU Format (DD.MM.YYYY)
        if not parsed_date:
            try:
                # Replace dots/dashes to standard format if needed
                clean_d = date_str.replace("-", ".")
                parsed_date = datetime.strptime(clean_d, "%d.%m.%Y").date()
            except: pass

        if parsed_date:
            if parsed_date >= yesterday:
                return True, f"Fresh Date: {parsed_date}"
            else:
                return False, f"Old Date: {parsed_date}"

    return False, "No Date Text Found"

def run():
    lt_now = get_real_time()
    now_str = lt_now.strftime("%Y-%m-%d %H:%M") # e.g. 2025-12-26 18:30
    
    print(f"DEBUG: Starting Scrape at {now_str} (LT)")
    
    first_run = not os.path.exists(DB_FILE)
    
    try:
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
    
    # Selectors
    listings = soup.find_all('article', {'data-testid': re.compile(r'adListing')})
    if not listings:
        listings = soup.select('a[href*="details.html?id="]')

    print(f"DEBUG: Found {len(listings)} potential listings.")

    db = load_db()
    updated = False
    
    for ad in listings:
        # Extract ID
        vid = ad.get('data-ad-id')
        if not vid:
            link_elem = ad.find('a', href=True) if ad.name == 'article' else ad
            if link_elem and 'id=' in link_elem.get('href', ''):
                vid = link_elem['href'].split('id=')[1].split('&')[0]
        
        if not vid: continue

        # Extract Price
        price_tag = ad.find('span', {'data-testid': re.compile(r'price')})
        if price_tag:
            price_str = price_tag.get_text(strip=True)
        else:
            p_match = re.search(r'€\s?[\d\.,]+', ad.get_text())
            price_str = p_match.group(0) if p_match else "0"
        
        price_val = parse_price(price_str)
        link = f"https://suchen.mobile.de/fahrzeuge/details.html?id={vid}&lang=en"

        # --- LOGIC ---
        
        # NEW CAR FOUND
        if vid not in db:
            # STRICT DATE CHECK
            is_recent, reason = is_recent_upload(ad)
            
            # Save to DB to avoid re-scanning old cars next time
            db[vid] = {"price": price_val, "found_at": now_str}
            updated = True
            
            # Only Notify if NOT first run AND Car is FRESH
            if not first_run:
                if is_recent:
                    print(f"DEBUG: New Valid Car {vid} ({reason})")
                    msg = (
                        f"*🆕 New Tesla Found\\!*\n\n"
                        f"💰 *{escape_md(price_str)}*\n"
                        f"📅 Found: {escape_md(now_str)}\n\n"
                        f"[Open Listing]({link})"
                    )
                    send_telegram(msg)
                else:
                    # Log why we skipped it
                    print(f"DEBUG: Skipped {vid} - {reason}")

        # EXISTING CAR (Check Price)
        else:
            stored_data = db[vid]
            # Handle old DB format
            if isinstance(stored_data, int):
                old_p = stored_data
            else:
                old_p = stored_data.get("price", 0)

            # Debug check
            if old_p != price_val:
                print(f"CHECK: {vid} | DB: {old_p} -> Web: {price_val}")

            # PRICE DROP (> 50 EUR)
            if old_p > 0 and price_val > 0 and price_val < (old_p - 50):
                print(f"ACTION: Sending Drop Alert for {vid}")
                # For drops, we send alert regardless of "Ad Online Since" date, 
                # because the Price Drop Event happened NOW.
                msg = (
                    f"*📉 Price Drop\\!*\n\n"
                    f"Old: ~{old_p} €~\n"
                    f"New: *{escape_md(price_str)}*\n"
                    f"📅 Found: {escape_md(now_str)}\n\n"
                    f"[Open Listing]({link})"
                )
                send_telegram(msg)
                
                if isinstance(db[vid], dict): db[vid]["price"] = price_val
                else: db[vid] = {"price": price_val, "found_at": now_str}
                updated = True

            # PRICE INCREASE (Update DB silently)
            elif price_val > (old_p + 50):
                if isinstance(db[vid], dict): db[vid]["price"] = price_val
                else: db[vid] = {"price": price_val, "found_at": now_str}
                updated = True

    if updated or first_run:
        save_db(db)
        print("DEBUG: Database saved.")
    else:
        print("DEBUG: No changes detected.")

if __name__ == "__main__":
    run()
