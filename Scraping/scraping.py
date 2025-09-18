import requests
from bs4 import BeautifulSoup
import csv
import re
import os

# CSVファイル名
filename = "seven_eleven_product.csv"

# 栄養成分を正規表現で分解する関数
def parse_nutrition(text):
    nutrition = {
        "calorie": "N/A",
        "protein": "N/A",
        "fat": "N/A",
        "carbohydrate": "N/A",
        "sugar": "N/A",
        "fiber": "N/A",
        "salt": "N/A",
    }

    # 各項目を正規表現で抽出
    patterns = {
        "calorie": r"熱量[:：]\s*([\d\.]+kcal)",
        "protein": r"たんぱく質[:：]\s*([\d\.]+g)",
        "fat": r"脂質[:：]\s*([\d\.]+g)",
        "carbohydrate": r"炭水化物[:：]\s*([\d\.]+g)",
        "sugar": r"糖質[:：]\s*([\d\.]+g)",
        "fiber": r"食物繊維[:：]\s*([\d\.]+g)",
        "salt": r"食塩相当量[:：]\s*([\d\.]+g)",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            nutrition[key] = match.group(1)

    return nutrition


# 商品ページから詳細を取得
def scrape_seven_eleven_product(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')

    # 商品名
    product_name_div = soup.find('div', class_='item_ttl')
    product_name_tag = product_name_div.find('h1') if product_name_div else None
    product_name = product_name_tag.get_text(strip=True).replace("\u3000", "") if product_name_tag else "N/A"

    # 価格
    price_tag = soup.find('div', class_='item_price')
    price = "N/A"
    if price_tag:
        price_text = price_tag.text.strip()
        price_match = re.search(r'(\d+円（税込\d+円）)', price_text)
        price = price_match.group(1) if price_match else price_text

    # 画像URL
    picture_url = ""
    product_wrap_div = soup.find('div', class_='productWrap')
    if product_wrap_div:
        img_tag = product_wrap_div.find('img')
        if img_tag and img_tag.has_attr("src"):
            picture_url = img_tag['src']

    # 栄養成分
    nutrition_td_text = ""
    for th in soup.find_all("th"):
        if "栄養成分" in th.get_text(strip=True):
            td = th.find_next("td")
            if td:
                nutrition_td_text = td.get_text(" ", strip=True)
                break
    nutrients = parse_nutrition(nutrition_td_text)

    return {
        "product_name": product_name,
        "price": price,
        "picture": picture_url,
        "url": url,
        **nutrients
    }


# CSVに保存（重複回避して追記）
def save_to_csv(data, filename=filename):
    file_exists = os.path.isfile(filename)

    fieldnames = ["product_name", "price", "picture", "url",
                  "calorie", "protein", "fat", "carbohydrate", "sugar", "fiber", "salt"]

    with open(filename, 'a', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

        # 新規作成時のみヘッダーを書き込む
        if not file_exists:
            writer.writeheader()

        # 既存ファイルの重複チェック
        if file_exists:
            with open(filename, 'r', encoding='utf-8') as f:
                existing = f.read()
            if data["url"] in existing:
                print(f"スキップ（既存）：{data['product_name']}")
                return

        writer.writerow(data)
        print(f"追加保存：{data['product_name']}")


# カテゴリページをクロールして商品URLを収集
def scrape_category(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')

    product_links = []
    for a in soup.select("div.list_inner a"):
        href = a.get("href")
        if href and "/products/" in href:
            full_url = "https://www.sej.co.jp" + href
            product_links.append(full_url)

    return list(set(product_links))  # 重複除去


# 使用例
if __name__ == "__main__":
    category_url = "https://www.sej.co.jp/products/a/onigiri"  # おにぎりカテゴリ例
    product_urls = scrape_category(category_url)

    for url in product_urls:
        try:
            product_info = scrape_seven_eleven_product(url)
            save_to_csv(product_info)
        except Exception as e:
            print(f"エラー：{url} -> {e}")
