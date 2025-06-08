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

# === SUUMOから4月に入って掲載された物件名を取得 ===
def fetch_april_properties():
    base_url = 'https://suumo.jp/ms/shinchiku/kanto/'
    headers = {"User-Agent": "Mozilla/5.0"}
    names = []

    for page in range(1, 100):
        url = base_url + f'?page={page}' if page > 1 else base_url
        print(f"📄 読み込み中: {url}")
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            print(f"❌ ページ取得失敗: {res.status_code}")
            break
        soup = BeautifulSoup(res.text, 'html.parser')
        items = soup.select(".cassette_header-title")
        if not items:
            break

        for tag in items:
            name = tag.text.strip()
            if name:
                names.append(name)
        time.sleep(2)  # ペース制御

    unique = list(dict.fromkeys(names))
    print(f"✅ 取得物件数: {len(unique)} 件")
    return unique

# === スプレッドシートへ記録（重複排除） ===
def write_properties_to_sheet(names, cred_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    existing = sheet.col_values(2)[1:]  # 物件名（B列）
    today = datetime.now().strftime('%Y/%m/%d')
    new_count = 0

    for name in names:
        if name in existing:
            print(f"⏭️ スキップ（重複）: {name}")
            continue
        mc_url = f"https://www.e-mansion.co.jp/bbs/search/?q={name}"
        sheet.append_row(['', name, mc_url])
        new_count += 1
        time.sleep(1)

    print(f"✅ 新規追加: {new_count} 件")

# === メイン処理 ===
def main():
    try:
        cred = create_credentials_file()
        names = fetch_april_properties()
        if not names:
            print("❌ 物件名が取得できませんでした。")
            return
        write_properties_to_sheet(names, cred)
    except Exception:
        print("❌ 実行時エラー:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
