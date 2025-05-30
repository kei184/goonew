import os
import time
import tempfile
import traceback
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests

# === 1. スプレッドシート設定 ===
SPREADSHEET_ID = '1LpduIjFPimgUX6g1j5cfLnMT6OayfA5un3it2Z5rwuE'
SHEET_NAME = '新着物件'

# === 2. Google Custom Search API 設定 ===
GOOGLE_API_KEY = os.environ['GOOGLE_API_KEY']
GOOGLE_CSE_ID = os.environ['GOOGLE_CSE_ID']

# === 3. 認証ファイル生成 ===
def create_credentials_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
        tmp.write(os.environ['GOOGLE_CREDENTIALS_JSON'].encode())
        return tmp.name

# === 4. gooから物件名を取得 ===
def fetch_property_names():
    options = Options()
    options.binary_location = "/usr/bin/google-chrome-stable"
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--single-process')
    options.add_argument('--window-size=1920,1080')

    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.get("https://house.goo.ne.jp/buy/bm/")

        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.newObjectList__tit"))
        )

        titles = driver.find_elements(By.CSS_SELECTOR, "div.newObjectList__tit")
        names = [t.text.strip() for t in titles if t.text.strip()]

        print(f"✅ 取得件数: {len(names)}")
        for n in names:
            print("・", n)
        return names

    finally:
        driver.quit()

# === 5. Google検索で公式URLを取得 ===
def get_official_url(query):
    search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}"
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
        time.sleep(1)  # API制限対策

# === 7. メイン処理 ===
def main():
    try:
        cred_path = create_credentials_file()
        names = fetch_property_names()
        if not names:
            print("❌ 物件が取得できませんでした。")
            return
        write_to_sheet(names, cred_path)
        print(f"✅ {len(names)} 件をスプレッドシートに保存しました。")
    except Exception:
        print("❌ 実行時エラー:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
