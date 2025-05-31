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

# === 4. gooのトップから物件リンクを取得し、リンク先タイトルを物件名として使う ===
def fetch_property_names():
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

    names = []
    for url in urls:
        try:
            driver.get(url)
            time.sleep(1)
            title = driver.title
            name = re.sub(r'^【goo住宅・不動産】|（価格・間取り） 物件情報｜新築マンション・分譲マンション$', '', title).strip()
            if name and 'goo住宅・不動産' not in name:
                names.append(name)
        except Exception as e:
            print("❌ タイトル取得失敗:", e)

    driver.quit()

    unique = list(dict.fromkeys(names))
    print(f"✅ 取得済み物件（重複除去）: {len(unique)} 件")
    for name in unique:
        print("・", name)
    return unique

# === 5. Google検索で公式URLを取得（リトライ付き）===
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

# === 6. スプレッドシートへ記録（C:マンコミ検索, D:Google検索, E:公式URL）===
def write_to_sheet(names, cred_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    existing = sheet.col_values(2)[1:]  # B列: 物件名
    today = datetime.now().strftime('%Y/%m/%d')
    new_count = 0

    for name in names:
        if name in existing:
            print(f"⏭️ スキップ（重複）: {name}")
            continue

        try:
            manshon_url = f"https://www.e-mansion.co.jp/bbs/search/{requests.utils.quote(name)}"
            google_url = f"https://www.google.com/search?q={requests.utils.quote(name)}"
            official_url = get_official_url(name)

            sheet.append_row([today, name, manshon_url, google_url, official_url])
            new_count += 1
            time.sleep(2)  # 各物件ごとに待機
        except Exception as e:
            print(f"❌ 書き込みエラー: {name} - {e}")

    print(f"✅ 新規追加: {new_count} 件")

# === 7. メイン処理 ===
def main():
    try:
        cred = create_credentials_file()
        names = fetch_property_names()
        if not names:
            print("❌ 物件が取得できませんでした。")
            return
        write_to_sheet(names, cred)
    except Exception:
        print("❌ 実行時エラー:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
