import os
import time
import tempfile
import traceback
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

# === 4. gooから物件名を取得（詳細ページを巡回） ===
def fetch_property_names():
    # Selenium で一覧ページからリンクを取得
    options = Options()
    options.binary_location = "/usr/bin/google-chrome"
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')

    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    driver.get("https://house.goo.ne.jp/buy/bm/")
    time.sleep(5)  # 必要なら WebDriverWait に置き換えても可

    elems = driver.find_elements(By.CSS_SELECTOR, "ul.bxslider li a")
    links = [a.get_attribute('href') for a in elems]
    driver.quit()

    names = []
    for url in links:
        full = url if url.startswith('http') else 'https://house.goo.ne.jp' + url
        res = requests.get(full)
        soup = BeautifulSoup(res.text, 'html.parser')

        # 詳細ページのタイトル要素を探す
        h1 = soup.find('h1') or soup.find('h2')
        if h1 and h1.text.strip():
            names.append(h1.text.strip())
        else:
            # クラス名が分かっていれば適宜調整
            names.append('【タイトル取得失敗】')

    print(f"✅ 取得件数: {len(names)}")
    for n in names:
        print("・", n)
    return names

# === 5. Google検索で公式URLを取得 ===
def get_official_url(query):
    url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}"
    try:
        res = requests.get(url); res.raise_for_status()
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
