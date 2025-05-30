import os
import time
import tempfile
import traceback
import re
from datetime import datetime
from collections import OrderedDict

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

# === 4. gooから物件名を取得（詳細ページタイトルから） ===
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

    # リンク先の詳細ページからタイトルを取得
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/114.0.0.0 Safari/537.36"
        ),
        "Referer": "https://house.goo.ne.jp/buy/bm/"
    }

    names = []
    for url in links:
        full = url if url.startswith('http') else 'https://house.goo.ne.jp' + url
        res = requests.get(full, headers=HEADERS)
        if res.status_code != 200:
            continue
        soup = BeautifulSoup(res.text, 'html.parser')
        title_text = soup.title.text.strip() if soup.title else ''
        # 前置句【…】と後置句（価格・間取り）以降を除去
        title_text = re.sub(r'^【[^】]+】\s*', '', title_text)
        title_text = re.sub(r'（価格・間取り）.*$', '', title_text)
        name = title_text.strip() or None
        if name:
            names.append(name)

    # 重複を除去しつつ順序を保持
    unique_names = list(OrderedDict.fromkeys(names))
    return unique_names

# === 5. Google検索で公式URLを取得 ===
def get_official_url(query):
    search_url = (
        f"https://www.googleapis.com/customsearch/v1"
        f"?q={requests.utils.quote(query)}"
        f"&key={GOOGLE_API_KEY}"
        f"&cx={GOOGLE_CSE_ID}"
    )
    try:
        res = requests.get(search_url)
        res.raise_for_status()
        items = res.json().get('items', [])
        return items[0]['link'] if items else ''
    except Exception:
        return ''

# === 6. スプレッドシートへ記録 ===
def write_to_sheet(names, cred_path):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    today = datetime.now().strftime('%Y/%m/%d')
    for name in names:
        url = get_official_url(name)
        sheet.append_row([today, name, url])
        time.sleep(1)  # API制限対策

# === 7. メイン処理 ===
def main():
    try:
        cred = create_credentials_file()
        names = fetch_property_names()
        if not names:
            print("❌ 物件が取得できませんでした。")
            return

        print(f"✅ 取得済み物件（重複除去）: {len(names)} 件")
        for n in names:
            print("・", n)

        write_to_sheet(names, cred)
        print(f"✅ {len(names)} 件をスプレッドシートに保存しました。")
    except Exception:
        print("❌ 実行時エラー:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
