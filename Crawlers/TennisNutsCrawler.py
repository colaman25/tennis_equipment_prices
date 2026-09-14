import time
import os
import random
import requests
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup

import config

from pymongo import MongoClient

MONGOCNX = config.mongo_cnx_string
MONGODB_NAME = 'tennis_prod'
MONGODB_COLLECTION = 'tennisprod'
SOURCE = 'Tennis Nuts'

PRODUCTS_PER_PAGE = 100
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
}
MAX_PROXY_RETRIES = 5

# A crawl that returns far fewer products than usual almost always means the
# site changed and a selector silently stopped matching, not that inventory
# actually dropped - fail loudly instead of quietly writing a near-empty
# run to Mongo, so it surfaces as a GitHub Actions failure email.
MIN_PRODUCTS_THRESHOLD = 50

# "Tennis Clothing" has no single flat listing on the site - it's a hub page
# that links out to these gendered sub-listings, so we crawl all of them
# under the one "Tennis Clothing" category label.
CATEGORIES = {
    'Tennis Rackets': [
        'https://www.tennisnuts.com/shop/tennis/tennis-rackets.html',
    ],
    'Tennis Clothing': [
        'https://www.tennisnuts.com/shop/tennis/tennis-clothing/mens-tennis-clothing.html',
        'https://www.tennisnuts.com/shop/tennis/tennis-clothing/womens-tennis-clothing.html',
        'https://www.tennisnuts.com/shop/tennis/tennis-clothing/boys-tennis-clothing.html',
        'https://www.tennisnuts.com/shop/tennis/tennis-clothing/girls-tennis-clothing.html',
    ],
    'Tennis Shoes': [
        'https://www.tennisnuts.com/shop/tennis/shoes-socks.html',
    ],
}


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


def getProxies():
    if not config.proxy_urls:
        return None
    proxy = random.choice(config.proxy_urls)
    return {'http': proxy, 'https': proxy}


def requestWithRetry(session, method, url, **kwargs):
    # Individual proxies in the pool can be temporarily down/slow - retry with
    # a freshly picked proxy rather than letting one bad proxy kill the run.
    last_exception = None
    for attempt in range(MAX_PROXY_RETRIES):
        try:
            response = session.request(method, url, proxies=getProxies(), **kwargs)
            response.raise_for_status()
            return response
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            last_exception = e
            print(f"  proxy request failed ({type(e).__name__}), retrying ({attempt + 1}/{MAX_PROXY_RETRIES})...")
            time.sleep(1)
    raise last_exception


def extractBrandAndName(card, productName):
    brand_tag = card.select_one('img.brand-icon')
    if not brand_tag or not brand_tag.get('alt'):
        return '', productName

    # alt text follows "{Brand} {product title} at Tennisnuts.com", but the
    # product title embedded in it can differ slightly from the visible
    # productName (e.g. missing "(Frame Only)"/"(2025)" qualifiers), so a
    # straight substring removal can leave brand + a mangled leftover title.
    # Instead take the longest run of leading words shared with productName -
    # that's always exactly the brand, regardless of how the rest diverges.
    altWords = brand_tag['alt'].replace('at Tennisnuts.com', '').strip().split()
    nameWords = productName.split()
    matchCount = 0
    for altWord, nameWord in zip(altWords, nameWords):
        if altWord.lower() != nameWord.lower():
            break
        matchCount += 1

    brand = ' '.join(altWords[:matchCount])
    strippedName = ' '.join(nameWords[matchCount:])
    return brand, strippedName if strippedName else productName


def scrapeListingUrl(session, url, prodcat, scrapetime):
    product_names = []
    product_prices = []
    old_prices = []
    product_category = []
    time_added = []
    product_urls = []
    product_brands = []

    page = 1
    while True:
        response = requestWithRetry(session, 'GET', url,
                                     params={'page': page, 'products_per_page': PRODUCTS_PER_PAGE},
                                     headers=REQUEST_HEADERS, timeout=30)
        soup = BeautifulSoup(response.text, 'html.parser')

        cards = soup.select('article.product-card')
        if not cards:
            break

        for card in cards:
            name_tag = card.select_one('h2.name')
            price_tag = card.select_one('div.price')
            old_price_tag = card.select_one('div.rrp')
            url_tag = card.select_one('a')

            raw_name = name_tag.get_text(strip=True) if name_tag else ''
            brand, name = extractBrandAndName(card, raw_name)

            product_names.append(name)
            product_prices.append(price_tag.get_text(strip=True) if price_tag else '')
            old_prices.append(old_price_tag.get_text(strip=True) if old_price_tag else '')
            product_category.append(prodcat)
            time_added.append(scrapetime)
            product_urls.append(url_tag['href'] if url_tag else '')
            product_brands.append(brand)

        has_next_page = soup.select_one('link[rel="next"]') is not None
        if not has_next_page:
            break

        page += 1
        time.sleep(1)

    return product_names, product_prices, old_prices, product_category, time_added, product_urls, product_brands


def runCrawler():
    session = requests.Session()

    df = pd.DataFrame(columns=['Product Name', 'Product Price', 'Old Price', 'Product Cat', 'Time Added', 'Source', 'Product URL', 'Brand'])
    atlas_client = AtlasClient(MONGOCNX, MONGODB_NAME)

    for prodcat, urls in CATEGORIES.items():
        scrapetime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        product_names = []
        product_prices = []
        old_prices = []
        product_category = []
        time_added = []
        product_urls = []
        product_brands = []

        for url in urls:
            names, prices, olds, cats, times, purls, brands = scrapeListingUrl(session, url, prodcat, scrapetime)
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

    output_dir = '../data'
    os.makedirs(output_dir, exist_ok=True)
    df.to_csv(os.path.join(output_dir, 'tennisnuts_products.csv'), index=False)

    if len(df) < MIN_PRODUCTS_THRESHOLD:
        raise RuntimeError(
            f'{SOURCE}: only {len(df)} products scraped in total, expected at '
            f'least {MIN_PRODUCTS_THRESHOLD} - likely a broken selector, not a real inventory drop.'
        )


if __name__ == '__main__':
    runCrawler()
