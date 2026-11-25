import os
import re
import json
import hashlib
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

# Configuration
KEYWORDS = [
    "computer", "laptop", "desktop", "tablet", "ipad", "galaxy tab",
    "graphics card", "motherboard", "ram", "ssd", "cpu", "pc parts",
    "mechanical keyboard", "monitor", "docking station", "soldering kit", "soldering station"
]
RADIUS_MILES = 30
ZIP_CODE = "28401"  # Wilmington, NC
MIN_NEW_DISCOUNT = 0.30  # 30% minimum off MSRP/List if not used/refurbished

SELECTED_SOURCES = os.getenv("SOURCES", "all").lower().split(",")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
FB_COOKIE = os.getenv("FB_COOKIE", "")  # Optional: c_user=...; xs=... for FB Marketplace

ua = UserAgent()

def get_headers():
    return {
        "User-Agent": ua.random,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

def make_id(source, url_or_title):
    clean = url_or_title.split("?")[0].strip().lower()
    return hashlib.sha256(f"{source}:{clean}".encode()).hexdigest()[:16]

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def parse_price(text):
    if not text:
        return None
    match = re.search(r"\$([\d,]+(?:\.\d{2})?)", str(text))
    if match:
        return float(match.group(1).replace(",", ""))
    return None

def qualifies(price, list_price, condition):
    cond = condition.lower()
    is_used_or_refurb = any(k in cond for k in ["used", "refurb", "renewed", "open-box", "open box", "pre-owned", "local"])
    if is_used_or_refurb:
        return True, 0
    if price and list_price and list_price > price:
        discount = (list_price - price) / list_price
        if discount >= MIN_NEW_DISCOUNT:
            return True, round(discount * 100)
    return False, 0

# --- SCRAPER MODULES ---

def scrape_craigslist():
    results = []
    # Wilmington, NC Craigslist (wilmington.craigslist.org) with 30-mile radius
    queries = ["computer", "tablet", "pc parts", "soldering", "monitor", "laptop"]
    for q in queries:
        url = f"https://wilmington.craigslist.org/search/sss?query={urllib.parse.quote(q)}&search_distance={RADIUS_MILES}&postal={ZIP_CODE}"
        try:
            r = requests.get(url, headers=get_headers(), timeout=15)
            soup = BeautifulSoup(r.text, "lxml")
            for item in soup.select("li.cl-static-search-result, li.result-row")[:10]:
                title_el = item.select_one(".title, .result-title")
                price_el = item.select_one(".price, .result-price")
                link_el = item.select_one("a")
                if not title_el or not link_el:
                    continue
                title = title_el.get_text(strip=True)
                price = parse_price(price_el.get_text(strip=True)) if price_el else None
                href = link_el["href"]
                if not href.startswith("http"):
                    href = "https://wilmington.craigslist.org" + href
                results.append({
                    "id": make_id("craigslist", href),
                    "source": "Craigslist (Wilmington)",
                    "title": title,
                    "price": price,
                    "list_price": None,
                    "discount_pct": 0,
                    "condition": "Local / Used",
                    "url": href
                })
        except Exception as e:
            print(f"Craigslist error ({q}): {e}")
    return results

def scrape_ebay():
    results = []
    # Searches both Refurbished/Used AND heavily discounted items
    queries = ["laptop", "tablet", "graphics card", "soldering station", "mechanical keyboard", "ssd"]
    for q in queries:
        # LH_ItemCondition=2000|2010|2020|2030|3000 = Certified/Seller Refurbished & Used
        url = f"https://www.ebay.com/sch/i.html?_nkw={urllib.parse.quote(q)}&LH_ItemCondition=2000%7C2010%7C2020%7C2030%7C3000&_sop=10"
        try:
            r = requests.get(url, headers=get_headers(), timeout=15)
            soup = BeautifulSoup(r.text, "lxml")
            for item in soup.select(".s-item")[:10]:
                title_el = item.select_one(".s-item__title")
                price_el = item.select_one(".s-item__price")
                link_el = item.select_one(".s-item__link")
                if not title_el or not link_el or "Shop on eBay" in title_el.get_text():
                    continue
                title = title_el.get_text(strip=True)
                price = parse_price(price_el.get_text(strip=True)) if price_el else None
                href = link_el["href"].split("?")[0]
                results.append({
                    "id": make_id("ebay", href),
                    "source": "eBay Refurb/Used",
                    "title": title,
                    "price": price,
                    "list_price": None,
                    "discount_pct": 0,
                    "condition": "Refurbished / Used",
                    "url": href
                })
        except Exception as e:
            print(f"eBay error ({q}): {e}")
    return results

def scrape_woot():
    results = []
    url = "https://computers.woot.com/plus"
    try:
        r = requests.get(url, headers=get_headers(), timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        for card in soup.select("a[href*='/offers/']")[:25]:
            text = card.get_text(" ", strip=True)
            href = card["href"]
            if not href.startswith("http"):
                href = "https://computers.woot.com" + href
            prices = re.findall(r"\$([\d,]+(?:\.\d{2})?)", text)
            price = float(prices[0].replace(",", "")) if len(prices) >= 1 else None
            list_price = float(prices[1].replace(",", "")) if len(prices) >= 2 else None
            ok, disc = qualifies(price, list_price, "Refurbished" if "refurb" in text.lower() else "New")
            if ok and price:
                results.append({
                    "id": make_id("woot", href),
                    "source": "Woot!",
                    "title": text[:110],
                    "price": price,
                    "list_price": list_price,
                    "discount_pct": disc,
                    "condition": "Refurbished" if "refurb" in text.lower() else "New Deal",
                    "url": href
                })
    except Exception as e:
        print(f"Woot error: {e}")
    return results

def scrape_backmarket():
    results = []
    queries = ["laptop", "ipad", "galaxy-tab", "monitor"]
    for q in queries:
        url = f"https://www.backmarket.com/en-us/search?q={q}"
        try:
            r = requests.get(url, headers=get_headers(), timeout=15)
            soup = BeautifulSoup(r.text, "lxml")
            for link in soup.select("a[href*='/en-us/p/']")[:8]:
                title = link.get_text(" ", strip=True)
                price = parse_price(title)
                href = "https://www.backmarket.com" + link["href"].split("?")[0]
                if price:
                    results.append({
                        "id": make_id("backmarket", href),
                        "source": "Back Market",
                        "title": title[:110],
                        "price": price,
                        "list_price": None,
                        "discount_pct": 0,
                        "condition": "Refurbished",
                        "url": href
                    })
        except Exception as e:
            print(f"BackMarket error ({q}): {e}")
    return results

def scrape_swappa():
    results = []
    for cat in ["laptops", "tablets"]:
        url = f"https://swappa.com/buy/{cat}"
        try:
            r = requests.get(url, headers=get_headers(), timeout=15)
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select("a[href*='/listing/'], a[href*='/buy/']")[:10]:
                txt = card.get_text(" ", strip=True)
                price = parse_price(txt)
                href = card["href"]
                if not href.startswith("http"):
                    href = "https://swappa.com" + href
                if price and len(txt) > 10:
                    results.append({
                        "id": make_id("swappa", href),
                        "source": "Swappa",
                        "title": txt[:110],
                        "price": price,
                        "list_price": None,
                        "discount_pct": 0,
                        "condition": "Used / Mint",
                        "url": href
                    })
        except Exception as e:
            print(f"Swappa error ({cat}): {e}")
    return results

def scrape_retail_30_off():
    """
    Aggregates Amazon (incl. Renewed), Newegg (incl. Refurb), Best Buy (incl. Outlet),
    Walmart, and Micro Center deals enforcing >=30% off for New items or Refurb/Open-Box.
    """
    results = []
    # Uses DuckDuckGo HTML search scoped to retailer deal/refurbished pages to bypass heavy JS walls cleanly
    targets = [
        ("Amazon / Renewed", "site:amazon.com (laptop OR tablet OR \"soldering kit\" OR ssd) (\"Renewed\" OR \"% off\")"),
        ("Newegg", "site:newegg.com (laptop OR gpu OR motherboard OR \"soldering\") (\"Refurbished\" OR \"Save:\")"),
        ("Best Buy", "site:bestbuy.com (laptop OR tablet OR monitor) (\"Open-Box\" OR \"Refurbished\" OR \"Save $\")"),
        ("Walmart", "site:walmart.com (laptop OR tablet OR \"soldering kit\" OR monitor) (\"Refurbished\" OR \"Save $\")"),
        ("Micro Center", "site:microcenter.com (laptop OR desktop OR gpu OR \"soldering\") (\"Refurbished\" OR \"Open Box\" OR \"Save $\")"),
    ]
    for source_name, dork in targets:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(dork)}"
        try:
            r = requests.get(url, headers=get_headers(), timeout=15)
            soup = BeautifulSoup(r.text, "lxml")
            for res in soup.select(".result")[:8]:
                a = res.select_one(".result__a")
                snip = res.select_one(".result__snippet")
                if not a:
                    continue
                title = a.get_text(strip=True)
                snippet = snip.get_text(" ", strip=True) if snip else ""
                combined = f"{title} {snippet}"
                href = a["href"]
                prices = re.findall(r"\$([\d,]+(?:\.\d{2})?)", combined)
                price = float(prices[0].replace(",", "")) if prices else None
                list_price = float(prices[1].replace(",", "")) if len(prices) > 1 else None
                pct_match = re.search(r"(\d{2})%\s*off", combined, re.I)
                explicit_pct = int(pct_match.group(1)) if pct_match else 0
                is_refurb = any(w in combined.lower() for w in ["refurb", "renewed", "open-box", "open box"])

                if is_refurb or explicit_pct >= 30:
                    results.append({
                        "id": make_id(source_name, href),
                        "source": source_name,
                        "title": title,
                        "price": price,
                        "list_price": list_price,
                        "discount_pct": explicit_pct,
                        "condition": "Refurbished/Open-Box" if is_refurb else f"New ({explicit_pct}% Off)",
                        "url": href
                    })
                else:
                    ok, disc = qualifies(price, list_price, "New")
                    if ok:
                        results.append({
                            "id": make_id(source_name, href),
                            "source": source_name,
                            "title": title,
                            "price": price,
                            "list_price": list_price,
                            "discount_pct": disc,
                            "condition": f"New ({disc}% Off)",
                            "url": href
                        })
        except Exception as e:
            print(f"{source_name} error: {e}")
    return results

def scrape_facebook_marketplace():
    results = []
    # Wilmington, NC Marketplace URL (30-mile radius = ~48km)
    url = "https://www.facebook.com/marketplace/wilmington/search/?query=computer%20laptop%20tablet&radius_in_km=48"
    headers = get_headers()
    if FB_COOKIE:
        headers["Cookie"] = FB_COOKIE
    try:
        r = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a[href*='/marketplace/item/']")[:15]:
            href = "https://www.facebook.com" + a["href"].split("?")[0]
            txt = a.get_text(" ", strip=True)
            price = parse_price(txt)
            if txt:
                results.append({
                    "id": make_id("fb", href),
                    "source": "FB Marketplace (30mi)",
                    "title": txt[:110],
                    "price": price,
                    "list_price": None,
                    "discount_pct": 0,
                    "condition": "Local / Used",
                    "url": href
                })
    except Exception as e:
        print(f"FB Marketplace note: {e}")
    return results

def send_push_notification(new_items):
    if not NTFY_TOPIC or not new_items:
        return
    count = len(new_items)
    top_lines = []
    for item in new_items[:4]:
        p = f"${item['price']:.0f}" if item.get("price") else "Deal"
        top_lines.append(f"• [{item['source']}] {p} - {item['title'][:55]}")
    body = "\n".join(top_lines)
    if count > 4:
        body += f"\n+ {count - 4} more deals in your app!"
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={
                "Title": f"🔥 {count} New Tech Deal{'s' if count > 1 else ''} Found!",
                "Priority": "high",
                "Tags": "computer,moneybag"
            },
            timeout=10
        )
    except Exception as e:
        print(f"Push alert error: {e}")

def main():
    blacklist = set(load_json("data/blacklist.json", []))
    existing_data = load_json("data/deals.json", {"items": []})
    existing_map = {item["id"]: item for item in existing_data.get("items", []) if item["id"] not in blacklist}

    source_map = {
        "craigslist": scrape_craigslist,
        "facebook": scrape_facebook_marketplace,
        "ebay": scrape_ebay,
        "woot": scrape_woot,
        "backmarket": scrape_backmarket,
        "swappa": scrape_swappa,
        "retail": scrape_retail_30_off,  # Covers Amazon, Newegg, Best Buy, Walmart, Micro Center
    }

    to_run = source_map.keys() if "all" in SELECTED_SOURCES else [s.strip() for s in SELECTED_SOURCES if s.strip() in source_map]

    newly_discovered = []
    now_str = datetime.now(ZoneInfo("America/New_York")).strftime("%b %d, %I:%M %p EST")

    for key in to_run:
        print(f"Running scraper: {key}...")
        scraped = source_map[key]()
        for item in scraped:
            iid = item["id"]
            if iid in blacklist:
                continue
            if iid not in existing_map:
                item["found_at"] = now_str
                existing_map[iid] = item
                newly_discovered.append(item)

    all_items = list(existing_map.values())
    save_json("data/deals.json", {
        "last_updated": now_str,
        "items": all_items
    })

    print(f"Done. Total active deals: {len(all_items)} | New this run: {len(newly_discovered)}")
    if newly_discovered:
        send_push_notification(newly_discovered)

if __name__ == "__main__":
    main()
