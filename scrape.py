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

# === 4. gooから物件名を取得（詳細ページ title タグから） ===
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
    links = [a.get_attribute('href') for a in elems if '/buy/bm/detail/' in a.get_attribute('href')]
    driver.quit()

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    names = []
    for url in links:
        full = url if url.startswith('http') else 'https://house.goo.ne.jp' + url
        res = requests.get(full, headers=headers)
        if res.status_code != 200:
            print(f"⚠️ スキップ {full} → ステータス {res.status_code}")
            continue

        soup = BeautifulSoup(res.text, 'html.parser')
        title = soup.title.text.strip() if soup.title else ''
        name = title

        # 不要部分を削除
        name = re.sub(r'^【[^】]+】\s*', '', name)  # 例: 【goo住宅・不動産】
        name = re.sub(r'（価格・間取り）.*$', '', name)  # 例: （価格・間取り）以降を削除
        name = name.strip() or '【タイトル取得失敗】'

        names.append(name)

    # 重複除去
    unique_names = sorted(set(names))
    print(f"✅ 取得済み物件（重複除去）: {len(unique_names)} 件")
    for n in unique_names:
        print("・", n)

    return unique_names

# === 5. Google検索で公式URLを取得 ===
def get_official_url(query):
    search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}&num=1"
    try:
        res = requests.get(search_url)
        res.raise_for_status()
        items = res.json().get('items', [])
        return items[0]['link'] if items else ''
    except Exception as e:
        print("検索エラー:", e)
        return ''

# === 6. スプレッドシートへ記録 ===
def write_to_sheet(names, cred_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    today = datetime.now().strftime('%Y/%m/%d')
    for name in names:
        url = get_official_url(name)
        sheet.append_row([today, name, url])
        time.sleep(1)

# === 7. メイン処理 ===
def main():
    try:
        cred = create_credentials_file()
        names = fetch_property_names()
        if not names:
            print("❌ 物件が取得できませんでした。")
            return
        write_to_sheet(names, cred)
        print(f"✅ {len(names)} 件をスプレッドシートに保存しました。")
    except Exception:
        print("❌ 実行時エラー:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
