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
import requests

# === 1. スプレッドシート設定 ===
SPREADSHEET_ID = 'あなたのスプレッドシートID'
SHEET_NAME = '新着物件'

# === 2. Google Custom Search API 設定 ===
GOOGLE_API_KEY = os.environ['GOOGLE_API_KEY']
GOOGLE_CSE_ID = os.environ['GOOGLE_CSE_ID']

# === 3. Google認証（Secretsから credentials.json を生成） ===
def create_credentials_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp_file:
        tmp_file.write(os.environ['GOOGLE_CREDENTIALS_JSON'].encode())
        return tmp_file.name

# === 4. gooから物件名を取得 ===
def fetch_property_names():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')

    driver = webdriver.Chrome(options=options)
    driver.get("https://house.goo.ne.jp/buy/bm/")
    time.sleep(5)

    titles = driver.find_elements(By.CSS_SELECTOR, "div.newObjectList__tit")
    property_names = [title.text.strip() for title in titles if title.text.strip()]

    driver.quit()
    return property_names

# === 5. Google検索でURLを取得 ===
def get_official_url(query):
    search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}"
    try:
        res = requests.get(search_url)
        res.raise_for_status()
        items = res.json().get('items')
        if items:
            return items[0]['link']
    except Exception as e:
        print(f"検索エラー: {e}")
    return ''

# === 6. スプレッドシートへ記録 ===
def write_to_sheet(property_names, credentials_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    today = datetime.now().strftime('%Y/%m/%d')
    for name in property_names:
        url = get_official_url(name)
        sheet.append_row([today, name, url])
        time.sleep(1)
