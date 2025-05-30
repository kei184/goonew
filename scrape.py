import os
import json
import time
import tempfile
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === 1. スプレッドシート情報 ===
SPREADSHEET_ID = 'あなたのスプレッドシートID'
SHEET_NAME = '新着物件'

# === 2. Google認証（Secretsから credentials.json を再生成） ===
def create_credentials_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp_file:
        tmp_file.write(os.environ['GOOGLE_CREDENTIALS_JSON'].encode())
        return tmp_file.name

# === 3. gooから物件名を取得 ===
def fetch_property_names():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')

    driver = webdriver.Chrome(options=options)
    driver.get("https://house.goo.ne.jp/buy/bm/")
    time.sleep(5)  # ページ描画待機

    titles = driver.find_elements(By.CSS_SELECTOR, "div.newObjectList__tit")
    property_names = [title.text.strip() for title in titles if title.text.strip()]
    
    driver.quit()
    return property_names

# === 4. スプレッドシートに書き込み ===
def write_to_sheet(property_names, credentials_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    today = datetime.now().strftime('%Y/%m/%d')
    for name in property_names:
        sheet.append_row([today, name, ''])

# === 5. メイン処理 ===
def main():
    credentials_path = create_credentials_file()
    names = fetch_property_names()
    write_to_sheet(names, credentials_path)
    print(f"{len(names)} 件の物件名を保存しました")

if __name__ == "__main__":
    main()
