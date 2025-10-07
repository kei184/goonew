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
# 追加：詳細抽出のヘルパー（<td>を丸ごと→不要除去→整形）
# ==============================

def _sanitize_cell(x: str) -> str:
    """セル内のタブ/改行/連続空白を除去して安定化。"""
    if x is None:
        return ""
    s = re.sub(r"[\t\r\n]+", " ", str(x))
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

def _clean_td_text(td):
    """<td> 内の不要要素（リンク等）を削除してテキスト化。"""
    # 不要なリンク・装飾を削除
    for e in td.select('span.link-s, .link-s, a'):
        e.decompose()
    text = td.get_text(" ", strip=True)
    text = re.sub(r"\s{2,}", " ", text)
    return text

def _get_td_by_label(soup, th_label_regex: str) -> str:
    """与えたラベル(th)に対応する次の<td>をテキストで返す。見つからなければ空。"""
    th = soup.find('th', string=re.compile(rf"^\s*{th_label_regex}\s*$"))
    if not th:
        return ""
    td = th.find_next_sibling('td')
    if not td:
        return ""
    return _clean_td_text(td)

def _normalize_layout_from_td(raw: str) -> str:
    """
    間取りを '2LDK・3LDK' のように統一（順序維持＆重複除去）。
    1K/1DK/1LDK/1R/… を抽出し、半角化して '・' 連結。
    """
    txt = (raw or "").replace("　", " ")
    hits = re.findall(r"([0-9０-９]+)\s*(LDK|DK|K|R)", txt, flags=re.I)
    out, seen = [], set()
    for num, typ in hits:
        num = num.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        typ = typ.upper()
        key = f"{num}{typ}"
        if key not in seen:
            seen.add(key)
            out.append(key)
    if not out and "ワンルーム" in txt:
        return "ワンルーム"
    return "・".join(out)

def _normalize_area_from_td(raw: str) -> str:
    """
    専有面積を '44.83㎡～74.57㎡' 形式に統一。
    ㎡/m²/m^2/m２/m 2/m をすべて吸収し、結果は必ず「㎡」で出力。
    """
    import re

    def cleanup(s: str) -> str:
        s = s or ""
        # NBSP/ゼロ幅など
        s = s.replace("\u00A0", " ").replace("\u200B", "")
        # 単位ゆれ → 中間表現 m2 に寄せてから抽出
        s = s.replace("㎡", "m2")
        s = s.replace("m²", "m2")          # Unicodeの²
        s = s.replace("m^2", "m2")
        s = re.sub(r"m\s*２", "m2", s)     # 全角 ２
        s = re.sub(r"m\s*2\b", "m2", s)    # m 2 / m\t2 / m\n2
        s = re.sub(r"\bm\s*$", "m2", s)    # 末尾が m のみ
        # 全角数字 → 半角、カンマ除去
        s = s.translate(str.maketrans("０１２３４５６７８９．，－", "0123456789.,-")).replace(",", "")
        # 先頭の記号/不要語/注釈は除去
        s = re.sub(r"^[：:/\-\s]+", "", s)
        s = re.sub(r"\s*(超|平均|前後|程度)", "", s)
        s = re.sub(r"[（(].*?[)）]", "", s)  # 括弧注釈を削除
        return s.strip()

    def fmt(num_str: str) -> str:
        # "44.830" → "44.83"、"70.0" → "70"
        try:
            v = float(num_str)
            out = f"{v:.2f}" if "." in num_str else f"{v:g}"
            # 末尾ゼロと小数点の整理
            out = out.rstrip("0").rstrip(".")
            return f"{out}㎡"
        except Exception:
            return f"{num_str}㎡"

    txt = cleanup(raw)
    wave = r"(?:～|~)"

    # 1) 明示レンジ（～/~ どちらでも）
    m = re.search(rf"(\d+(?:\.\d+)?)\s*m2\s*{wave}\s*(\d+(?:\.\d+)?)\s*m2", txt)
    if m:
        a, b = m.group(1), m.group(2)
        return f"{fmt(a)}～{fmt(b)}"

    # 2) m2 の値を全部拾い、2つ以上なら最小～最大
    nums = re.findall(r"(\d+(?:\.\d+)?)\s*m2", txt)
    if len(nums) >= 2:
        vals = sorted(float(n) for n in nums)
        return f"{fmt(str(vals[0]))}～{fmt(str(vals[-1]))}"
    if len(nums) == 1:
        return fmt(nums[0])

    return ""

def fetch_property_details(url, driver):
    """
    画像URL（?700優先）/ 住所 / 交通 / 間取り（2LDK・3LDK）/ 専有面積（44.83m2～74.57m2形式）
    を抽出して返す。<th>の次の<td>を丸ごと→不要削除→整形。
    """
    driver.get(url)
    time.sleep(1.2)  # JS描画の安定待ち

    soup = BeautifulSoup(driver.page_source, "html.parser")

    # 画像URL：a.image-popup 最優先 → img[src^=https://img.house.goo.ne.jp] を ?700 に寄せる
    image_url = ""
    a_img = soup.select_one('a.image-popup[href^="https://img.house.goo.ne.jp/"]')
    if a_img and a_img.has_attr("href"):
        image_url = a_img["href"]
    else:
        img = soup.find("img", src=re.compile(r"^https://img\.house\.goo\.ne\.jp/"))
        if img and img.has_attr("src"):
            image_url = re.sub(r"\?500\b", "?700", img["src"])

    # --- ラベル直の <td> を“丸ごと”取得してから整形 ---
    address_raw = _get_td_by_label(soup, r"(住所|所在地)")
    access_raw  = _get_td_by_label(soup, r"交通")
    layout_raw  = _get_td_by_label(soup, r"(間取り|間取)")
    area_raw    = _get_td_by_label(soup, r"専有面積")

    layout = _normalize_layout_from_td(layout_raw)
    area   = _normalize_area_from_td(area_raw)

    return {
        "image_url": image_url,
        "address": _sanitize_cell(address_raw),
        "layout": _sanitize_cell(layout),   # 例: 2LDK・3LDK
        "area":   _sanitize_cell(area),     # 例: 44.83m2～74.57m2
        "access": _sanitize_cell(access_raw),
    }


# ==============================
# gooトップ → 物件リンク → タイトル整形
# ==============================

def _normalize_name_from_title(title: str) -> str:
    """
    gooのtitleから余計な尾部を除去。
    例:
      "【goo住宅・不動産】ザ・パークハウス 東中野プレイス（価格・間取り） 物件情報｜新築マンション・分譲マンション"
       → "ザ・パークハウス 東中野プレイス"
    """
    t = (title or "").strip()
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

    rows_to_append = []  # まとめて追加（ズレ防止 & 高速）

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

        row = [
            today,                                   # A: 取得日付
            _sanitize_cell(name),                    # B: 物件名
            _sanitize_cell(manshon_url),             # C: マンコミ検索URL
            _sanitize_cell(google_url),              # D: Google検索URL
            _sanitize_cell(official_url),            # E: 公式URL
            _sanitize_cell(p.get('image_url','')),   # F: 画像URL
            _sanitize_cell(p.get('address','')),     # G: 住所
            _sanitize_cell(p.get('layout','')),      # H: 間取り（例: 2LDK・3LDK）
            _sanitize_cell(p.get('area','')),        # I: 専有面積（例: 44.83m2～74.57m2）
            _sanitize_cell(p.get('access','')),      # J: 交通
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
