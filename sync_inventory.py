#!/usr/bin/env python3
"""Sync Notion inventory tracker (出品中 items) into index.html's product shelf."""

import base64
import io
import os
import re
import sys
from datetime import date, datetime

import requests
from deep_translator import GoogleTranslator
from notion_client import Client
from PIL import Image

NOTION_DATA_SOURCE_ID = "d7b55c79-19e2-45ba-bf41-81e72df196bf"
HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")
PLACEHOLDER_PATH = os.path.join(os.path.dirname(__file__), "assets", "placeholder.jpg")

GRID_START_MARKER = '<div class="grid" id="shelf-grid">'
OUTLET_GRID_START_MARKER = '<div class="outlet-grid" id="outlet-grid">'
MAX_IMAGE_DIM = 800
JPEG_QUALITY = 75

# TODO(Yuta): replace with your real Formspree form endpoint
# (https://formspree.io -> create a form -> copy the endpoint URL below).
# Until this is set, Outlet Corner submissions will fail silently.
FORMSPREE_ENDPOINT = "https://formspree.io/f/mwvglbjl"

# Series/work name -> site category. Extend as new series are stocked.
CATEGORY_MAP = {
    "one piece": "one-piece",
    "uzaki-chan wants to hang out!": "bishoujo",
    "super sonico": "bishoujo",
    "overlord": "dark-fantasy bishoujo",
    "blue lock": "sports-shonen",
}
DEFAULT_CATEGORY = "bishoujo"
NEW_FLAG_DAYS = 7

# Known-good exact translations, checked before falling back to machine translation.
CONDITION_TRANSLATIONS = {
    "未開封": "Unopened",
    "未開封・美品": "Unopened & like-new condition",
    "未開封、美品": "Unopened & like-new condition",
    "未使用": "Unopened",
    "中古": "Used",
}

_contains_japanese = re.compile(r"[぀-ヿ一-鿿]")


def get_category(series: str) -> str:
    key = (series or "").strip().lower()
    for name, cat in CATEGORY_MAP.items():
        if name in key:
            return cat
    return DEFAULT_CATEGORY


def translate_condition(condition: str) -> str:
    text = (condition or "").strip()
    if not text:
        return text
    if text in CONDITION_TRANSLATIONS:
        return CONDITION_TRANSLATIONS[text]
    if not _contains_japanese.search(text):
        return text
    try:
        return GoogleTranslator(source="ja", target="en").translate(text)
    except Exception as exc:  # noqa: BLE001 - translation is best-effort
        print(f"Condition translation failed for {text!r}: {exc}", file=sys.stderr)
        return text


def is_recently_listed(listed_date: str, days: int = NEW_FLAG_DAYS) -> bool:
    if not listed_date:
        return False
    try:
        d = datetime.fromisoformat(listed_date.replace("Z", "+00:00")).date()
    except ValueError:
        return False
    return (date.today() - d).days <= days


def compress_image(raw_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def fetch_and_compress(url: str) -> bytes:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return compress_image(resp.content)


def get_placeholder_bytes() -> bytes:
    with open(PLACEHOLDER_PATH, "rb") as f:
        return f.read()


def escape_html(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_card(product: dict) -> str:
    name = escape_html(product["name"])
    maker = escape_html(product["maker"])
    series = escape_html(product["series"])
    condition = escape_html(translate_condition(product["condition"]))
    ebay_url = escape_html(product["ebay_url"])
    category = escape_html(product["category"])
    b64 = product["image_b64"]

    series_line = " &middot; ".join(p for p in (series, maker) if p)

    if product["restocked"]:
        flag = '<span class="flag restocked">Restocked</span>'
    elif is_recently_listed(product["listed_date"]):
        flag = '<span class="flag new">New</span>'
    else:
        flag = ""

    return f"""      <div class="card" data-cat="{category}">
        <div class="photo">
          {flag}
          <img src="data:image/jpeg;base64,{b64}" alt="{name}" />
        </div>
        <div class="info">
          <span class="series">{series_line}</span>
          <h3>{name}</h3>
          <span class="condition">{condition}</span>
          <span class="ship">Ships from Japan &middot; tracked</span>
        </div>
        <a class="buy" href="{ebay_url}" target="_blank" rel="noopener">Get this one <span>&rarr;</span></a>
      </div>
"""


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


def build_outlet_card(item: dict, bundle_options: list[str]) -> str:
    name = escape_html(item["name"])
    maker = escape_html(item["maker"])
    series = escape_html(item["series"])
    condition = escape_html(translate_condition(item["condition"]))
    price = item["outlet_price"]
    b64 = item["image_b64"]
    slug = slugify(item["name"])

    series_line = " &middot; ".join(p for p in (series, maker) if p)

    options_html = "\n".join(
        f'          <option value="{escape_html(o)}">{escape_html(o)}</option>'
        for o in bundle_options
    )

    return f"""      <div class="outlet-card">
        <div class="photo">
          <span class="outlet-tag">+${price:g} Add-On</span>
          <img src="data:image/jpeg;base64,{b64}" alt="{name}" />
        </div>
        <div class="info">
          <span class="series">{series_line}</span>
          <h3>{name}</h3>
          <span class="condition">{condition}</span>
        </div>
        <form class="outlet-form" action="{FORMSPREE_ENDPOINT}" method="POST">
          <input type="hidden" name="outlet_item" value="{name}" />
          <input type="hidden" name="_subject" value="Outlet bundle request: {name}" />
          <label for="bundle-{slug}">Bundle with which $50 figure?</label>
          <select id="bundle-{slug}" name="bundle_item" required>
            <option value="" disabled selected>Choose a figure&hellip;</option>
{options_html}
          </select>
          <label for="handle-{slug}">Your name / eBay handle</label>
          <input type="text" id="handle-{slug}" name="customer_name" placeholder="e.g. figurefan_22" required />
          <button type="submit">Request This Bundle</button>
          <p class="outlet-note">Reserved for 24 hours after we confirm &mdash; released back if we don't hear back in time.</p>
        </form>
      </div>
"""


def fetch_titles_by_status(notion: Client, status: str) -> set[str]:
    titles = set()
    cursor = None
    while True:
        resp = notion.data_sources.query(
            data_source_id=NOTION_DATA_SOURCE_ID,
            filter={"property": "🔒 ステータス", "select": {"equals": status}},
            start_cursor=cursor,
        )
        for page in resp["results"]:
            title_items = page["properties"].get("✍️ 商品名", {}).get("title", [])
            name = "".join(t.get("plain_text", "") for t in title_items)
            if name:
                titles.add(name)

        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return titles


def fetch_listed_products(notion: Client) -> list[dict]:
    sold_titles = fetch_titles_by_status(notion, "売却済み")

    products = []
    cursor = None
    while True:
        resp = notion.data_sources.query(
            data_source_id=NOTION_DATA_SOURCE_ID,
            filter={"property": "🔒 ステータス", "select": {"equals": "出品中"}},
            start_cursor=cursor,
        )
        for page in resp["results"]:
            props = page["properties"]
            title_items = props.get("✍️ 商品名", {}).get("title", [])
            name = "".join(t.get("plain_text", "") for t in title_items)

            ebay_url = (props.get("🔒 eBay URL", {}) or {}).get("url") or ""
            if not name or not ebay_url:
                print(f"Skipping page {page['id']}: missing name or eBay URL", file=sys.stderr)
                continue

            maker = (props.get("✍️ メーカー", {}) or {}).get("rich_text", [])
            maker_text = "".join(t.get("plain_text", "") for t in maker)

            series = (props.get("✍️ シリーズ・作品名", {}) or {}).get("rich_text", [])
            series_text = "".join(t.get("plain_text", "") for t in series)

            condition = (props.get("✍️ コンディション", {}) or {}).get("rich_text", [])
            condition_text = "".join(t.get("plain_text", "") for t in condition)

            listed_date = ((props.get("🔒 出品日", {}) or {}).get("date") or {}).get("start")

            files = (props.get("✍️ 商品写真", {}) or {}).get("files", [])
            image_url = None
            if files:
                f = files[0]
                image_url = f.get("file", {}).get("url") or f.get("external", {}).get("url")

            products.append(
                {
                    "name": name,
                    "maker": maker_text,
                    "series": series_text,
                    "condition": condition_text,
                    "ebay_url": ebay_url,
                    "category": get_category(series_text),
                    "image_url": image_url,
                    "listed_date": listed_date,
                    "restocked": name in sold_titles,
                }
            )

        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return products


def fetch_outlet_products(notion: Client) -> list[dict]:
    items = []
    cursor = None
    while True:
        resp = notion.data_sources.query(
            data_source_id=NOTION_DATA_SOURCE_ID,
            filter={"property": "✍️ アウトレット対象", "checkbox": {"equals": True}},
            start_cursor=cursor,
        )
        for page in resp["results"]:
            props = page["properties"]
            title_items = props.get("✍️ 商品名", {}).get("title", [])
            name = "".join(t.get("plain_text", "") for t in title_items)
            if not name:
                print(f"Skipping outlet page {page['id']}: missing name", file=sys.stderr)
                continue

            maker = (props.get("✍️ メーカー", {}) or {}).get("rich_text", [])
            maker_text = "".join(t.get("plain_text", "") for t in maker)

            series = (props.get("✍️ シリーズ・作品名", {}) or {}).get("rich_text", [])
            series_text = "".join(t.get("plain_text", "") for t in series)

            condition = (props.get("✍️ コンディション", {}) or {}).get("rich_text", [])
            condition_text = "".join(t.get("plain_text", "") for t in condition)

            outlet_price = (props.get("✍️ アウトレット価格（USD）", {}) or {}).get("number") or 25

            files = (props.get("✍️ 商品写真", {}) or {}).get("files", [])
            image_url = None
            if files:
                f = files[0]
                image_url = f.get("file", {}).get("url") or f.get("external", {}).get("url")

            items.append(
                {
                    "name": name,
                    "maker": maker_text,
                    "series": series_text,
                    "condition": condition_text,
                    "outlet_price": outlet_price,
                    "image_url": image_url,
                }
            )

        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return items


def render_outlet_grid(outlet_items: list[dict], bundle_options: list[str]) -> str:
    placeholder_b64 = base64.b64encode(get_placeholder_bytes()).decode("ascii")

    cards = []
    for item in outlet_items:
        image_b64 = placeholder_b64
        if item["image_url"]:
            try:
                compressed = fetch_and_compress(item["image_url"])
                image_b64 = base64.b64encode(compressed).decode("ascii")
            except Exception as exc:  # noqa: BLE001 - log and fall back
                print(f"Outlet photo fetch failed for {item['name']!r}: {exc}", file=sys.stderr)

        item["image_b64"] = image_b64
        cards.append(build_outlet_card(item, bundle_options))

    if not cards:
        body = '\n      <p class="outlet-empty">Nothing in the outlet corner right now &mdash; check back soon.</p>\n'
    else:
        body = "\n" + "".join(cards)

    return OUTLET_GRID_START_MARKER + body + "    </div>"


def render_grid(products: list[dict]) -> str:
    placeholder_b64 = base64.b64encode(get_placeholder_bytes()).decode("ascii")

    cards = []
    for product in products:
        image_b64 = placeholder_b64
        if product["image_url"]:
            try:
                compressed = fetch_and_compress(product["image_url"])
                image_b64 = base64.b64encode(compressed).decode("ascii")
            except Exception as exc:  # noqa: BLE001 - log and fall back
                print(f"Photo fetch failed for {product['name']!r}: {exc}", file=sys.stderr)

        product["image_b64"] = image_b64
        cards.append(build_card(product))

    return GRID_START_MARKER + "\n\n" + "".join(cards) + "\n    </div>"


def update_html_section(html: str, marker: str, new_section: str, label: str) -> str:
    pattern = re.compile(re.escape(marker) + r".*?\n    </div>", re.DOTALL)
    if not pattern.search(html):
        raise RuntimeError(f"Could not locate {label} section in index.html")
    return pattern.sub(lambda _: new_section, html, count=1)


def main():
    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        print("NOTION_API_KEY environment variable is required", file=sys.stderr)
        sys.exit(1)

    notion = Client(auth=api_key)

    products = fetch_listed_products(notion)
    print(f"Found {len(products)} listed (出品中) product(s)")
    new_grid = render_grid(products)

    outlet_items = fetch_outlet_products(notion)
    print(f"Found {len(outlet_items)} outlet product(s)")
    bundle_options = [p["name"] for p in products]
    new_outlet_grid = render_outlet_grid(outlet_items, bundle_options)

    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    original_html = html
    html = update_html_section(html, GRID_START_MARKER, new_grid, "shelf-grid")
    html = update_html_section(html, OUTLET_GRID_START_MARKER, new_outlet_grid, "outlet-grid")

    if html == original_html:
        print("No changes to index.html")
        return

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html updated")


if __name__ == "__main__":
    main()
