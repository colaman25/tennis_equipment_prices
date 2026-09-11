import numpy as np
import pandas as pd
import requests
import time
import logging
import os
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from selenium import webdriver

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

import config

from pymongo import MongoClient

MONGOCNX = config.mongo_cnx_string
MONGODB_NAME = 'tennis_prod'
MONGODB_COLLECTION = 'tennisprod'
SOURCE = 'Direct Tennis UK'

# Direct Tennis doesn't expose a brand field on its category listing pages
# (only per-product detail pages do, via JSON-LD). Rather than pay for an
# extra page load per product, brand is best-effort guessed by matching the
# start of the product name against this list, harvested from the site's
# own brand logos and brand menu links.
KNOWN_BRANDS = sorted([
    '2XU', 'Adidas', 'Apacs', 'Ashaway', 'Asics', 'Babolat', 'Bullpadel', 'Carlton',
    'Craft', 'Dunlop', 'Ellesse', 'Fitness Mad', 'Forza', 'Head', 'K-Swiss', 'Kanso',
    'Karakal', 'Li-Ning', 'Lotto', 'Luxilon', 'McDavid', 'Merrell', 'Mizuno', 'Naked',
    'New Balance', 'Nike', 'Odlo', 'Orthosole', 'Osaka', 'Prince', 'Ronhill', 'Skechers',
    'Skins', 'Slazenger', 'Smell Well', 'Sorbothane', 'Tecnifibre', 'Under Armour',
    'Victor', 'Vulkan', 'Wilson', 'Yehlex', 'Yonex',
], key=len, reverse=True)


def guessBrand(productName):
    nameLower = productName.lower()
    for brand in KNOWN_BRANDS:
        brandLower = brand.lower()
        if nameLower == brandLower or nameLower.startswith(brandLower + ' '):
            return brand
    return ''


def stripBrand(productName, brand):
    if not brand or productName.lower() == brand.lower():
        return productName
    if productName.lower().startswith(brand.lower() + ' '):
        return productName[len(brand):].strip()
    return productName


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


def runCrawler():
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument('--remote-debugging-pipe')
    chrome_options.add_argument("enable-automation")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--dns-prefetch-disable")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--ignore-ssl-errors")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument("enable-features=NetworkServiceInProcess")
    chrome_options.add_argument("disable-features=NetworkService")

    driver = webdriver.Chrome(options=chrome_options)

    driver.get("https://www.directtennis.co.uk/tennis-rackets")

    df = pd.DataFrame(columns=['Product Name', 'Product Price', 'Old Price', 'Product Cat', 'Time Added', 'Source', 'Product URL', 'Brand'])
    menuitem = driver.find_elements(By.XPATH, '//a[@class="menu-item-title"]')

    for b in range(len(menuitem)):
        menuitem = driver.find_elements(By.XPATH, '//a[@class="menu-item-title"]')
        topbar = driver.find_elements(By.XPATH, '//a[@class="megamenu-header"]')
        menuitem_label = menuitem[b].text
        scrapetime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        for a in range(len(topbar)):
            try:
                hover = ActionChains(driver).move_to_element(topbar[a])
                hover.perform()
                time.sleep(1)

                menuitem[b].click()
                time.sleep(1)
                break
            except:
                continue

        # menuitem_label is the specific brand/collection/filter link just clicked
        # (e.g. "Babolat"), not the actual product category. The page's own
        # breadcrumb ("Home > Tennis Rackets > Babolat") has the real category
        # as its second entry, so prefer that when the click succeeded.
        breadcrumb_category = driver.find_elements(By.XPATH, '//div[@class="breadcrumb-content"]//ul/li[2]/a')
        prodcat = breadcrumb_category[0].text if breadcrumb_category else menuitem_label

        product_names = []
        product_prices = []
        old_prices = []
        product_category = []
        time_added = []
        product_urls = []

        while True:
            # Extract every field from the same per-product container instead of
            # separate independent queries (name/price/old-price/url) - if any one
            # product's card is missing a sub-element (e.g. no old-price span),
            # independent queries return mismatched counts and the lists drift out
            # of alignment, crashing pd.DataFrame() with "All arrays must be of the
            # same length". Keying everything off one row guarantees they stay in sync.
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            rows = soup.select('div.col-12.product-row')

            for row in rows:
                name_tag = row.select_one('div.block-with-text')
                price_tag = row.select_one('span.new-price')
                old_price_tag = row.select_one('span.old-price')
                url_tag = row.select_one('a.product-name')

                product_names.append(name_tag.get_text(strip=True) if name_tag else '')
                product_prices.append(price_tag.get_text(strip=True) if price_tag else '')
                old_prices.append(old_price_tag.get_text(strip=True) if old_price_tag else '')
                product_category.append(prodcat)
                time_added.append(scrapetime)
                product_urls.append(urljoin(driver.current_url, url_tag['href']) if url_tag and url_tag.has_attr('href') else '')

            try:
                next_button = driver.find_element(By.ID, 'btnNextTop')
                next_button.click()
                time.sleep(3)
            except:
                break

        product_brands = [guessBrand(name) for name in product_names]
        product_names = [stripBrand(name, brand) for name, brand in zip(product_names, product_brands)]

        dict = {'Product Name': product_names, 'Product Price': product_prices, 'Old Price': old_prices,
                'Product Cat': product_category, 'Time Added': time_added, 'Product URL': product_urls,
                'Brand': product_brands}
        df1 = pd.DataFrame(dict)
        df1['Source'] = SOURCE
        data_dict = df1.to_dict(orient="records")

        if data_dict:
            atlas_client = AtlasClient(MONGOCNX, MONGODB_NAME)
            atlas_client.insert(MONGODB_COLLECTION, data_dict)

        df = pd.concat([df, df1])
        print(f"{b + 1} of {len(menuitem)} items scraped....")

        driver.execute_script("window.scrollTo(0, 0)")

    driver.quit()
    df.to_csv('../data/direct_tennis_products.csv', index=False)


if __name__ == '__main__':
    runCrawler()