#!/usr/bin/env python3
"""Sync Notion inventory tracker (出品中 items) into index.html's product shelf."""

import io
import json
import os
import re
import sys
from datetime import date, datetime

import requests
from deep_translator import GoogleTranslator
from notion_client import Client
from PIL import Image, ImageFilter

NOTION_DATA_SOURCE_ID = "d7b55c79-19e2-45ba-bf41-81e72df196bf"
HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")
PLACEHOLDER_URL = "assets/placeholder.jpg"
PLACEHOLDER_DIMS = (800, 800)
PRODUCTS_DIR = os.path.join(os.path.dirname(__file__), "assets", "products")
PRODUCTS_URL_PREFIX = "assets/products"
SITE_BASE_URL = "https://50dollarfigure.com"

# Matches --paper in index.html, so composited box photos blend with the page.
BOX_PHOTO_BG = (247, 243, 236)

GITHUB_ISSUE_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/issues/(\d+)")
GITHUB_ATTACHMENT_URL_RE = re.compile(r"https://github\.com/user-attachments/assets/[a-zA-Z0-9-]+")

GRID_START_MARKER = '<div class="grid" id="shelf-grid">'
OUTLET_GRID_START_MARKER = '<div class="outlet-grid" id="outlet-grid">'
SCHEMA_START_MARKER = '<script type="application/ld+json" id="product-schema">'
SCHEMA_END_MARKER = "</script>"
MAX_IMAGE_DIM = 800
JPEG_QUALITY = 75

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

# Official English spelling for series/manufacturer names, since machine
# translation can't be trusted to get brand casing right (e.g. "BANDAI SPIRITS").
PROPER_NOUN_TRANSLATIONS = {
    "ワンピース": "One Piece",
    "バンダイスピリッツ": "BANDAI SPIRITS",
}

_contains_japanese = re.compile(r"[぀-ヿ一-鿿]")


def get_category(series: str) -> str:
    key = translate_proper_noun(series or "").strip().lower()
    for name, cat in CATEGORY_MAP.items():
        if name in key:
            return cat
    return DEFAULT_CATEGORY


def translate_text(text: str, known: dict[str, str] | None = None) -> str:
    value = (text or "").strip()
    if not value:
        return value
    if known and value in known:
        return known[value]
    if not _contains_japanese.search(value):
        return value
    try:
        return GoogleTranslator(source="ja", target="en").translate(value)
    except Exception as exc:  # noqa: BLE001 - translation is best-effort
        print(f"Translation failed for {value!r}: {exc}", file=sys.stderr)
        return value


def translate_condition(condition: str) -> str:
    return translate_text(condition, CONDITION_TRANSLATIONS)


def translate_proper_noun(name: str) -> str:
    return translate_text(name, PROPER_NOUN_TRANSLATIONS)


def is_recently_listed(listed_date: str, days: int = NEW_FLAG_DAYS) -> bool:
    if not listed_date:
        return False
    try:
        d = datetime.fromisoformat(listed_date.replace("Z", "+00:00")).date()
    except ValueError:
        return False
    return (date.today() - d).days <= days


def composite_on_background(raw_bytes: bytes, bg_color: tuple[int, int, int] = BOX_PHOTO_BG) -> Image.Image:
    """Flatten a transparent product photo onto a solid background with a soft drop shadow."""
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
    alpha = img.split()[-1]

    shadow = Image.new("RGBA", img.size, (23, 19, 16, 0))
    shadow.putalpha(alpha.point(lambda a: int(a * 0.35)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(img.width / 80))
    shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    offset = max(4, img.width // 100)
    shadow_layer.paste(shadow, (offset, offset + 2), shadow)

    canvas = Image.new("RGBA", img.size, bg_color + (255,))
    canvas = Image.alpha_composite(canvas, shadow_layer)
    canvas = Image.alpha_composite(canvas, img)
    return canvas.convert("RGB")


def compress_image(raw_bytes: bytes, composite: bool = False) -> tuple[bytes, int, int]:
    if composite:
        img = composite_on_background(raw_bytes)
    else:
        img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue(), img.width, img.height


def resolve_image_url(url: str) -> str:
    """Resolve a pasted GitHub issue link to the raw image it contains.

    The upload workaround for Notion's free-plan file limit is to drag a photo
    into a GitHub issue and copy a link from there — it's easy to grab the
    issue's page URL instead of the actual image attachment URL. If `url`
    looks like an issue page, fetch the issue body via the public GitHub API
    and pull out the embedded user-attachments image URL instead.
    """
    match = GITHUB_ISSUE_URL_RE.match(url)
    if not match:
        return url
    owner, repo, number = match.groups()
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{number}", timeout=15
        )
        resp.raise_for_status()
        body = resp.json().get("body") or ""
        attachment_match = GITHUB_ATTACHMENT_URL_RE.search(body)
        if attachment_match:
            return attachment_match.group(0)
    except Exception as exc:  # noqa: BLE001 - fall through to original URL
        print(f"Could not resolve GitHub issue URL {url!r}: {exc}", file=sys.stderr)
    return url


def fetch_and_compress(url: str, composite: bool = False) -> tuple[bytes, int, int]:
    resp = requests.get(resolve_image_url(url), timeout=30)
    resp.raise_for_status()
    return compress_image(resp.content, composite=composite)


def save_product_image(
    image_url: str, filename: str, used_files: set[str], composite: bool = False
) -> tuple[str, int, int]:
    """Download, compress, and write a product photo to assets/products/.

    Returns (relative URL, width, height) to use in <img src/width/height>,
    and always falls back to the shared placeholder on any download/processing error.
    When `composite` is true, the source is treated as a transparent cutout and
    flattened onto the site's paper background with a soft drop shadow (used for
    the real, unedited item photo — as opposed to the AI-generated promo image).
    """
    os.makedirs(PRODUCTS_DIR, exist_ok=True)
    dest_path = os.path.join(PRODUCTS_DIR, filename)
    try:
        compressed, width, height = fetch_and_compress(image_url, composite=composite)
        with open(dest_path, "wb") as f:
            f.write(compressed)
        used_files.add(filename)
        return f"{PRODUCTS_URL_PREFIX}/{filename}", width, height
    except Exception as exc:  # noqa: BLE001 - log and fall back
        print(f"Photo fetch failed for {filename!r}: {exc}", file=sys.stderr)
        return PLACEHOLDER_URL, *PLACEHOLDER_DIMS


def extract_photo_urls(files: list[dict]) -> tuple[str | None, str | None]:
    """Pull (promo_image_url, box_image_url) from a Notion Files & media property.

    First upload = AI-generated promo image (as displayed in the shelf grid).
    Second upload, if present = the real, unedited item photo (transparent PNG),
    shown as the second lightbox slide after compositing onto the site background.
    """

    def url_of(f: dict) -> str | None:
        return f.get("file", {}).get("url") or f.get("external", {}).get("url")

    promo_url = url_of(files[0]) if len(files) > 0 else None
    box_url = url_of(files[1]) if len(files) > 1 else None
    return promo_url, box_url


def cleanup_stale_images(used_files: set[str]) -> None:
    if not os.path.isdir(PRODUCTS_DIR):
        return
    for existing in os.listdir(PRODUCTS_DIR):
        if existing not in used_files:
            os.remove(os.path.join(PRODUCTS_DIR, existing))


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
    maker = escape_html(translate_proper_noun(product["maker"]))
    series = escape_html(translate_proper_noun(product["series"]))
    condition = escape_html(translate_condition(product["condition"]))
    ebay_url = escape_html(product["ebay_url"])
    category = escape_html(product["category"])
    image_src = escape_html(product["image_src"])

    series_line = " &middot; ".join(p for p in (series, maker) if p)

    if product["restocked"]:
        flag = '<span class="flag restocked">Restocked</span>'
    elif is_recently_listed(product["listed_date"]):
        flag = '<span class="flag new">New</span>'
    else:
        flag = ""

    box_attr = ""
    if product.get("box_image_src"):
        box_attr = f' data-box-src="{escape_html(product["box_image_src"])}"'

    return f"""      <div class="card" data-cat="{category}">
        <div class="photo">
          {flag}
          <img src="{image_src}" width="{product['image_width']}" height="{product['image_height']}" alt="{name}" loading="lazy"{box_attr} />
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
    maker = escape_html(translate_proper_noun(item["maker"]))
    series = escape_html(translate_proper_noun(item["series"]))
    condition = escape_html(translate_condition(item["condition"]))
    price = item["outlet_price"]
    image_src = escape_html(item["image_src"])
    slug = slugify(item["name"])

    series_line = " &middot; ".join(p for p in (series, maker) if p)

    options_html = "\n".join(
        f'          <option value="{escape_html(o)}">{escape_html(o)}</option>'
        for o in bundle_options
    )

    box_attr = ""
    if item.get("box_image_src"):
        box_attr = f' data-box-src="{escape_html(item["box_image_src"])}"'

    return f"""      <div class="outlet-card">
        <div class="photo">
          <span class="outlet-tag">+${price:g} Add-On</span>
          <img src="{image_src}" width="{item['image_width']}" height="{item['image_height']}" alt="{name}" loading="lazy"{box_attr} />
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
          <label for="contact-{slug}">eBay username or email, so we can reach you</label>
          <input type="text" id="contact-{slug}" name="customer_contact" placeholder="e.g. figurefan_22 or you@email.com" />
          <button type="submit">Request This Bundle</button>
          <p class="outlet-note">Reserved for 24 hours after we confirm. If you gave us a way to reach you, we'll message you when it's ready &mdash; otherwise, check back on this listing's title within 24 hours.</p>
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
            filter={
                "and": [
                    {"property": "🔒 ステータス", "select": {"equals": "出品中"}},
                    {"property": "✍️ ブランド区分", "select": {"equals": "$50 FIGURE"}},
                ]
            },
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
            image_url, box_image_url = extract_photo_urls(files)

            products.append(
                {
                    "name": name,
                    "maker": maker_text,
                    "series": series_text,
                    "condition": condition_text,
                    "ebay_url": ebay_url,
                    "category": get_category(series_text),
                    "image_url": image_url,
                    "box_image_url": box_image_url,
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
            filter={
                "and": [
                    {"property": "✍️ アウトレット対象", "checkbox": {"equals": True}},
                    {"property": "✍️ ブランド区分", "select": {"equals": "$50 FIGURE"}},
                ]
            },
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
            image_url, box_image_url = extract_photo_urls(files)

            items.append(
                {
                    "name": name,
                    "maker": maker_text,
                    "series": series_text,
                    "condition": condition_text,
                    "outlet_price": outlet_price,
                    "image_url": image_url,
                    "box_image_url": box_image_url,
                }
            )

        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return items


def render_outlet_grid(outlet_items: list[dict], bundle_options: list[str], used_files: set[str]) -> str:
    cards = []
    for item in outlet_items:
        slug = slugify(item["name"])
        if item["image_url"]:
            item["image_src"], item["image_width"], item["image_height"] = save_product_image(
                item["image_url"], f"outlet-{slug}.jpg", used_files
            )
        else:
            item["image_src"] = PLACEHOLDER_URL
            item["image_width"], item["image_height"] = PLACEHOLDER_DIMS

        if item.get("box_image_url"):
            item["box_image_src"], _, _ = save_product_image(
                item["box_image_url"], f"outlet-{slug}-box.jpg", used_files, composite=True
            )
        else:
            item["box_image_src"] = None

        cards.append(build_outlet_card(item, bundle_options))

    if not cards:
        body = '\n      <p class="outlet-empty">Nothing in the outlet corner right now &mdash; check back soon.</p>\n'
    else:
        body = "\n" + "".join(cards)

    return OUTLET_GRID_START_MARKER + body + "    </div>"


def render_grid(products: list[dict], used_files: set[str]) -> str:
    cards = []
    for product in products:
        slug = slugify(product["name"])
        if product["image_url"]:
            product["image_src"], product["image_width"], product["image_height"] = save_product_image(
                product["image_url"], f"shelf-{slug}.jpg", used_files
            )
        else:
            product["image_src"] = PLACEHOLDER_URL
            product["image_width"], product["image_height"] = PLACEHOLDER_DIMS

        if product.get("box_image_url"):
            product["box_image_src"], _, _ = save_product_image(
                product["box_image_url"], f"shelf-{slug}-box.jpg", used_files, composite=True
            )
        else:
            product["box_image_src"] = None

        cards.append(build_card(product))

    return GRID_START_MARKER + "\n\n" + "".join(cards) + "\n    </div>"


def render_product_schema(products: list[dict]) -> str:
    """Build a Product/ItemList JSON-LD block for Google's product search."""
    items = []
    for i, product in enumerate(products, start=1):
        series = translate_proper_noun(product["series"])
        maker = translate_proper_noun(product["maker"])
        description = " / ".join(p for p in (series, maker) if p) or "Anime & manga prize figure, shipped from Japan."
        items.append(
            {
                "@type": "ListItem",
                "position": i,
                "item": {
                    "@type": "Product",
                    "name": product["name"],
                    "image": f"{SITE_BASE_URL}/{product['image_src']}",
                    "description": description,
                    "brand": {"@type": "Brand", "name": maker or "$50 FIGURE"},
                    "offers": {
                        "@type": "Offer",
                        "price": "50",
                        "priceCurrency": "USD",
                        "availability": "https://schema.org/InStock",
                        "url": product["ebay_url"],
                    },
                },
            }
        )

    data = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": items}
    return SCHEMA_START_MARKER + json.dumps(data, ensure_ascii=False) + SCHEMA_END_MARKER


def update_html_section(html: str, marker: str, new_section: str, label: str) -> str:
    pattern = re.compile(re.escape(marker) + r".*?\n    </div>", re.DOTALL)
    if not pattern.search(html):
        raise RuntimeError(f"Could not locate {label} section in index.html")
    return pattern.sub(lambda _: new_section, html, count=1)


def update_schema_section(html: str, new_section: str) -> str:
    pattern = re.compile(re.escape(SCHEMA_START_MARKER) + r".*?" + re.escape(SCHEMA_END_MARKER), re.DOTALL)
    if not pattern.search(html):
        raise RuntimeError("Could not locate product-schema section in index.html")
    return pattern.sub(lambda _: new_section, html, count=1)


def main():
    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        print("NOTION_API_KEY environment variable is required", file=sys.stderr)
        sys.exit(1)

    notion = Client(auth=api_key)

    used_files: set[str] = set()

    products = fetch_listed_products(notion)
    print(f"Found {len(products)} listed (出品中) product(s)")
    new_grid = render_grid(products, used_files)

    outlet_items = fetch_outlet_products(notion)
    print(f"Found {len(outlet_items)} outlet product(s)")
    bundle_options = [p["name"] for p in products]
    new_outlet_grid = render_outlet_grid(outlet_items, bundle_options, used_files)

    cleanup_stale_images(used_files)

    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    new_schema = render_product_schema(products)

    original_html = html
    html = update_html_section(html, GRID_START_MARKER, new_grid, "shelf-grid")
    html = update_html_section(html, OUTLET_GRID_START_MARKER, new_outlet_grid, "outlet-grid")
    html = update_schema_section(html, new_schema)

    if html == original_html:
        print("No changes to index.html")
        return

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html updated")


if __name__ == "__main__":
    main()
