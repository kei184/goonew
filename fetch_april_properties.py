import os
import time
import tempfile
import traceback
from datetime import datetime

import requests
from bs4 import BeautifulSoup

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === スプレッドシート設定 ===
SPREADSHEET_ID = '1LpduIjFPimgUX6g1j5cfLnMT6OayfA5un3it2Z5rwuE'
SHEET_NAME = '新着物件'

# === 認証ファイル生成 ===
def create_credentials_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
        tmp.write(os.environ['GOOGLE_CREDENTIALS_JSON'].encode())
        return tmp.name

# === SUUMO 各エリアのURL一覧（新着新築マンション） ===
AREA_URLS = [
    "https://suumo.jp/ms/shinchiku/hokkaido/",
    "https://suumo.jp/ms/shinchiku/tohoku/",
    "https://suumo.jp/ms/shinchiku/kanto/",
    "https://suumo.jp/ms/shinchiku/chubu/",
    "https://suumo.jp/ms/shinchiku/kinki/",
    "https://suumo.jp/ms/shinchiku/chugoku/",
    "https://suumo.jp/ms/shinchiku/shikoku/",
    "https://suumo.jp/ms/shinchiku/kyushu/",
    "https://suumo.jp/ms/shinchiku/okinawa/"
]

# === SUUMOから物件名を取得 ===
def fetch_suumo_properties():
    headers = {"User-Agent": "Mozilla/5.0"}
    property_names = []

    for area_url in AREA_URLS:
        print(f"📦 処理中: {area_url}")
        try:
            res = requests.get(area_url, headers=headers)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, 'html.parser')
            links = soup.select("a.cassette_header-title")
            for link in links:
                name = link.get_text(strip=True)
                if name:
                    property_names.append(name)
            time.sleep(2)
        except Exception as e:
            print(f"⚠️ エラー: {area_url} → {e}")

    unique = list(dict.fromkeys(property_names))
    print(f"✅ 取得済み物件（重複除去）: {len(unique)} 件")
    return unique

# === スプレッドシートへ記録（既存と照合） ===
def write_to_sheet(names, cred_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    existing_names = sheet.col_values(2)[1:]  # B列: 物件名
    today = datetime.now().strftime('%Y/%m/%d')
    new_count = 0

    for name in names:
        if name in existing_names:
            print(f"⏭️ スキップ（重複）: {name}")
            continue

        sheet.append_row([today, name, '', '', ''])
        new_count += 1
        time.sleep(1)

    print(f"✅ 新規追加: {new_count} 件")

# === メイン処理 ===
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
