import os
import time
from datetime import datetime

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

import config

from pymongo import MongoClient

MONGOCNX = config.mongo_cnx_string
MONGODB_NAME = 'tennis_prod'
MONGODB_COLLECTION = 'tennisprod'
SOURCE = 'Tennis Warehouse Europe'
BASE_URL = 'https://www.tenniswarehouse-europe.com'

# There's no flat "all rackets"/"all clothing" listing - the site organises
# everything by brand, so we crawl each brand's own catalogue page under each
# category. Every product across every collection for that brand is already
# rendered on one page (confirmed by hand - e.g. clicking "Pure Drive" on the
# Babolat page is just a same-page filter anchor, not a separate page), so no
# further pagination/collection-drilling is needed once past the brand page.
CATEGORIES = {
    'Tennis Rackets': [
        '/catpage-BABOLATRAC-EN.html', '/catpage-WILSONRACS-EN.html', '/catpage-HEADRAC-EN.html',
        '/catpage-PRINCERAC-EN.html', '/catpage-YONEXRAC-EN.html', '/catpage-DUNLOPRAC-EN.html',
        '/catpage-PROKERAC-EN.html', '/catpage-SOLINCRAC-EN.html', '/catpage-TECRAC-EN.html',
        '/catpage-VOLRAC-EN.html',
    ],
    'Tennis Clothing': [
        # Men's Apparel
        '/catpage.html?ccode=MANIKE', '/catpage-MAADIDAS-EN.html', '/catpage-ASICSMAPP-EN.html',
        '/catpage-MABABOLAT-EN.html', '/catpage-BBMAPP-EN.html', '/catpage-BOASTMA-EN.html',
        '/catpage-MABOSS-EN.html', '/catpage-BUPAAPP-EN.html', '/catpage-MRACEPSS15-EN.html',
        '/catpage-DUMAPP-EN.html', '/catpage-MAFILA-EN.html', '/catpage-FLOKAP-EN.html',
        '/catpage-HMENAPP-EN.html', '/catpage-JMAPP-EN.html', '/catpage-KSWMAPP-EN.html',
        '/catpage-MALACOSTE-EN.html', '/catpage-MALECOQ-EN.html', '/catpage-LOTTOMAPP-EN.html',
        '/catpage-MDPMAPP-EN.html', '/catpage-MIZUNOMA-EN.html', '/catpage-MAMOURA-EN.html',
        '/catpage-NKFPP.html', '/catpage-NOXMAPP-EN.html', '/catpage-ONMAP-EN.html',
        '/catpage-PENGUINMA-EN.html', '/catpage-MAPRINCE-EN.html', '/catpage-RGMAPP-EN.html',
        '/catpage-MATF-EN.html', '/catpage-TEWAAPP-EN.html', '/catpage-UAAPP-EN.html',
        '/catpage-MAWILSON-EN.html', '/catpage-MAYONEX-EN.html',
        # Women's Apparel
        '/catpage-WANIKE-EN.html', '/catpage-WAADIDAS-EN.html', '/catpage-ASICSWAPP-EN.html',
        '/catpage-WABABOLAT-EN.html', '/catpage-BBWA-EN.html', '/catpage-BBWAPP-EN.html',
        '/catpage-BOASTWA-EN.html', '/catpage-BUPAAPPW-EN.html', '/catpage-DWA-EN.html',
        '/catpage-WAFILA-EN.html', '/catpage-HEAWMSAPP-EN.html', '/catpage-JWAPP-EN.html',
        '/catpage-KSWWAPP-EN.html', '/catpage-WALACOSTE-EN.html', '/catpage-LOTTOWMAPP-EN.html',
        '/catpage-LILWA-EN.html', '/catpage-WAMOURA-EN.html', '/catpage-NOXWAPP-EN.html',
        '/catpage-ONWAP-EN.html', '/catpage-PENGUINWA-EN.html', '/catpage-RGWAPP-EN.html',
        '/catpage-WATF-EN.html', '/catpage-TWEWAPP-EN.html', '/catpage-UAWAPP-EN.html',
        '/catpage-WAWILSON-EN.html', '/catpage-WAYONEX-EN.html',
    ],
    'Tennis Shoes': [
        # Men's Shoes
        '/catpage-MSNIKE-EN.html', '/catpage-MSADIDAS-EN.html', '/catpage-MSASICS-EN.html',
        '/catpage-MSBABOLAT-EN.html', '/catpage-BULLMENSH-EN.html', '/catpage-MSHEAD-EN.html',
        '/catpage-MSJOMA-EN.html', '/catpage-MSKSWISS-EN.html', '/catpage-LMSH-EN.html',
        '/catpage-MSLOTTO-EN.html', '/catpage-MIZUMT-EN.html', '/catpage-MSNOXPAD-EN.html',
        '/catpage-ONMTS-EN.html', '/catpage-MSWILSON-EN.html', '/catpage-MSYONEX-EN.html',
        # Women's Shoes
        '/catpage-WSNIKE-EN.html', '/catpage-WSADIDAS-EN.html', '/catpage-ASICWSHOE-EN.html',
        '/catpage-WSBABOLAT-EN.html', '/catpage-BULLWOMSH-EN.html', '/catpage-WSHEAD-EN.html',
        '/catpage-JOMAWS-EN.html', '/catpage-KSWS-EN.html', '/catpage-LWSH-EN.html',
        '/catpage-WSLOTTO-EN.html', '/catpage-MIZUWT-EN.html', '/catpage-WSNOXPAD-EN.html',
        '/catpage-ONWTS-EN.html', '/catpage-WSWILSON-EN.html', '/catpage-WSYONEX-EN.html',
    ],
}

# Suffixes the site appends to the GTM brand attribute depending on product
# type (e.g. "Babolat Tennis", "Babolat Racquets") - stripped to get a plain
# brand name we can then strip off the front of the product name too.
BRAND_SUFFIXES = [' Tennis', ' Racquets', ' Racket', ' Apparel', ' Shoes', ' Padel']

PRODUCT_LOAD_TIMEOUT = 15
PRODUCT_LOAD_POLL_INTERVAL = 0.5


class AtlasClient ():

   def __init__ (self, mongocnx, dbname):
       self.mongodb_client = MongoClient(mongocnx)
       self.database = self.mongodb_client[dbname]

   def ping (self):
       self.mongodb_client.admin.command('ping')

   def get_collection (self, collection_name):
       collection = self.database[collection_name]
       return collection

   def find (self, collection_name, filter = {}, limit=0):
       collection = self.database[collection_name]
       items = list(collection.find(filter=filter, limit=limit))
       return items

   def insert (self, collection_name, data = {}):
       collection = self.database[collection_name]
       x = collection.insert_many(data)


def cleanBrand(rawBrand):
    brand = rawBrand.strip()
    for suffix in BRAND_SUFFIXES:
        if brand.endswith(suffix):
            return brand[:-len(suffix)].strip()
    return brand


def stripBrand(productName, brand):
    if not brand or productName.lower() == brand.lower():
        return productName
    if productName.lower().startswith(brand.lower() + ' '):
        return productName[len(brand):].strip()
    return productName


def formatEuroPrice(text):
    # Prices are shown European-style ("189,90 €") - the shared parsePrice()
    # in data.py used by the Streamlit app assumes a period decimal, so this
    # must be normalised here, not left for later, or prices parse as
    # nonsense (e.g. "189,90" -> "18990" after naive comma-stripping).
    if not text:
        return ''
    return text.strip().replace(',', '.').replace('\xa0', ' ')


def dismissLanguageAndVatModal(driver):
    try:
        selects = driver.find_elements(By.TAG_NAME, 'select')
        for s in selects:
            if s.get_attribute('name') == 'lang' and s.get_attribute('aria-label') == 'Preferred Language':
                Select(s).select_by_value('en')
            if s.get_attribute('name') == 'vat' and s.get_attribute('aria-label') == 'Country VAT':
                Select(s).select_by_value('UK')
        driver.find_element(By.XPATH, '//button[contains(text(), "Set Selections")]').click()
        time.sleep(2)
    except Exception:
        pass

    try:
        driver.find_element(By.XPATH, '//button[contains(text(), "Reject All")]').click()
        time.sleep(1)
    except Exception:
        pass


def waitForProductsToLoad(driver):
    deadline = time.time() + PRODUCT_LOAD_TIMEOUT
    lastCount = -1
    while time.time() < deadline:
        count = len(driver.find_elements(By.CSS_SELECTOR, '.cattable-wrap-cell'))
        if count > 0 and count == lastCount:
            return
        lastCount = count
        time.sleep(PRODUCT_LOAD_POLL_INTERVAL)


def scrapeBrandPage(driver, path, prodcat, scrapetime):
    product_names = []
    product_prices = []
    old_prices = []
    product_category = []
    time_added = []
    product_urls = []
    product_brands = []

    driver.get(BASE_URL + path)
    waitForProductsToLoad(driver)

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    cards = soup.select('.cattable-wrap-cell')

    for card in cards:
        name_tag = card.select_one('.cattable-wrap-cell-info-name')
        price_tag = card.select_one('.cattable-wrap-cell-info-price')
        old_price_tag = card.select_one('.cattable-wrap-cell-info-price-msrp .is-crossout')
        url_tag = card.select_one('a.cattable-wrap-cell-info')

        rawName = name_tag.get_text(strip=True) if name_tag else ''
        rawBrand = card.get('data-gtm_impression_brand', '')
        brand = cleanBrand(rawBrand)
        name = stripBrand(rawName, brand)

        price_text = ''
        if price_tag:
            first_span = price_tag.find('span')
            if first_span:
                price_text = first_span.get_text(strip=True)

        product_names.append(name)
        product_brands.append(brand)
        product_prices.append(formatEuroPrice(price_text))
        old_prices.append(formatEuroPrice(old_price_tag.get_text(strip=True)) if old_price_tag else '')
        product_category.append(prodcat)
        time_added.append(scrapetime)
        product_urls.append(url_tag.get('href', '').strip() if url_tag else '')

    return product_names, product_prices, old_prices, product_category, time_added, product_urls, product_brands


def runCrawler():
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--dns-prefetch-disable")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--ignore-ssl-errors")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })

    # The Language & VAT modal only needs dismissing once - it persists across
    # page navigations within the same browser session (confirmed by hand).
    driver.get(BASE_URL + '/TennisRackets.html')
    time.sleep(4)
    dismissLanguageAndVatModal(driver)

    df = pd.DataFrame(columns=['Product Name', 'Product Price', 'Old Price', 'Product Cat', 'Time Added', 'Source', 'Product URL', 'Brand'])
    atlas_client = AtlasClient(MONGOCNX, MONGODB_NAME)

    for prodcat, paths in CATEGORIES.items():
        scrapetime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        product_names = []
        product_prices = []
        old_prices = []
        product_category = []
        time_added = []
        product_urls = []
        product_brands = []

        for path in paths:
            names, prices, olds, cats, times, purls, brands = scrapeBrandPage(driver, path, prodcat, scrapetime)
            product_names.extend(names)
            product_prices.extend(prices)
            old_prices.extend(olds)
            product_category.extend(cats)
            time_added.extend(times)
            product_urls.extend(purls)
            product_brands.extend(brands)
            time.sleep(1)

        dict = {'Product Name': product_names, 'Product Price': product_prices, 'Old Price': old_prices,
                'Product Cat': product_category, 'Time Added': time_added, 'Product URL': product_urls,
                'Brand': product_brands}
        df1 = pd.DataFrame(dict)
        df1['Source'] = SOURCE
        data_dict = df1.to_dict(orient="records")

        if data_dict:
            atlas_client.insert(MONGODB_COLLECTION, data_dict)

        df = pd.concat([df, df1])
        print(f"{prodcat}: {len(df1)} products scraped....")

    driver.quit()

    output_dir = '../data'
    os.makedirs(output_dir, exist_ok=True)
    df.to_csv(os.path.join(output_dir, 'tenniswarehouseeurope_products.csv'), index=False)


if __name__ == '__main__':
    runCrawler()
