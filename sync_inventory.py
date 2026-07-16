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
MAX_IMAGE_DIM = 800
JPEG_QUALITY = 75

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


def update_html(new_grid: str) -> bool:
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    pattern = re.compile(
        re.escape(GRID_START_MARKER) + r".*?\n    </div>", re.DOTALL
    )
    if not pattern.search(html):
        raise RuntimeError("Could not locate shelf-grid section in index.html")

    updated_html = pattern.sub(lambda _: new_grid, html, count=1)

    if updated_html == html:
        return False

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(updated_html)
    return True


def main():
    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        print("NOTION_API_KEY environment variable is required", file=sys.stderr)
        sys.exit(1)

    notion = Client(auth=api_key)
    products = fetch_listed_products(notion)
    print(f"Found {len(products)} listed (出品中) product(s)")

    new_grid = render_grid(products)
    changed = update_html(new_grid)

    if changed:
        print("index.html updated")
    else:
        print("No changes to index.html")


if __name__ == "__main__":
    main()
