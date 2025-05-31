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

# === 4. gooから物件リンクを取得 ===
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

    names = []
    for url in links:
        full = url if url.startswith('http') else 'https://house.goo.ne.jp' + url
        try:
            res = requests.get(full, timeout=10)
            if res.status_code != 200:
                continue
            soup = BeautifulSoup(res.text, 'html.parser')
            title = soup.title.text.strip() if soup.title else ''
            title = re.sub(r'【[^】]+】\s*', '', title)
            title = re.sub(r'（価格・間取り）.*$', '', title)
            clean = title.strip()
            if clean and 'goo住宅・不動産' not in clean:
                names.append(clean)
        except Exception as e:
            print(f"⚠️ スキップ: {full} ({e})")
            continue

    unique = list(dict.fromkeys(names))
    print(f"✅ 取得済み物件（重複除去）: {len(unique)} 件")
    for name in unique:
        print("・", name)
    return unique

# === 5. Google検索で公式URLを取得（最大3回試行）===
def get_official_url(query):
    for attempt in range(3):
        try:
            search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}&num=1"
            res = requests.get(search_url)
            if res.status_code == 429:
                wait = 10 + 5 * attempt
                print(f"⚠️ API制限中（{res.status_code}）: 待機{wait}秒")
                time.sleep(wait)
                continue
            res.raise_for_status()
            items = res.json().get('items', [])
            for item in items:
                link = item.get('link', '')
                if any(domain in link for domain in ['.co.jp', '.jp']) and not 'suumo' in link:
                    return link
            return items[0]['link'] if items else ''
        except Exception as e:
            print(f"検索エラー: {e}")
            time.sleep(2)
    return ''

# === 6. スプレッドシートへ記録 ===
def write_to_sheet(names, cred_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    existing_names = sheet.col_values(2)[1:]  # B列（物件名）
    existing_urls = sheet.col_values(3)[1:]   # C列（URL）
    today = datetime.now().strftime('%Y/%m/%d')
    new_count = 0

    for name in names:
        if name in existing_names:
            idx = existing_names.index(name)
            if existing_urls[idx].strip():
                print(f"⏭️ スキップ（既にURLあり）: {name}")
                continue

        url = get_official_url(name)
        if name in existing_names:
            row_num = existing_names.index(name) + 2
            sheet.update_cell(row_num, 3, url)
            print(f"🔄 URLのみ更新: {name}")
        else:
            sheet.append_row([today, name, url])
            print(f"➕ 新規追加: {name}")
            new_count += 1
        time.sleep(1.5)

    print(f"✅ 完了。新規 {new_count} 件を追加")

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
