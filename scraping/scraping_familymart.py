# scraping_familymart_complete.py
# goods.html / safety.html からカテゴリURLを実在チェック付きで自動検出し、
# 商品詳細(名前/価格/画像/URL)と安全・安心(栄養)を突合してCSV化します。
# 対象カテゴリは下の TARGETS / USE_KEYS で管理。デザート等は含めません。

import time, re, csv, unicodedata
import requests
from bs4 import BeautifulSoup

BASE = "https://www.family.co.jp"
GOODS_TOP = f"{BASE}/goods.html"
SAFETY_TOP = f"{BASE}/goods/safety.html"
HEADERS = {"User-Agent": "Mozilla/5.0"}
OUT_CSV = "familymart_products.csv"

# 出力カラム（指定スキーマ）
COLUMNS = [
    "product_name","price","picture","calorie","protein","fat",
    "carbohydrate","sugar","fiber","salt","url"
]

# 使いたいカテゴリ（左：内部キー／中央：goods.html での表示テキスト正規表現／右：safety.html での表示テキスト正規表現）
# ※デザート・菓子・飲料・加工食品・酒は除外
TARGETS = [
    ("obento",    r"お弁当",                          r"お弁当"),
    ("omusubi",   r"おむすび|おにぎり",               r"おむすび"),
    ("osushi",    r"お寿司",                          r"お寿司"),
    ("sand",      r"サンドイッチ|ロールパン|バーガー", r"サンド|ロールパン|バーガー"),
    ("noodles",   r"そば|うどん|中華めん",             r"そば|うどん|中華めん"),
    ("pasta",     r"パスタ",                          r"パスタ"),
    ("salad",     r"サラダ",                          r"サラダ"),
    ("chilled",   r"惣菜|スープ|グラタン|お好み焼",     r"スープ|グラタン|お好み焼|惣菜"),
    ("hot_snack", r"ホットスナック|惣菜",              r"ホットスナック|惣菜"),
]

# 実際に回すカテゴリ（ここをオン/オフすれば増減可能）
USE_KEYS = [k for k,_,_ in TARGETS]

session = requests.Session()

# ===== 共通ユーティリティ =====
def get_soup(url: str) -> BeautifulSoup:
    for i in range(3):
        try:
            res = session.get(url, headers=HEADERS, timeout=25)
            res.raise_for_status()
            return BeautifulSoup(res.text, "html.parser")
        except Exception:
            if i == 2: raise
            time.sleep(1.2)

def absolutize(href: str) -> str:
    if not href: return ""
    if href.startswith("http"): return href
    if href.startswith("/"):    return BASE + href
    return f"{BASE}/{href}"

def url_ok(url: str) -> bool:
    try:
        r = session.get(url, headers=HEADERS, timeout=15, allow_redirects=True, stream=True)
        return 200 <= r.status_code < 300
    except Exception:
        return False

def normalize_name(name: str) -> str:
    if not name: return ""
    s = unicodedata.normalize("NFKC", name)
    s = s.replace("（", "(").replace("）", ")")
    s = re.sub(r"\s+", "", s)
    return s

def parse_price_tax_included(soup: BeautifulSoup) -> str:
    # ページ内に現れる「○○円」から最後の金額（=税込が多い）を採用
    for t in soup.find_all(string=re.compile(r"\d+\s*円")):
        s = (t or "").strip()
        nums = re.findall(r"(\d+)\s*円", s)
        if nums:
            return nums[-1]
    return ""

def extract_picture_url(soup: BeautifulSoup) -> str:
    og = soup.select_one('meta[property="og:image"]')
    if og and og.get("content"): return og["content"]
    imgel = soup.select_one("img[src]")
    if imgel:
        src = imgel.get("src") or ""
        return src if src.startswith("http") else absolutize(src)
    return ""

# ===== goods.html / safety.html からカテゴリURLを実在チェック付きで取得 =====
def discover_goods_categories():
    """goods.htmlから /goods/<detail_dir>.html を拾う"""
    soup = get_soup(GOODS_TOP)
    anchors = soup.select('a[href]')
    cat_map = {}  # key -> {list_url, detail_dir}
    for key, goods_pat, _ in TARGETS:
        rex = re.compile(goods_pat)
        for a in anchors:
            txt = (a.get_text(" ", strip=True) or "")
            if rex.search(txt):
                href = a.get("href") or ""
                full = absolutize(href)
                m = re.search(r"/goods/([^/]+)\.html$", full)
                if m and url_ok(full):
                    cat_map[key] = {"list_url": full, "detail_dir": m.group(1)}
                    break
    return cat_map

def discover_safety_categories():
    """safety.htmlから /goods/safety/goodsNNN.html を拾う"""
    soup = get_soup(SAFETY_TOP)
    anchors = soup.select('a[href]')
    safety_map = {}  # key -> safety_url
    for key, _, safety_pat in TARGETS:
        rex = re.compile(safety_pat)
        for a in anchors:
            txt = (a.get_text(" ", strip=True) or "")
            if rex.search(txt):
                href = a.get("href") or ""
                full = absolutize(href)
                if re.search(r"/goods/safety/goods\d+\.html$", full) and url_ok(full):
                    safety_map[key] = full
                    break
    return safety_map

# ===== 安全・安心（栄養） 辞書化：リンク → 直後のtable =====
def build_nutrition_dict(safety_url: str) -> dict:
    """
    構造：
      <a href="/goods/<dir>/<id>.html">商品名</a>
      （地域バッジや注記など…）
      <table> ← このtbodyの数値セルが 熱量/たんぱく質/脂質/炭水化物/食塩相当量
    """
    nutr = {}
    soup = get_soup(safety_url)
    if not soup:
        return nutr

    for a in soup.select('a[href$=".html"]'):
        name = (a.get_text(" ", strip=True) or "").strip()
        if not name:
            continue

        # a の後で最初に現れる table を栄養表とみなす
        nxt = a
        table = None
        for _ in range(25):
            nxt = nxt.find_next()
            if nxt is None:
                break
            if getattr(nxt, "name", None) == "table":
                table = nxt
                break
        if not table:
            continue

        vals = []
        for td in table.select("tbody tr td"):
            t = td.get_text(" ", strip=True)
            m = re.search(r"\d+(?:\.\d+)?", t)
            vals.append(m.group(0) if m else "")

        kcal = vals[0] if len(vals) > 0 else ""
        pro  = vals[1] if len(vals) > 1 else ""
        fat  = vals[2] if len(vals) > 2 else ""
        carb = vals[3] if len(vals) > 3 else ""
        salt = vals[4] if len(vals) > 4 else ""

        nutr[normalize_name(name)] = {
            "calorie": kcal, "protein": pro, "fat": fat,
            "carbohydrate": carb, "sugar": "", "fiber": "", "salt": salt
        }
    return nutr

def match_nutrition(nutr_dict: dict, product_name: str) -> dict:
    key = normalize_name(product_name)
    if key in nutr_dict:
        return nutr_dict[key]
    # 完全一致→前方一致/包含（双方向）
    for k, v in nutr_dict.items():
        if key.startswith(k) or k.startswith(key) or (k in key) or (key in k):
            return v
    return {"calorie":"","protein":"","fat":"","carbohydrate":"","sugar":"","fiber":"","salt":""}

# ===== 商品一覧 → 詳細URL抽出 =====
def list_detail_urls(list_url: str, detail_dir: str):
    soup = get_soup(list_url)
    if not soup: return []
    pattern = rf"/goods/{detail_dir}/\d+\.html$"
    urls = []
    for a in soup.select('a[href$=".html"]'):
        href = a.get("href") or ""
        full = absolutize(href)
        if re.search(pattern, full):
            urls.append(full)
    return list(dict.fromkeys(urls))  # 重複排除

# ===== 1カテゴリ処理 =====
def scrape_category_rows(key: str, list_url: str, detail_dir: str, safety_url: str):
    rows = []
    detail_urls = list_detail_urls(list_url, detail_dir)
    print(f"[{key}] list:   {list_url}")
    print(f"[{key}] safety: {safety_url}")
    print(f"[{key}] {len(detail_urls)} detail pages found")

    nutr_dict = build_nutrition_dict(safety_url)

    for i, url in enumerate(detail_urls, 1):
        try:
            dsoup = get_soup(url)
            name_el = dsoup.select_one("h1")
            name = name_el.get_text(" ", strip=True) if name_el else ""
            price = parse_price_tax_included(dsoup)
            picture = extract_picture_url(dsoup)
            nutr = match_nutrition(nutr_dict, name)

            row = {
                "product_name": name,
                "price": price,
                "picture": picture,
                "calorie": nutr["calorie"],
                "protein": nutr["protein"],
                "fat": nutr["fat"],
                "carbohydrate": nutr["carbohydrate"],
                "sugar": nutr["sugar"],
                "fiber": nutr["fiber"],
                "salt": nutr["salt"],
                "url": url,
            }

            # 必須：price / picture / calorie / protein / fat が埋まっていること
            if all([row["price"], row["picture"], row["calorie"], row["protein"], row["fat"]]):
                rows.append(row)
                print(f"  saved {i:3d}/{len(detail_urls)}: {name[:28]} ...")
        except Exception as e:
            print(f"  ! error ({url}): {e}")
        time.sleep(0.5)  # マナー
    return rows

# ===== メイン =====
def main():
    goods_map  = discover_goods_categories()
    safety_map = discover_safety_categories()

    all_rows = []
    for key in USE_KEYS:
        if key not in goods_map:
            print(f"[{key}] skipped: list URL not found on goods.html"); continue
        if key not in safety_map:
            print(f"[{key}] skipped: safety URL not found on safety.html"); continue
        list_url   = goods_map[key]["list_url"]
        detail_dir = goods_map[key]["detail_dir"]
        safety_url = safety_map[key]

        all_rows.extend(scrape_category_rows(key, list_url, detail_dir, safety_url))
        time.sleep(1.0)  # カテゴリ間の小休止

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in all_rows:
            w.writerow(r)

    print(f"Done -> {OUT_CSV} ({len(all_rows)} items)")

if __name__ == "__main__":
    main()
