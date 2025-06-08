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

# === 2. 認証ファイル生成 ===
def create_credentials_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
        tmp.write(os.environ['GOOGLE_CREDENTIALS_JSON'].encode())
        return tmp.name

# === 3. SUUMO 各エリアの新着物件一覧から物件名を取得 ===
def fetch_suumo_properties():
    base_url = "https://suumo.jp"
    area_paths = [
        "/ms/shinchiku/hokkaido/",
        "/ms/shinchiku/tohoku/",
        "/ms/shinchiku/kanto/",
        "/ms/shinchiku/chubu/",
        "/ms/shinchiku/kinki/",
        "/ms/shinchiku/chugoku/",
        "/ms/shinchiku/shikoku/",
        "/ms/shinchiku/kyushu/"
    ]

    headers = {"User-Agent": "Mozilla/5.0"}
    all_props = []

    for path in area_paths:
        url = base_url + path
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            print(f"❌ エリアページ取得失敗: {url}")
            continue

        soup = BeautifulSoup(res.text, 'html.parser')
        new_link = soup.find("a", string=re.compile("今週の.*新着物件"))
        if not new_link or not new_link.get("href"):
            print(f"⚠️ 新着物件リンクが見つかりません: {url}")
            continue

        list_url = base_url + new_link["href"]
        print(f"🔍 取得中: {list_url}")
        res_list = requests.get(list_url, headers=headers)
        if res_list.status_code != 200:
            print(f"❌ 一覧ページ取得失敗: {list_url}")
            continue

        list_soup = BeautifulSoup(res_list.text, 'html.parser')
        for a in list_soup.select("a.cassette_header-title"):
            name = a.text.strip()
            if name:
                all_props.append(name)
        time.sleep(1)

    unique = list(dict.fromkeys(all_props))
    print(f"✅ 取得済み物件（重複除去）: {len(unique)} 件")
    return unique

# === 4. スプレッドシートへ記録（B:物件名, C:マンションコミュニティURL） ===
def write_to_sheet(names, cred_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    existing_names = sheet.col_values(2)[1:]
    today = datetime.now().strftime('%Y/%m/%d')
    new_count = 0

    for name in names:
        if name in existing_names:
            print(f"⏭️ スキップ（重複）: {name}")
            continue

        mc_url = f"https://www.e-mansion.co.jp/bbs/search/?q={name}"
        sheet.append_row([today, name, mc_url])
        new_count += 1
        time.sleep(1)

    print(f"✅ 新規追加: {new_count} 件")

# === 5. メイン処理 ===
def main():
    try:
        cred = create_credentials_file()
        names = fetch_suumo_properties()
        if not names:
            print("❌ 物件が取得できませんでした。")
            return
        write_to_sheet(names, cred)
    except Exception:
        print("❌ 実行時エラー:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
