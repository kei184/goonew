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

# === 3. スプレッドシートから既存の物件名リストを取得 ===
def get_existing_names(sheet):
    """
    すでにシートに書き込まれている物件名の集合(Set)を返す。
    物件名はシート上「B列」に記載されている想定。
    """
    try:
        # B列の全データを取得（1行目が見出しなら2行目以降を使う）
        col_values = sheet.col_values(2)
        # 先頭が見出しの場合は除外
        if col_values and col_values[0] == '物件名':
            col_values = col_values[1:]
        return set(col_values)
    except Exception:
        return set()

# === 4. gooから (物件名, 詳細URL) を取得 ===
def fetch_properties():
    """
    Goo の「新着物件」ページを Selenium で開き、
    詳細ページ URL をすべて取得。さらに requests + BeautifulSoup で
    <title> タグから物件名を抽出し、(name, detail_url) のリストを返す。
    """
    # ① Selenium セットアップ（ヘッドレス Chrome）
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
        time.sleep(5)  # ページロード待機（要素検索に十分な時間を確保）

        # ② 詳細リンクを取得
        elems = driver.find_elements(By.CSS_SELECTOR, "ul.bxslider li a")
        raw_links = [a.get_attribute('href') for a in elems if a.get_attribute('href')]

    finally:
        driver.quit()

    # ③ ヘッダーを設置して詳細ページごとに物件名を取得
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/114.0.0.0 Safari/537.36"
        ),
        "Referer": "https://house.goo.ne.jp/buy/bm/"
    }

    props = []
    for url in raw_links:
        # URL が相対パスの場合は絶対化
        full = url if url.startswith('http') else 'https://house.goo.ne.jp' + url
        try:
            res = requests.get(full, headers=HEADERS)
            if res.status_code != 200:
                continue
            soup       = BeautifulSoup(res.text, 'html.parser')
            title_text = soup.title.text.strip() if soup.title else ''

            # タイトル前の「【…】」を一括で削除
            title_text = re.sub(r'^【[^】]+】\s*', '', title_text)
            # 「（価格・間取り）…」以降を一括で削除
            title_text = re.sub(r'（価格・間取り）.*$', '', title_text)

            name = title_text.strip()
            if name:
                props.append((name, full))
        except Exception:
            continue

    # ④ 重複を除去しつつ順序を保持
    seen = OrderedDict()
    for name, url in props:
        if name not in seen:
            seen[name] = url

    return list(seen.items())  # 返り値例: [("ザ・パークハウス 新宿富久町","https://..."), ...]

# === 5. スプレッドシートへ書き込み ===
def write_to_sheet(props, cred_path):
    """
    props = [(name, detail_url), ...]
    既存の物件名を読み込み、重複しないものだけを追加します。
    追加する列は [日付, 物件名, 公式URL] の順番。
    """
    # ⑴ シートにアクセス
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds  = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet  = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    # ⑵ 既存の物件名集合を取得
    existing_names = get_existing_names(sheet)

    # ⑶ 新規行を1つずつ追加
    today = datetime.now().strftime('%Y/%m/%d')
    added = 0
    for name, url in props:
        if name in existing_names:
            continue  # すでに書かれている物件はスキップ
        sheet.append_row([today, name, url])
        existing_names.add(name)
        added += 1
        time.sleep(1)  # 書き込み→API負荷を抑えるため

    print(f"✅ 新規追加: {added} 件（既存と重複するものはスキップしました）")

# === 6. メイン処理 ===
def main():
    try:
        cred  = create_credentials_file()
        props = fetch_properties()
        if not props:
            print("❌ 物件が取得できませんでした。")
            return

        print(f"✅ 取得済み物件（重複除去）: {len(props)} 件")
        for name, url in props:
            print("・", name, "→", url)

        write_to_sheet(props, cred)
        print(f"✅ スプレッドシートへの書き込みが完了しました。")
    except Exception:
        print("❌ 実行時エラー:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
