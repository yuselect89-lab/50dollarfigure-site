#!/usr/bin/env python3
"""Sync Notion inventory tracker (出品中 items) into index.html's product shelf."""

import base64
import io
import json
import os
import re
import sys
from datetime import date, datetime

import requests
from deep_translator import GoogleTranslator
from google.oauth2 import service_account
from googleapiclient.discovery import build
from notion_client import Client
from PIL import Image, ImageFilter
from pillow_heif import register_heif_opener

register_heif_opener()

NOTION_DATA_SOURCE_ID = "d7b55c79-19e2-45ba-bf41-81e72df196bf"
HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")
SITEMAP_PATH = os.path.join(os.path.dirname(__file__), "sitemap.xml")
PRODUCT_PAGES_DIR = os.path.join(os.path.dirname(__file__), "products")
PLACEHOLDER_URL = "assets/placeholder.jpg"
PLACEHOLDER_DIMS = (800, 800)
PRODUCTS_DIR = os.path.join(os.path.dirname(__file__), "assets", "products")
PRODUCTS_URL_PREFIX = "assets/products"
SITE_BASE_URL = "https://50dollarfigure.com"
RAW_REPO_IMAGE_BASE = "https://raw.githubusercontent.com/yuselect89-lab/50dollarfigure-site/main/assets/products"
DRIVE_IMAGE_MANIFEST = os.path.join(os.path.dirname(__file__), ".drive-image-manifest.json")

GOOGLE_DRIVE_PRIZE_FOLDER_ID = "1_1MnlneD79VFQiYy6-laIMCtVxBwZrc6"
GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

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
    "ウマ娘 プリティーダービー マヤノトップガン フィギュア": "Uma Musume Pretty Derby Mayano Top Gun Figure",
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




def build_drive_service(credentials_dict: dict):
    """Build a read-only Google Drive client from service-account credentials."""
    credentials = service_account.Credentials.from_service_account_info(
        credentials_dict, scopes=GOOGLE_DRIVE_SCOPES
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _escape_drive_query_value(value: str) -> str:
    """Escape a literal used inside a Google Drive API query."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_folder_by_name(drive, parent_folder_id: str, folder_name: str) -> str | None:
    """Find an exact-name child folder inside the configured prize folder."""
    escaped_name = _escape_drive_query_value(folder_name)
    results = drive.files().list(
        q=(
            f"'{parent_folder_id}' in parents "
            f"and name='{escaped_name}' "
            "and mimeType='application/vnd.google-apps.folder' "
            "and trashed=false"
        ),
        spaces="drive",
        fields="files(id,name)",
        pageSize=2,
    ).execute()
    folders = results.get("files", [])
    if len(folders) > 1:
        print(
            f"Multiple Drive folders matched {folder_name!r}; using {folders[0]['id']}",
            file=sys.stderr,
        )
    return folders[0]["id"] if folders else None


def _natural_filename_key(name: str) -> list:
    """Sort names naturally so 2.jpg comes before 10.jpg."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def get_images_from_folder(drive, folder_id: str) -> list[tuple[str, str]]:
    """Return image files in natural filename order."""
    images = []
    page_token = None
    while True:
        results = drive.files().list(
            q=f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false",
            spaces="drive",
            fields="nextPageToken,files(id,name,mimeType)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        images.extend((item["id"], item["name"]) for item in results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    return sorted(images, key=lambda item: _natural_filename_key(item[1]))


def get_google_drive_image_urls(drive, product_name: str) -> list[str]:
    """Return the first two Drive image URLs for an exact product-folder match."""
    folder_id = find_folder_by_name(drive, GOOGLE_DRIVE_PRIZE_FOLDER_ID, product_name)
    if not folder_id:
        print(f"No Google Drive folder found for {product_name!r}")
        return []

    images = get_images_from_folder(drive, folder_id)
    if not images:
        print(f"No images found in Google Drive folder for {product_name!r}")
        return []

    return [
        f"https://drive.google.com/uc?export=view&id={image_id}"
        for image_id, _ in images[:2]
    ]


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


def compress_image(raw_bytes: bytes) -> tuple[bytes, int, int]:
    # Always composite: a no-op for already-opaque photos (JPEGs, flat-background
    # shots), but essential for transparent PNG cutouts, which otherwise get their
    # transparent areas filled black by a plain RGBA->RGB convert. Since either
    # photo slot (main or second) can hold a transparent cutout, detecting by
    # content here is more robust than trusting which slot the file was in.
    img = composite_on_background(raw_bytes)
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


def fetch_and_compress(url: str) -> tuple[bytes, int, int]:
    resp = requests.get(resolve_image_url(url), timeout=30)
    resp.raise_for_status()
    return compress_image(resp.content)


def save_product_image(
    image_url: str, filename: str, used_files: set[str]
) -> tuple[str, int, int]:
    """Download, compress, and write a product photo to assets/products/.

    Returns (relative URL, width, height) to use in <img src/width/height>,
    and always falls back to the shared placeholder on any download/processing error.
    Transparent PNG cutouts are flattened onto the site's paper background with a
    soft drop shadow; already-opaque photos pass through the same step unaffected.
    """
    os.makedirs(PRODUCTS_DIR, exist_ok=True)

    # Images staged from Drive already live in this repository. Reuse the local
    # file instead of downloading its public GitHub URL back over HTTP.
    raw_prefix = RAW_REPO_IMAGE_BASE + "/"
    if image_url.startswith(raw_prefix):
        local_filename = image_url[len(raw_prefix):]
        if "/" not in local_filename and "\\" not in local_filename:
            local_path = os.path.join(PRODUCTS_DIR, local_filename)
            if os.path.isfile(local_path):
                try:
                    with Image.open(local_path) as local_image:
                        width, height = local_image.size
                    used_files.add(local_filename)
                    return (
                        f"{PRODUCTS_URL_PREFIX}/{local_filename}",
                        width,
                        height,
                    )
                except Exception as exc:
                    print(
                        f"Local GitHub image invalid for {local_filename!r}: {exc}",
                        file=sys.stderr,
                    )

    dest_path = os.path.join(PRODUCTS_DIR, filename)
    try:
        compressed, width, height = fetch_and_compress(image_url)
        with open(dest_path, "wb") as f:
            f.write(compressed)
        used_files.add(filename)
        return f"{PRODUCTS_URL_PREFIX}/{filename}", width, height
    except Exception as exc:  # noqa: BLE001 - retain cache or fall back
        print(f"Photo fetch failed for {filename!r}: {exc}", file=sys.stderr)
        if os.path.isfile(dest_path):
            try:
                with Image.open(dest_path) as cached_image:
                    width, height = cached_image.size
                used_files.add(filename)
                print(f"Using cached product image for {filename}")
                return f"{PRODUCTS_URL_PREFIX}/{filename}", width, height
            except Exception as cached_exc:
                print(
                    f"Cached product image invalid for {filename!r}: {cached_exc}",
                    file=sys.stderr,
                )
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
        # Notion-backed source images remain durable even when an item is not
        # currently listed on the website.
        if existing.startswith("notion-"):
            continue
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
    name = escape_html(product_display_name(product))
    maker = escape_html(translate_proper_noun(product["maker"]))
    series = escape_html(translate_proper_noun(product["series"]))
    condition = escape_html(translate_condition(product["condition"]))
    ebay_url = escape_html(product["ebay_url"])
    product_url = escape_html(product.get("product_url") or product_page_url(product))
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
        <a class="photo" href="{product_url}" aria-label="View details for {name}">
          {flag}
          <img src="{image_src}" width="{product['image_width']}" height="{product['image_height']}" alt="{name}" loading="lazy"{box_attr} />
        </a>
        <div class="info">
          <span class="series">{series_line}</span>
          <h3><a href="{product_url}">{name}</a></h3>
          <span class="condition">{condition}</span>
          <span class="ship">Ships from Japan &middot; tracked</span>
        </div>
        <a class="buy" href="{ebay_url}" target="_blank" rel="noopener">Get this one <span>&rarr;</span></a>
      </div>
"""


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


def page_key(page_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", page_id).lower()


def product_display_name(product: dict) -> str:
    return translate_proper_noun(product["name"])


def product_page_filename(product: dict) -> str:
    base = slugify(product_display_name(product))
    key = page_key(product.get("page_id", ""))[:8]
    if base == "item" and key:
        base = f"japan-prize-figure-{key}"
    return f"{base}-{key}.html" if key and not base.endswith(key) else f"{base}.html"


def product_page_url(product: dict) -> str:
    return f"products/{product_page_filename(product)}"


def product_absolute_url(product: dict) -> str:
    return f"{SITE_BASE_URL}/{product_page_url(product)}"


def product_description(product: dict) -> str:
    series = translate_proper_noun(product["series"])
    maker = translate_proper_noun(product["maker"])
    condition = translate_condition(product["condition"])
    parts = [p for p in (series, maker, condition) if p]
    detail = " / ".join(parts)
    if detail:
        return f"{product_display_name(product)}. {detail}. Anime prize figure shipped from Japan for $50."
    return f"{product_display_name(product)}. Anime prize figure shipped from Japan for $50."


def build_outlet_card(item: dict, bundle_options: list[str]) -> str:
    name = escape_html(translate_proper_noun(item["name"]))
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
                    "page_id": page["id"],
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
                item["box_image_url"], f"outlet-{slug}-box.jpg", used_files
            )
        else:
            item["box_image_src"] = None

        cards.append(build_outlet_card(item, bundle_options))

    if not cards:
        body = '\n      <p class="outlet-empty">Nothing in the outlet corner right now &mdash; check back soon.</p>\n'
    else:
        body = "\n" + "".join(cards)

    return OUTLET_GRID_START_MARKER + body + "    </div>"


def render_product_page(product: dict) -> str:
    name = escape_html(product_display_name(product))
    title = f"{name} - $50 FIGURE"
    maker = escape_html(translate_proper_noun(product["maker"]))
    series = escape_html(translate_proper_noun(product["series"]))
    condition = escape_html(translate_condition(product["condition"]))
    description = escape_html(product_description(product))
    ebay_url = escape_html(product["ebay_url"])
    image_src = escape_html(f"../{product['image_src']}")
    canonical_url = product_absolute_url(product)
    schema = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product_display_name(product),
        "url": canonical_url,
        "image": f"{SITE_BASE_URL}/{product['image_src']}",
        "description": product_description(product),
        "brand": {"@type": "Brand", "name": translate_proper_noun(product["maker"]) or "$50 FIGURE"},
        "offers": {
            "@type": "Offer",
            "name": "Buy this figure on eBay",
            "price": "50",
            "priceCurrency": "USD",
            "availability": "https://schema.org/InStock",
            "url": product["ebay_url"],
            "seller": {"@type": "Organization", "name": "$50 FIGURE"},
        },
    }
    facts = "\n".join(
        f"        <li><span>{label}</span><strong>{value}</strong></li>"
        for label, value in (
            ("Series", series),
            ("Maker", maker),
            ("Condition", condition),
            ("Price", "$50 shipped"),
        )
        if value
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical_url}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:image" content="{SITE_BASE_URL}/{product['image_src']}">
<meta property="og:url" content="{canonical_url}">
<meta property="og:type" content="product">
<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>
<style>
  :root {{ --paper:#F7F3EC; --shelf:#EDE7DA; --ink:#171310; --tag-red:#D8232A; --gold:#E8A93B; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink); font-family:Arial, sans-serif; }}
  a {{ color:inherit; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:24px; }}
  .top {{ display:flex; justify-content:space-between; gap:20px; align-items:center; padding:16px 0 28px; }}
  .brand {{ font-weight:900; letter-spacing:.04em; text-decoration:none; }}
  .back {{ font-size:14px; color:#6f6255; }}
  .product {{ display:grid; grid-template-columns:minmax(280px,.95fr) 1.05fr; gap:42px; align-items:start; }}
  .photo {{ background:var(--shelf); border:2px solid var(--ink); border-radius:14px; overflow:hidden; }}
  .photo img {{ display:block; width:100%; height:auto; }}
  .eyebrow {{ color:var(--tag-red); font-weight:800; letter-spacing:.08em; text-transform:uppercase; font-size:12px; }}
  h1 {{ font-size:clamp(32px,5vw,56px); line-height:1.05; margin:10px 0 18px; }}
  .desc {{ color:#493f36; line-height:1.7; font-size:16px; }}
  .facts {{ list-style:none; margin:28px 0; padding:0; border-top:2px solid var(--ink); }}
  .facts li {{ display:flex; justify-content:space-between; gap:20px; padding:13px 0; border-bottom:1px solid #d9d0bd; }}
  .facts span {{ color:#75695d; }}
  .buy {{ display:inline-flex; align-items:center; justify-content:center; background:var(--ink); color:var(--paper); border:2px solid var(--ink); border-radius:999px; padding:15px 26px; font-weight:800; text-decoration:none; }}
  .buy:hover {{ background:var(--tag-red); border-color:var(--tag-red); }}
  .note {{ margin-top:16px; color:#75695d; font-size:13px; line-height:1.6; }}
  @media (max-width:780px) {{ .product {{ grid-template-columns:1fr; gap:26px; }} .wrap {{ padding:18px; }} }}
</style>
</head>
<body>
  <main class="wrap">
    <nav class="top">
      <a class="brand" href="../">$50 FIGURE</a>
      <a class="back" href="../#shelf">Back to all figures</a>
    </nav>
    <section class="product">
      <div class="photo"><img src="{image_src}" width="{product['image_width']}" height="{product['image_height']}" alt="{name}"></div>
      <div>
        <div class="eyebrow">Anime prize figure from Japan</div>
        <h1>{name}</h1>
        <p class="desc">{description}</p>
        <ul class="facts">
{facts}
        </ul>
        <a class="buy" href="{ebay_url}" target="_blank" rel="noopener">Buy on eBay</a>
        <p class="note">$50 includes tracked shipping. Availability depends on the linked eBay listing.</p>
      </div>
    </section>
  </main>
</body>
</html>
"""


def render_product_pages(products: list[dict]) -> None:
    os.makedirs(PRODUCT_PAGES_DIR, exist_ok=True)
    active_files = set()
    for product in products:
        filename = product_page_filename(product)
        product["product_url"] = f"products/{filename}"
        active_files.add(filename)
        with open(os.path.join(PRODUCT_PAGES_DIR, filename), "w", encoding="utf-8") as page_file:
            page_file.write(render_product_page(product))

    for existing in os.listdir(PRODUCT_PAGES_DIR):
        if existing.endswith(".html") and existing not in active_files:
            os.remove(os.path.join(PRODUCT_PAGES_DIR, existing))


def render_sitemap(products: list[dict]) -> str:
    urls = [
        ("https://50dollarfigure.com/", "daily", "1.0"),
    ]
    for product in products:
        urls.append((product_absolute_url(product), "daily", "0.8"))

    rows = "\n".join(
        f"""  <url>
    <loc>{escape_html(url)}</loc>
    <changefreq>{changefreq}</changefreq>
    <priority>{priority}</priority>
  </url>"""
        for url, changefreq, priority in urls
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{rows}
</urlset>
"""


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
                product["box_image_url"], f"shelf-{slug}-box.jpg", used_files
            )
        else:
            product["box_image_src"] = None

        product["product_url"] = product_page_url(product)
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
                    "name": product_display_name(product),
                    "url": product_absolute_url(product),
                    "image": f"{SITE_BASE_URL}/{product['image_src']}",
                    "description": product_description(product),
                    "brand": {"@type": "Brand", "name": maker or "$50 FIGURE"},
                    "offers": {
                        "@type": "Offer",
                        "name": "Buy this figure on eBay",
                        "price": "50",
                        "priceCurrency": "USD",
                        "availability": "https://schema.org/InStock",
                        "url": product["ebay_url"],
                        "seller": {"@type": "Organization", "name": "$50 FIGURE"},
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



def sync_product_images_from_drive(notion: Client, drive) -> int:
    """Fill empty Notion product-photo properties from matching Drive folders."""
    updated_count = 0
    scanned_count = 0
    cursor = None

    while True:
        response = notion.data_sources.query(
            data_source_id=NOTION_DATA_SOURCE_ID,
            start_cursor=cursor,
        )
        for page in response.get("results", []):
            scanned_count += 1
            properties = page.get("properties", {})
            title_items = properties.get("✍️ 商品名", {}).get("title", [])
            product_name = "".join(
                item.get("plain_text", "") for item in title_items
            ).strip()
            photo_property = properties.get("✍️ 商品写真", {})

            if not product_name or photo_property.get("files"):
                continue

            try:
                urls = get_google_drive_image_urls(drive, product_name)
                if not urls:
                    continue
                notion.pages.update(
                    page_id=page["id"],
                    properties={
                        "✍️ 商品写真": {
                            "files": [
                                {
                                    "type": "external",
                                    "name": f"product_image_{index}",
                                    "external": {"url": url},
                                }
                                for index, url in enumerate(urls, start=1)
                            ]
                        }
                    },
                )
                updated_count += 1
                print(f"Updated images for {product_name}")
            except Exception as exc:
                print(
                    f"Failed to sync Drive images for {product_name!r}: {exc}",
                    file=sys.stderr,
                )

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    print(
        f"Synced images for {updated_count} product(s) from Google Drive "
        f"({scanned_count} scanned)"
    )
    return updated_count



def load_drive_service_from_env():
    """Initialize Drive from a base64 or raw-JSON GitHub Actions secret."""
    credentials_value = os.environ.get("GOOGLE_DRIVE_CREDENTIALS")
    print(f"Google Drive credentials configured: {bool(credentials_value)}")
    if not credentials_value:
        raise RuntimeError("GOOGLE_DRIVE_CREDENTIALS is not set")

    credential_text = credentials_value.strip()
    if credential_text.startswith("{"):
        credentials_data = json.loads(credential_text)
        credential_format = "JSON"
    else:
        normalized_b64 = "".join(credential_text.split())
        credentials_json = base64.b64decode(
            normalized_b64, validate=True
        ).decode("utf-8")
        credentials_data = json.loads(credentials_json)
        credential_format = "base64 JSON"

    drive = build_drive_service(credentials_data)
    print(f"Google Drive service initialized from {credential_format}")
    return drive


def _notion_external_url(file_item: dict) -> str:
    return (file_item.get("external") or {}).get("url") or ""


def _needs_github_image_staging(files: list[dict]) -> bool:
    """Stage empty, direct-Drive, or missing GitHub-backed image properties."""
    if not files:
        return True

    for item in files:
        url = _notion_external_url(item)
        if "drive.google.com/" in url:
            return True
        raw_prefix = RAW_REPO_IMAGE_BASE + "/"
        if url.startswith(raw_prefix):
            filename = url[len(raw_prefix):]
            if "/" in filename or "\\" in filename:
                return True
            if not os.path.isfile(os.path.join(PRODUCTS_DIR, filename)):
                return True
    return False


def _write_compressed_drive_image(drive, file_id: str, filename: str) -> None:
    raw_bytes = drive.files().get_media(fileId=file_id).execute()
    compressed, _, _ = compress_image(raw_bytes)
    os.makedirs(PRODUCTS_DIR, exist_ok=True)
    with open(os.path.join(PRODUCTS_DIR, filename), "wb") as output:
        output.write(compressed)


def stage_drive_images(notion: Client, drive) -> int:
    """Download Drive images into GitHub's assets tree and write a manifest."""
    manifest = []
    cursor = None
    scanned_count = 0
    failure_count = 0

    while True:
        response = notion.data_sources.query(
            data_source_id=NOTION_DATA_SOURCE_ID,
            filter={
                "property": "✍️ ブランド区分",
                "select": {"equals": "$50 FIGURE"},
            },
            start_cursor=cursor,
        )
        for page in response.get("results", []):
            scanned_count += 1
            properties = page.get("properties", {})
            title_items = properties.get("✍️ 商品名", {}).get("title", [])
            product_name = "".join(
                item.get("plain_text", "") for item in title_items
            ).strip()
            photo_files = properties.get("✍️ 商品写真", {}).get("files", [])

            if not product_name or not _needs_github_image_staging(photo_files):
                continue

            try:
                folder_id = find_folder_by_name(
                    drive, GOOGLE_DRIVE_PRIZE_FOLDER_ID, product_name
                )
                if not folder_id:
                    print(f"No Google Drive folder found for {product_name!r}")
                    continue

                drive_images = get_images_from_folder(drive, folder_id)[:2]
                if not drive_images:
                    print(f"No images found in Google Drive folder for {product_name!r}")
                    continue

                page_key = re.sub(r"[^a-zA-Z0-9]", "", page["id"]).lower()
                filenames = []
                for index, (file_id, _) in enumerate(drive_images, start=1):
                    filename = f"notion-{page_key}-{index}.jpg"
                    _write_compressed_drive_image(drive, file_id, filename)
                    filenames.append(filename)

                manifest.append(
                    {
                        "page_id": page["id"],
                        "product_name": product_name,
                        "filenames": filenames,
                    }
                )
                print(f"Staged {len(filenames)} image(s) for {product_name}")
            except Exception as exc:
                failure_count += 1
                print(
                    f"Failed to stage Drive images for {product_name!r}: {exc}",
                    file=sys.stderr,
                )

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    with open(DRIVE_IMAGE_MANIFEST, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, ensure_ascii=False, indent=2)

    print(
        f"Staged GitHub images for {len(manifest)} product(s) "
        f"({scanned_count} $50 FIGURE products scanned)"
    )
    if failure_count:
        raise RuntimeError(
            f"Failed to convert Drive images for {failure_count} product(s)"
        )
    return len(manifest)


def publish_staged_image_urls(notion: Client) -> int:
    """Publish already-pushed GitHub image URLs into Notion external files."""
    if not os.path.isfile(DRIVE_IMAGE_MANIFEST):
        raise RuntimeError("Drive image manifest is missing")

    with open(DRIVE_IMAGE_MANIFEST, "r", encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)

    updated_count = 0
    for entry in manifest:
        files = [
            {
                "type": "external",
                "name": filename,
                "external": {"url": f"{RAW_REPO_IMAGE_BASE}/{filename}"},
            }
            for filename in entry["filenames"]
        ]
        notion.pages.update(
            page_id=entry["page_id"],
            properties={"✍️ 商品写真": {"files": files}},
        )
        updated_count += 1
        print(f"Published GitHub image URLs for {entry['product_name']}")

    print(f"Published GitHub image URLs for {updated_count} product(s)")
    return updated_count


def main():
    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        print("NOTION_API_KEY environment variable is required", file=sys.stderr)
        sys.exit(1)

    print(f"GitHub ref: {os.environ.get('GITHUB_REF', 'local')}")
    print(f"GitHub SHA: {os.environ.get('GITHUB_SHA', 'local')}")
    notion = Client(auth=api_key)

    mode = sys.argv[1] if len(sys.argv) > 1 else "render"
    if mode == "--stage-drive-images":
        try:
            stage_drive_images(notion, load_drive_service_from_env())
        except Exception as exc:
            print(f"Drive image staging failed: {exc}", file=sys.stderr)
            sys.exit(1)
        return
    if mode == "--publish-drive-images":
        try:
            publish_staged_image_urls(notion)
        except Exception as exc:
            print(f"Publishing GitHub image URLs failed: {exc}", file=sys.stderr)
            sys.exit(1)
        return
    if mode != "render":
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(2)

    used_files: set[str] = set()

    products = fetch_listed_products(notion)
    print(f"Found {len(products)} listed (出品中) product(s)")
    new_grid = render_grid(products, used_files)
    render_product_pages(products)

    outlet_items = fetch_outlet_products(notion)
    print(f"Found {len(outlet_items)} outlet product(s)")
    bundle_options = [translate_proper_noun(p["name"]) for p in products]
    new_outlet_grid = render_outlet_grid(outlet_items, bundle_options, used_files)

    cleanup_stale_images(used_files)

    with open(SITEMAP_PATH, "w", encoding="utf-8") as f:
        f.write(render_sitemap(products))

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
