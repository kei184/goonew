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

# === 4. 4月以降に掲載された物件のURLを取得 ===
def fetch_april_property_links():
    options = Options()
    options.binary_location = "/usr/bin/google-chrome"
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')

    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)

    base_url = "https://house.goo.ne.jp"
    april_links = set()

    for page in range(1, 30):
        url = f"{base_url}/buy/bm/pn/{page}/"
        driver.get(url)
        time.sleep(3)

        elems = driver.find_elements(By.CSS_SELECTOR, "a[href*='/buy/bm/detail/']")
        for elem in elems:
            href = elem.get_attribute("href")
            if href:
                april_links.add(href)

    driver.quit()
    return list(april_links)

# === 5. ページタイトルから物件名を取得 ===
def extract_property_names(links):
    headers = {"User-Agent": "Mozilla/5.0"}
    names = []
    for url in links:
        try:
            res = requests.get(url, headers=headers)
            if res.status_code != 200:
                continue
            soup = BeautifulSoup(res.text, 'html.parser')
            title = soup.title.text.strip() if soup.title else ''
            title = re.sub(r'【[^】]+】\s*', '', title)
            title = re.sub(r'（価格・間取り）.*$', '', title)
            if title:
                names.append((title, url))
        except:
            continue
        time.sleep(2)
    return names

# === 6. Google検索で公式URLを取得 ===
def get_official_url(query):
    search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}&num=1"
    for attempt in range(3):
        try:
            res = requests.get(search_url)
            if res.status_code == 429:
                time.sleep(15)
                continue
            res.raise_for_status()
            items = res.json().get('items', [])
            for item in items:
                link = item.get('link', '')
                if any(domain in link for domain in ['.co.jp', '.jp']) and 'suumo' not in link:
                    return link
            return items[0]['link'] if items else ''
        except:
            return ''
    return ''

# === 7. スプレッドシートへ追記 ===
def write_to_sheet(results, cred_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    existing = sheet.col_values(2)[1:]  # B列: 物件名
    today = datetime.now().strftime('%Y/%m/%d')
    new_count = 0

    for name, detail_url in results:
        if name in existing:
            continue
        url_mansion = f"https://www.e-mansion.co.jp/bbs/search/%E7%89%A9%E4%BB%B6?q={name}"
        url_google = f"https://www.google.com/search?q={name}"
        url_official = get_official_url(name)
        sheet.append_row([today, name, url_mansion, url_google, url_official])
        new_count += 1
        time.sleep(2)

    print(f"✅ 新規追加: {new_count} 件")

# === 8. メイン処理 ===
def main():
    try:
        cred = create_credentials_file()
        links = fetch_april_property_links()
        results = extract_property_names(links)
        write_to_sheet(results, cred)
    except Exception:
        traceback.print_exc()

if __name__ == '__main__':
    main()
