print("✅ scrape.py 起動確認済み")

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

options = Options()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.binary_location = "/usr/bin/google-chrome-stable"

try:
    driver = webdriver.Chrome(options=options)
    print("✅ Chrome 起動成功")
    driver.get("https://house.goo.ne.jp/buy/bm/")
    print("✅ ページ読み込み成功")
    print(driver.page_source[:1000])  # HTML先頭1000文字だけ表示
    driver.quit()
except Exception as e:
    print("❌ エラー発生:")
    print(e)
