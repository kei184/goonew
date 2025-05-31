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

# === 4. Gooの「新着物件」→詳細ページを巡回して物件名を取得 ===
def fetch_property_names():
    """
    Seleniumで「https://house.goo.ne.jp/buy/bm/」を開き、
    各詳細リンクを取得。requests + BeautifulSoup で詳細ページを開き、
    img タグの alt 属性（物件名）を取得します。
    重複を排除し登場順を保持したリストを返します。
    """
    options = Options()
    options.binary_location = "/usr/bin/google-chrome"
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')

    service = Service("/usr/bin/chromedriver")
    driver  = webdriver.Chrome(service=service, options=options)

    links = []
    try:
        driver.get("https://house.goo.ne.jp/buy/bm/")
        time.sleep(5)  # 必要に応じて WebDriverWait に置き換えてください

        # 「ul.bxslider li a」の href をすべて収集
        elems = driver.find_elements(By.CSS_SELECTOR, "ul.bxslider li a")
        for a in elems:
            href = a.get_attribute("href")
            if href and "/buy/bm/detail/" in href:
                full = href if href.startswith("http") else "https://house.goo.ne.jp" + href
                links.append(full)
    finally:
        driver.quit()

    if not links:
        return []

    # HTTPヘッダーを用意
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/114.0.0.0 Safari/537.36"
        ),
        "Referer": "https://house.goo.ne.jp/buy/bm/"
    }

    raw_names = []
    for detail_url in links:
        try:
            res = requests.get(detail_url, headers=HEADERS)
            if res.status_code != 200:
                continue
            soup = BeautifulSoup(res.text, "html.parser")

            # 詳細ページ内の img タグの alt 属性から物件名を取得
            # 例: <img class="property-main-image" alt="ザ・パークハウス 新宿富久町" ...>
            img = soup.select_one("img[property='og:image'], img.property-main-image, img[alt]")
            name = ""
            if img and img.has_attr("alt") and img["alt"].strip():
                name = img["alt"].strip()
            else:
                # imgのaltが取れなかった場合、<h1> や <h2> を探す
                h1 = soup.find("h1")
                if h1 and h1.text.strip():
                    name = h1.text.strip()
                else:
                    h2 = soup.find("h2")
                    name = h2.text.strip() if h2 and h2.text.strip() else ""

            # 前置句「【goo住宅・不動産】」が入るケースがあれば削除
            name = re.sub(r'^【[^】]+】\s*', '', name)
            # 「（価格・間取り）…」など後半を削除
            name = re.sub(r'（価格・間取り）.*$', '', name)

            if name:
                raw_names.append(name)

        except Exception:
            continue

    # 重複を排除しつつ順序を保持
    unique_names = list(OrderedDict.fromkeys(raw_names))
    return unique_names

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
                return items[0].get("link", "") if items else ""
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
    既存の物件名と重複しないものだけを Google Sheets に追記します。
    追記する列は [日付, 物件名, 公式URL] の順。
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
        cred  = create_credentials_file()
        names = fetch_property_names()

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
