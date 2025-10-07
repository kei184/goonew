# scrape.py
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
# 必要に応じて待機を厳密化する場合は以下を使用
# from selenium.webdriver.support.ui import WebDriverWait
# from selenium.webdriver.support import expected_conditions as EC

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


# ==============================
# 追加：詳細抽出のヘルパー
# ==============================

LABELS = {
    "address": [r"住所", r"所在地"],
    "access":  [r"交通"],
    "layout":  [r"間取り", r"間取"],
    "area":    [r"専有面積", r"専有面積（壁芯）", r"専有面積（登記）"],
}

def _text_without_title(soup: BeautifulSoup) -> str:
    full = soup.get_text("\n", strip=True)
    if soup.title and soup.title.string:
        full = full.replace(soup.title.string.strip(), "")
    return full

def _first_after_label_text(soup: BeautifulSoup, label_patterns) -> str:
    """
    dt/dd → th/td → 全文テキストの順で、ラベル直後の1行ぶんを返す
    """
    def _pair(dt_like, dd_like):
        for lp in label_patterns:
            tag = soup.find(dt_like, string=re.compile(rf"^\s*{lp}\s*[:：]?\s*$"))
            if tag:
                sib = tag.find_next_sibling(dd_like)
                if sib:
                    return sib.get_text(" ", strip=True)
            tag = soup.find(dt_like, string=re.compile(lp))
            if tag:
                sib = tag.find_next_sibling(dd_like)
                if sib:
                    return sib.get_text(" ", strip=True)
        return ""

    v = _pair("dt", "dd")
    if v: return v
    v = _pair("th", "td")
    if v: return v

    full = _text_without_title(soup)
    for lp in label_patterns:
        m = re.search(rf"{lp}\s*[:：]?\s*([^\n\r]+)", full)
        if m:
            cand = m.group(1).strip()
            if any(bad in cand for bad in ("物件情報", "価格", "新築マンション", "分譲マンション")):
                continue
            return cand
    return ""

def _normalize_layout(raw: str) -> str:
    """
    レイアウトを '2LDK・3LDK' のように統一。
    - 数字は半角化
    - 種類順: R < K < DK < LDK
    - 重複除去して '・' 連結
    """
    txt = (raw or "").replace("　", " ")
    hits = re.findall(r"([0-9０-９]+)\s*(LDK|DK|K|R)", txt, flags=re.I)
    items = []
    for num, typ in hits:
        num = num.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        typ = typ.upper()
        # 数値が先に来るよう保持
        items.append((int(num), typ))

    if not items and "ワンルーム" in txt:
        return "ワンルーム"

    order = {"R":0, "K":1, "DK":2, "LDK":3}
    items = sorted(items, key=lambda x: (x[0], order.get(x[1], 99)))

    seen = set()
    out = []
    for n, t in items:
        key = f"{n}{t}"
        if key not in seen:
            seen.add(key)
            out.append(f"{n}{t}")
    return "・".join(out)

def _normalize_area(raw: str) -> str:
    """
    専有面積を '56.63m2～68.38m2' 形式に統一。
    - ㎡/m²/m^2/m を m2 に寄せる
    - “～” を含むレンジ優先、無ければ複数値から最小～最大
    - 単一値なら 'XX.XXm2'
    """
    def _to_m2(s: str) -> str:
        s = s or ""
        s = s.replace("㎡", "m2").replace("m^2", "m2")
        s = re.sub(r"m\s*２", "m2", s)  # m２ → m2
        s = re.sub(r"\bm\s*$", "m2", s) # 末尾 m → m2
        s = s.translate(str.maketrans("０１２３４５６７８９．－", "0123456789.-"))
        s = re.sub(r"^[：:/\-\s]+", "", s)  # 先頭記号
        s = re.sub(r"\s*(超|平均|前後|程度)", "", s)
        return s

    txt = _to_m2(raw)

    m = re.search(r"(\d+(?:\.\d+)?)\s*m2\s*～\s*(\d+(?:\.\d+)?)\s*m2", txt)
    if m:
        a, b = m.group(1), m.group(2)
        return f"{a}m2～{b}m2"

    nums = re.findall(r"(\d+(?:\.\d+)?)\s*m2", txt)
    if len(nums) >= 2:
        vals = sorted(float(n) for n in nums)
        return f"{vals[0]:g}m2～{vals[-1]:g}m2"
    if len(nums) == 1:
        return f"{nums[0]}m2"

    # raw側をもう一度寄せて保険
    raw2 = (raw or "").replace("㎡", "m2")
    m2 = re.findall(r"(\d+(?:\.\d+)?)\s*m2", raw2)
    if len(m2) >= 2:
        vals = sorted(float(n) for n in m2)
        return f"{vals[0]:g}m2～{vals[-1]:g}m2"
    if len(m2) == 1:
        return f"{m2[0]}m2"

    return ""

def _sanitize_cell(x: str) -> str:
    """セル内のタブ/改行/連続空白を除去して安定化。"""
    if x is None:
        return ""
    s = re.sub(r"[\t\r\n]+", " ", str(x))
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def fetch_property_details(url, driver):
    """
    画像URL（?700優先）/ 住所 / 交通 / 間取り（2LDK・3LDK）/ 専有面積（56.63m2～68.38m2）
    を抽出して返す。
    """
    driver.get(url)
    time.sleep(1.2)

    soup = BeautifulSoup(driver.page_source, "html.parser")

    # 画像URL：a.image-popup 最優先 → img[src^=https://img.house.goo.ne.jp]
    image_url = ""
    a_img = soup.select_one('a.image-popup[href^="https://img.house.goo.ne.jp/"]')
    if a_img and a_img.has_attr("href"):
        image_url = a_img["href"]
    else:
        img = soup.find("img", src=re.compile(r"^https://img\.house\.goo\.ne\.jp/"))
        if img and img.has_attr("src"):
            image_url = re.sub(r"\?500\b", "?700", img["src"])

    # ラベル直後テキスト
    raw_address = _first_after_label_text(soup, LABELS["address"])
    raw_access  = _first_after_label_text(soup, LABELS["access"])
    raw_layout  = _first_after_label_text(soup, LABELS["layout"])
    raw_area    = _first_after_label_text(soup, LABELS["area"])

    # 最終整形（フォーマット保証）
    address = _sanitize_cell(raw_address)
    access  = _sanitize_cell(raw_access)
    layout  = _normalize_layout(raw_layout or _text_without_title(soup))
    area    = _normalize_area(raw_area   or _text_without_title(soup))

    # 任意のデバッグ
    if os.getenv("DEBUG_DETAIL", "").lower() in ("1", "true", "on"):
        print("[DBG]", url)
        print("      image_url:", image_url)
        print("      address  :", address)
        print("      access   :", access)
        print("      layout   :", layout)
        print("      area     :", area)

    return {
        "image_url": image_url,
        "address": address,
        "layout": layout,   # 例: "2LDK・3LDK"
        "area": area,       # 例: "56.63m2～68.38m2"
        "access": access,
    }


# ==============================
# gooトップ → 物件リンク → タイトル整形
# ==============================

def _normalize_name_from_title(title: str) -> str:
    """
    gooのtitleから余計な尾部を除去。
    """
    t = title.strip()
    t = re.sub(r"^【goo住宅・不動産】", "", t)
    t = re.sub(r"（価格・間取り）\s*物件情報｜新築マンション・分譲マンション.*$", "", t)
    t = re.sub(r"\s*物件情報｜新築マンション・分譲マンション.*$", "", t)
    t = re.sub(r"[（）\s]+$", "", t)
    return t.strip()

def fetch_property_infos():
    options = Options()
    options.binary_location = "/usr/bin/google-chrome"
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    # UA固定（A/B差異の回避に有効な場合あり）
    options.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36')

    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    driver.get("https://house.goo.ne.jp/buy/bm/")
    time.sleep(5)

    elems = driver.find_elements(By.CSS_SELECTOR, "ul.bxslider li a")
    urls = [a.get_attribute("href") for a in elems if a.get_attribute("href")]

    properties = []
    seen_names = set()

    for url in urls:
        try:
            driver.get(url)
            time.sleep(1)
            title = driver.title or ""
            name = _normalize_name_from_title(title)
            if not name or "goo住宅・不動産" in name or name in seen_names:
                continue
            detail = fetch_property_details(url, driver)
            properties.append({
                'name': name,
                'detail_url': url,
                **detail
            })
            seen_names.add(name)
        except Exception as e:
            print("❌ タイトル/詳細取得失敗:", e)

    driver.quit()
    print(f"✅ 取得済み物件: {len(properties)} 件")
    for p in properties:
        print("・", p['name'])
    return properties


# === 6. Google検索で公式URLを取得（リトライ付き）===
def get_official_url(query):
    search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}&num=1"
    for attempt in range(3):
        try:
            res = requests.get(search_url)
            if res.status_code == 429:
                wait = 10
                print(f"⚠️ API制限（429）: {wait}秒待機して再試行... ({attempt + 1}/3)")
                time.sleep(wait)
                continue
            res.raise_for_status()
            items = res.json().get('items', [])
            for item in items:
                link = item.get('link', '')
                if any(domain in link for domain in ['.co.jp', '.jp']) and 'suumo' not in link:
                    return link
            return items[0]['link'] if items else ''
        except Exception as e:
            print("検索エラー:", e)
            return ''
    return ''


# === 7. スプレッドシートへ記載（A列から固定10列, RAW, 改行/タブ除去）===
def write_to_sheet(properties, cred_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    existing = sheet.col_values(2)[1:]  # B列: 物件名（ヘッダ除く）
    today = datetime.now().strftime('%Y/%m/%d')
    new_count = 0

    rows_to_append = []  # まとめて追加

    for p in properties:
        name = p['name']

        # デバッグ（必要時のみ）
        if os.getenv("DEBUG_ROW", "").lower() in ("1", "true", "on"):
            print("[DBG ROW]", name, p.get('layout',''), p.get('area',''))

        if name in existing:
            print(f"⏭️ スキップ（重複）: {name}")
            continue

        manshon_url = f"https://www.e-mansion.co.jp/bbs/search/{requests.utils.quote(name)}"
        google_url = f"https://www.google.com/search?q={requests.utils.quote(name)}"
        official_url = get_official_url(name)

        # 最終整形（形式を保証）
        layout = _sanitize_cell(_normalize_layout(p.get('layout', '')))
        area   = _sanitize_cell(_normalize_area(p.get('area', '')))

        row = [
            today,                                 # A: 取得日付
            _sanitize_cell(name),                  # B: 物件名
            _sanitize_cell(manshon_url),           # C: マンコミ検索URL
            _sanitize_cell(google_url),            # D: Google検索URL
            _sanitize_cell(official_url),          # E: 公式URL
            _sanitize_cell(p.get('image_url','')), # F: 画像URL
            _sanitize_cell(p.get('address','')),   # G: 住所
            layout,                                # H: 間取り（例: 2LDK・3LDK）
            area,                                  # I: 専有面積（例: 56.63m2～68.38m2）
            _sanitize_cell(p.get('access','')),    # J: 交通
        ]
        # 必ず10列（A～J）に揃える
        row += [""] * (10 - len(row))
        rows_to_append.append(row)
        new_count += 1

    if rows_to_append:
        # A列から順に RAW で追記（自動解釈を抑止、列ズレ防止）
        sheet.append_rows(rows_to_append, value_input_option='RAW', table_range="A1:J1")

    print(f"✅ 新規追加: {new_count} 件")


# === 8. メイン処理 ===
def main():
    try:
        cred = create_credentials_file()
        properties = fetch_property_infos()
        if not properties:
            print("❌ 物件が取得できませんでした。")
            return
        write_to_sheet(properties, cred)
    except Exception:
        print("❌ 実行時エラー:")
        traceback.print_exc()


if __name__ == "__main__":
    main()
