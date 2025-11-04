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
# 追加：詳細抽出ヘルパー（<td>を丸ごと→不要除去→整形）
# ==============================

def _sanitize_cell(x: str) -> str:
    """セル内のタブ/改行/連続空白を除去して安定化。"""
    if x is None:
        return ""
    s = re.sub(r"[\t\r\n]+", " ", str(x))
    s = s.replace("\u00A0", " ").replace("\u200B", "")  # NBSP / ゼロ幅
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

def _clean_td_text(td):
    """<td> 内の不要要素（リンク等）を削除してテキスト化。"""
    for e in td.select('span.link-s, .link-s, a'):
        e.decompose()
    text = td.get_text(" ", strip=True)
    text = text.replace("\u00A0", " ").replace("\u200B", "")
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

def _normalize_area_to_tsubo_m2_display(a: float) -> str:
    """数値文字列→'xx.xx' or 'xx' に整形した上で '㎡' を付ける。"""
    if a is None:
        return ""
    # 小数は最大2桁、末尾の0は落とす
    s = f"{a:.2f}"
    s = s.rstrip("0").rstrip(".")
    return f"{s}㎡"

def _normalize_area_from_td(raw: str) -> str:
    """
    専有面積を '44.83㎡～74.57㎡' 形式に統一。
    ㎡/m²/m^2/m２/m 2/m を中間表現 m2 に寄せてから抽出し、最終は必ず「㎡」で出力。
    """
    def cleanup_to_m2(s: str) -> str:
        s = s or ""
        s = s.replace("\u00A0", " ").replace("\u200B", "")
        # 単位ゆれ → m2 へ寄せる
        s = s.replace("㎡", "m2")
        s = s.replace("m²", "m2")
        s = s.replace("m^2", "m2")
        s = re.sub(r"m\s*２", "m2", s)     # m２ → m2
        s = re.sub(r"m\s*2\b", "m2", s)    # m 2 / m\t2 / m\n2
        s = re.sub(r"\bm\s*$", "m2", s)    # 末尾 m → m2
        # 全角数字→半角、区切り除去
        s = s.translate(str.maketrans("０１２３４５６７８９．，－", "0123456789.,-")).replace(",", "")
        # 余計な先頭記号・注釈・説明語
        s = re.sub(r"^[：:/\-\s]+", "", s)
        s = re.sub(r"\s*(超|平均|前後|程度)", "", s)
        s = re.sub(r"[（(].*?[)）]", "", s)
        return s.strip()

    txt = cleanup_to_m2(raw)
    wave = r"(?:～|~)"

    # 明示レンジ
    m = re.search(rf"(\d+(?:\.\d+)?)\s*m2\s*{wave}\s*(\d+(?:\.\d+)?)\s*m2", txt)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return f"{_normalize_area_to_tsubo_m2_display(a)}～{_normalize_area_to_tsubo_m2_display(b)}"

    # m2の出現を全部拾って最小～最大
    nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*m2", txt)]
    if len(nums) >= 2:
        nums.sort()
        return f"{_normalize_area_to_tsubo_m2_display(nums[0])}～{_normalize_area_to_tsubo_m2_display(nums[-1])}"
    if len(nums) == 1:
        return _normalize_area_to_tsubo_m2_display(nums[0])

    return ""


def fetch_property_details(url, driver):
    """
    画像URL / 住所 / 交通 / 間取り（2LDK・3LDK） / 専有面積（xx.xx㎡～yy.yy㎡） / 総戸数
    を抽出して返す。
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

    # ラベル直の <td> を“丸ごと”取得してから整形
    address_raw = _get_td_by_label(soup, r"(住所|所在地)")
    access_raw  = _get_td_by_label(soup, r"交通")
    layout_raw  = _get_td_by_label(soup, r"(間取り|間取)")
    area_raw    = _get_td_by_label(soup, r"専有面積")

    layout = _normalize_layout_from_td(layout_raw)
    area   = _normalize_area_from_td(area_raw)

    # ✅ 総戸数（ラベル表現ゆれ対応）
    total_units_raw = ""
    for tr in soup.select("table tr"):
        th = tr.find("th")
        td = tr.find("td")
        if th and td and re.search(r"総戸数", th.get_text()):
            total_units_raw = td.get_text(" ", strip=True)
            break

    return {
        "image_url": _sanitize_cell(image_url),
        "address": _sanitize_cell(address_raw),
        "layout": _sanitize_cell(layout),
        "area": _sanitize_cell(area),
        "access": _sanitize_cell(access_raw),
        "total_units": _sanitize_cell(total_units_raw),  # ← ✅ 追加分
    }



# ==============================
# gooトップ → 物件リンク → タイトル整形
# ==============================

def _normalize_name_from_title(title: str) -> str:
    """
    gooのtitleから余計な尾部を除去。
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
def _next_empty_row_in_col_a(sheet):
    """A列の次の空行番号（1始まり）を返す。A列が常に埋まる前提でシンプル・高速。"""
    col_a = sheet.col_values(1)  # A列
    return len(col_a) + 1  # 次の空行

def write_to_sheet(properties, cred_path):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    existing = sheet.col_values(2)[1:]  # B列: 物件名（ヘッダ除く）
    today = datetime.now().strftime('%Y/%m/%d')
    new_count = 0

    for p in properties:
        name = p['name']

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
            _sanitize_cell(p.get('area','')),        # I: 専有面積（例: 44.83㎡～74.57㎡）
            _sanitize_cell(p.get('access','')),      # J: 交通
        ]
        # 必ず10列（A～J）に揃える
        row += [""] * (10 - len(row))

        # ★ ここがポイント：A列の次の空行を計算して、明示的に A{r}:J{r} に書き込む
        r = _next_empty_row_in_col_a(sheet)
        sheet.update(f"A{r}:J{r}", [row], value_input_option='RAW')

        new_count += 1
        time.sleep(0.5)  # レート制御（必要に応じて調整）

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
