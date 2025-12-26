import os
import json
import re
import requests
from datetime import datetime, timedelta, timezone
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

def get_lithuania_time():
    return datetime.now(timezone.utc) + timedelta(hours=2)

def check_upload_date(ad_soup):
    """
    Parses 'Ad online since' text.
    Returns: (bool is_recent, str display_date)
    """
    now = get_lithuania_time()
    today = now.date()
    yesterday = today - timedelta(days=1)
    
    text = ad_soup.get_text(" ", strip=True)

    # Regex for "Ad online since 12/26/2025" or "Inserat online seit 26.12.2025"
    date_pattern = re.search(r'(?:Ad online since|Inserat online seit|Online since|Eingestellt am).*?(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})', text, re.IGNORECASE)
    
    if date_pattern:
        date_str = date_pattern.group(1)
        parsed_date = None
        
        # Try US Format (MM/DD/YYYY)
        try:
            if "/" in date_str:
                parsed_date = datetime.strptime(date_str, "%m/%d/%Y").date()
        except: pass
        
        # Try EU Format (DD.MM.YYYY)
        if not parsed_date:
            try:
                clean_d = date_str.replace("-", ".")
                parsed_date = datetime.strptime(clean_d, "%d.%m.%Y").date()
            except: pass

        if parsed_date:
            # We return the formatted date string for the message
            display_str = parsed_date.strftime("%Y-%m-%d")
            
            if parsed_date >= yesterday:
                return True, display_str
            else:
                return False, display_str

    return False, "Unknown"

def run():
    lt_now = get_lithuania_time()
    now_str = lt_now.strftime("%Y-%m-%d %H:%M")
    
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
    
    listings = soup.find_all('article', {'data-testid': re.compile(r'adListing')})
    if not listings:
        listings = soup.select('a[href*="details.html?id="]')

    print(f"DEBUG: Found {len(listings)} potential listings.")

    db = load_db()
    updated = False
    
    for ad in listings:
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
        
        # 1. NEW CAR FOUND
        if vid not in db:
            is_recent, upload_date_str = check_upload_date(ad)
            
            db[vid] = {"price": price_val, "found_at": now_str}
            updated = True
            
            if not first_run:
                if is_recent:
                    print(f"DEBUG: New Valid Car {vid} (Date: {upload_date_str})")
                    msg = (
                        f"*🆕 New Tesla Found\\!*\n\n"
                        f"💰 *{escape_md(price_str)}*\n"
                        f"📅 Uploaded: {escape_md(upload_date_str)}\n\n"
                        f"[Open Listing]({link})"
                    )
                    send_telegram(msg)
                else:
                    print(f"DEBUG: Skipped {vid} - Too old ({upload_date_str})")

        # 2. EXISTING CAR (Price Check)
        else:
            stored_data = db[vid]
            if isinstance(stored_data, int): old_p = stored_data
            else: old_p = stored_data.get("price", 0)

            if old_p != price_val:
                print(f"CHECK: {vid} | DB: {old_p} -> Web: {price_val}")

            # PRICE DROP (> 50 EUR)
            if old_p > 0 and price_val > 0 and price_val < (old_p - 50):
                print(f"ACTION: Sending Drop Alert for {vid}")
                msg = (
                    f"*📉 Price Drop\\!*\n\n"
                    f"Old: ~{old_p} €~\n"
                    f"New: *{escape_md(price_str)}*\n"
                    f"📅 Detected: {escape_md(now_str)}\n\n"
                    f"[Open Listing]({link})"
                )
                send_telegram(msg)
                
                if isinstance(db[vid], dict): db[vid]["price"] = price_val
                else: db[vid] = {"price": price_val, "found_at": now_str}
                updated = True

            # PRICE INCREASE (> 50 EUR)
            elif price_val > (old_p + 50):
                print(f"ACTION: Sending Increase Alert for {vid}")
                msg = (
                    f"*📈 Price Increased\\!*\n\n"
                    f"Old: ~{old_p} €~\n"
                    f"New: *{escape_md(price_str)}*\n"
                    f"📅 Detected: {escape_md(now_str)}\n\n"
                    f"[Open Listing]({link})"
                )
                send_telegram(msg)

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
