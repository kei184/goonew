import os
import time
import tempfile
import traceback
import re
from collections import OrderedDict
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
SHEET_NAME    = '新着物件'

# === 2. Google認証ファイル生成（Secrets から） ===
def create_credentials_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
        tmp.write(os.environ['GOOGLE_CREDENTIALS_JSON'].encode())
        return tmp.name

# === 3. 既存の物件名一覧を取得（重複排除用） ===
def get_existing_names(sheet):
    """
    シートの B列（物件名列）をすべて読み込んで set にして返す。
    先頭行が見出しの場合は除外します。
    """
    try:
        col = sheet.col_values(2)
        if col and col[0] == '物件名':  # 見出しがある場合
            col = col[1:]
        return set(col)
    except Exception:
        return set()

# === 4. Gooの「新着物件」ページから物件名を取得 ===
def fetch_property_names():
    """
    Seleniumで「https://house.goo.ne.jp/buy/bm/」を開き、
    <div class="newObjectList__tit"> のテキストをすべて拾って返す。
    重複を排除し登場順を保持したリストで返します。
    """
    options = Options()
    options.binary_location = "/usr/bin/google-chrome"
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')

    service = Service("/usr/bin/chromedriver")
    driver  = webdriver.Chrome(service=service, options=options)

    try:
        driver.get("https://house.goo.ne.jp/buy/bm/")
        time.sleep(5)  # 必要に応じて WebDriverWait に置き換えてください

        elements = driver.find_elements(By.CSS_SELECTOR, "div.newObjectList__tit")
        raw_names = [el.text.strip() for el in elements if el.text.strip()]
    finally:
        driver.quit()

    # 重複を排除しつつ順序を保持
    unique = list(OrderedDict.fromkeys(raw_names))
    return unique

# === 5. Google検索で公式URLを取得（バックオフ対応） ===
def get_official_url(query, max_retries=3):
    """
    Google Custom Search JSON API を使い、物件名のみで検索して
    公式ページと思われるURLを返します。
    429エラーが返ってきたら指数バックオフしつつリトライします。
    """
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "q":   query,
        "key": os.environ['GOOGLE_API_KEY'],
        "cx":  os.environ['GOOGLE_CSE_ID'],
        "num": 1
    }

    backoff = 1
    for attempt in range(1, max_retries + 1):
        try:
            res = requests.get(endpoint, params=params)
            if res.status_code == 200:
                data = res.json()
                items = data.get("items", [])
                if items:
                    return items[0].get("link", "")
                return ""
            elif res.status_code == 429:
                print(f"⚠️ 429 Rate Limit for '{query}', retry #{attempt} after {backoff}s")
                time.sleep(backoff)
                backoff *= 2
                continue
            else:
                print(f"⚠️ CSE API error {res.status_code} for '{query}'")
                return ""
        except Exception as e:
            print(f"⚠️ Exception during CSE request for '{query}': {e}")
            time.sleep(backoff)
            backoff *= 2

    return ""

# === 6. スプレッドシートへ書き込み（重複チェック含む） ===
def write_to_sheet(names, cred_path):
    """
    names: [物件名1, 物件名2, ...]
    既存の物件名と重複しないものだけ Google Sheets に追記します。
    追記列は [日付, 物件名, 公式URL] の順。
    """
    # 認証・シートアクセス
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds  = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet  = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    # 既存の物件名を取得
    existing = get_existing_names(sheet)

    today = datetime.now().strftime('%Y/%m/%d')
    added = 0

    for name in names:
        if name in existing:
            continue  # 既にある場合はスキップ

        url = get_official_url(name)
        sheet.append_row([today, name, url])
        existing.add(name)
        added += 1
        # 書き込み負荷＋API負荷軽減のため少し待機
        time.sleep(1)

    print(f"✅ 新規追加: {added} 件（重複はスキップ）")

# === 7. メイン処理 ===
def main():
    try:
        cred   = create_credentials_file()
        names  = fetch_property_names()

        if not names:
            print("❌ 物件が取得できませんでした。")
            return

        print(f"✅ 取得済み物件（重複排除済み）: {len(names)} 件")
        for n in names:
            print("・", n)

        write_to_sheet(names, cred)
        print("✅ スプレッドシートへの書き込みが完了しました。")
    except Exception:
        print("❌ 実行時エラー:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
