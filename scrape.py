import os
import time
import tempfile
import traceback
import re
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

import requests
from bs4 import BeautifulSoup

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === 1. スプレッドシート設定 ===
SPREADSHEET_ID = '1LpduIjFPimgUX6g1j5cfLnMT6OayfA5un3it2Z5rwuE'
SHEET_NAME = '新着物件'

# === 2. Google API設定 ===
GOOGLE_API_KEY = os.environ['GOOGLE_API_KEY']
GOOGLE_CSE_ID = os.environ['GOOGLE_CSE_ID']

# === 3. 認証ファイル生成 ===
def create_credentials_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
        tmp.write(os.environ['GOOGLE_CREDENTIALS_JSON'].encode())
        return tmp.name

# === 4. 物件詳細情報をスクレイピング ===
def fetch_property_details(url, driver):
    try:
        driver.get(url)
        time.sleep(2)
        html = driver.page_source
        soup = BeautifulSoup(html, 'html.parser')

        def extract_text(label):
            tag = soup.find(['th', 'dt'], string=re.compile(label))
            if tag:
                next_tag = tag.find_next_sibling(['td', 'dd'])
                if next_tag:
                    return next_tag.get_text(strip=True)
            return ''

        # 画像URL：最初の「image-popup」クラスの <a href=...> を取得
        image_anchor = soup.select_one('a.image-popup')
        image_url = ''
        if image_anchor and image_anchor.has_attr('href'):
            image_url = image_anchor['href']

        return {
            'image_url': image_url,
            'address': extract_text('住所'),
            'layout': extract_text('間取り'),
            'area': extract_text('専有面積'),
            'access': extract_text('交通')
        }

    except Exception as e:
        print("❌ 詳細情報の取得エラー:", e)
        return {
            'image_url': '',
            'address': '',
            'layout': '',
            'area': '',
            'access': ''
        }

# === 5. gooのトップから物件リンクを取得し、各種情報をまとめる ===
def fetch_property_infos():
    options = Options()
    options.binary_location = "/usr/bin/google-chrome"
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')

    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    driver.get("https://house.goo.ne.jp/buy/bm/")
    time.sleep(5)

    elems = driver.find_elements(By.CSS_SELECTOR, "ul.bxslider li a")
    urls = [a.get_attribute("href") for a in elems if a.get_attribute("href")]

    properties = []
    seen_names = set()

    for url in urls:
        try:
            driver.get(url)
            time.sleep(1)
            title = driver.title
            name = re.sub(r'^【goo住宅・不動産】|（価格・間取り） 物件情報｜新築マンション・分譲マンション$', '', title).strip()
            if not name or 'goo住宅・不動産' in name or name in seen_names:
                continue
            seen_names.add(name)
            detail = fetch_property_details(url, driver)
            properties.append({
                'name': name,
                'detail_url': url,
                **detail
            })
        except Exception as e:
            print("❌ タイトル取得失敗:", e)

    driver.quit()
    print(f"✅ 取得済み物件: {len(properties)} 件")
    for p in properties:
        print("・", p['name'])
    return properties

# === 6. Google検索で公式URLを取得 ===
def get_official_url(query):
    search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}&num=1"
    for attempt in range(3):
        try:
            res = requests.get(search_url)
            if res.status_code == 429:
                wait = 10
                print(f"⚠️ API制限（429）: {wait}秒待機して再試行... ({attempt + 1}/3)")
                time.sleep(wait)
                continue
            res.raise_for_status()
            items = res.json().get('items', [])
            for item in items:
                link = item.get('link', '')
                if any(domain in link for domain in ['.co.jp', '.jp']) and 'suumo' not in link:
                    return link
            return items[0]['link'] if items else ''
        except Exception as e:
            print("検索エラー:", e)
            return ''
    return ''

# === 7. スプレッドシートへ記録 ===
def write_to_sheet(properties, cred_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    existing = sheet.col_values(2)[1:]  # B列: 物件名
    today = datetime.now().strftime('%Y/%m/%d')
    new_count = 0

    for p in properties:
        name = p['name']
        if name in existing:
            print(f"⏭️ スキップ（重複）: {name}")
            continue

        try:
            manshon_url = f"https://www.e-mansion.co.jp/bbs/search/{requests.utils.quote(name)}"
            google_url = f"https://www.google.com/search?q={requests.utils.quote(name)}"
            official_url = get_official_url(name)

            sheet.append_row([
                today,
                name,
                manshon_url,
                google_url,
                official_url,
                p['image_url'],
                p['address'],
                p['layout'],
                p['area'],
                p['access'],
            ])
            new_count += 1
            time.sleep(2)
        except Exception as e:
            print(f"❌ 書き込みエラー: {name} - {e}")

    print(f"✅ 新規追加: {new_count} 件")

# === 8. メイン処理 ===
def main():
    try:
        cred = create_credentials_file()
        properties = fetch_property_infos()
        if not properties:
            print("❌ 物件が取得できませんでした。")
            return
        write_to_sheet(properties, cred)
    except Exception:
        print("❌ 実行時エラー:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
