import time
import os
import random
import requests
import pandas as pd
from datetime import datetime

import config

from pymongo import MongoClient

MONGOCNX = config.mongo_cnx_string
MONGODB_NAME = 'tennis_prod'
MONGODB_COLLECTION = 'tennisprod'
SOURCE = 'Tennis Point'

# tennis-point.co.uk renders its category pages client-side via an Algolia-powered
# search widget rather than native Shopify collections (their own
# /collections/<x>/products.json endpoint returns no products). These are the
# app ID / search-only API key / index name the storefront's own JS uses to
# query Algolia directly, captured from the page's network requests.
ALGOLIA_APP_ID = 'PHLCFMEKOL'
ALGOLIA_API_KEY = '6daf5dd1db5063623b2115e970ce3077'
ALGOLIA_INDEX = 'tpo-uk-en'
# The regular /queries search endpoint hard-caps at 1000 results total
# (page * hitsPerPage <= 1000) - fine for Tennis Rackets (566) but not for
# Tennis Clothing (~5000) or Tennis Shoes (~1100). Algolia's own error message
# for that cap points to the /browse endpoint instead, which supports
# cursor-based pagination with no such limit, using the same search key.
ALGOLIA_BROWSE_URL = f'https://{ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/browse'
ALGOLIA_HEADERS = {
    'Content-Type': 'application/json',
    'Referer': 'https://www.tennis-point.co.uk/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
}
HITS_PER_PAGE = 1000
MAX_PROXY_RETRIES = 5

# A crawl that returns far fewer products than usual almost always means the
# site changed and a selector silently stopped matching, not that inventory
# actually dropped - fail loudly instead of quietly writing a near-empty
# run to Mongo, so it surfaces as a GitHub Actions failure email.
MIN_PRODUCTS_THRESHOLD = 50

CATEGORIES = {
    'Tennis Rackets': 'tennis-rackets',
    'Tennis Clothing': 'tennis-clothing',
    'Tennis Shoes': 'tennis-shoes',
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


def formatPrice(value):
    if value is None:
        return ''
    return f'£{value:.2f}'


def browseAlgolia(session, categoryFilter, cursor):
    params = f'x-algolia-api-key={ALGOLIA_API_KEY}&x-algolia-application-id={ALGOLIA_APP_ID}'
    payload = {'cursor': cursor} if cursor else {'filters': f'categories:"{categoryFilter}"', 'hitsPerPage': HITS_PER_PAGE}

    response = requestWithRetry(session, 'POST', f'{ALGOLIA_BROWSE_URL}?{params}',
                                 headers=ALGOLIA_HEADERS, json=payload, timeout=30)
    return response.json()


def scrapeCategory(session, prodcat, categoryFilter, scrapetime):
    product_names = []
    product_prices = []
    old_prices = []
    product_category = []
    time_added = []
    product_urls = []
    product_brands = []

    cursor = None
    while True:
        result = browseAlgolia(session, categoryFilter, cursor)
        hits = result.get('hits', [])
        if not hits:
            break

        for hit in hits:
            prices = hit.get('prices', {})
            guest_price = prices.get('guest', {}).get('1')
            rrp_price = prices.get('uvp', {}).get('1')
            has_discount = guest_price is not None and rrp_price is not None and rrp_price > guest_price

            product_names.append(hit.get('title', ''))
            product_prices.append(formatPrice(guest_price))
            old_prices.append(formatPrice(rrp_price) if has_discount else '')
            product_category.append(prodcat)
            time_added.append(scrapetime)
            product_urls.append(f'https://www.tennis-point.co.uk/products/{hit.get("handle", "")}')
            product_brands.append(hit.get('brand', ''))

        cursor = result.get('cursor')
        if not cursor:
            break
        time.sleep(0.5)

    return product_names, product_prices, old_prices, product_category, time_added, product_urls, product_brands


def runCrawler():
    session = requests.Session()

    df = pd.DataFrame(columns=['Product Name', 'Product Price', 'Old Price', 'Product Cat', 'Time Added', 'Source', 'Product URL', 'Brand'])
    atlas_client = AtlasClient(MONGOCNX, MONGODB_NAME)

    for prodcat, categoryFilter in CATEGORIES.items():
        scrapetime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        names, prices, olds, cats, times, urls, brands = scrapeCategory(session, prodcat, categoryFilter, scrapetime)

        dict = {'Product Name': names, 'Product Price': prices, 'Old Price': olds,
                'Product Cat': cats, 'Time Added': times, 'Product URL': urls, 'Brand': brands}
        df1 = pd.DataFrame(dict)
        df1['Source'] = SOURCE
        data_dict = df1.to_dict(orient="records")

        if data_dict:
            atlas_client.insert(MONGODB_COLLECTION, data_dict)

        df = pd.concat([df, df1])
        print(f"{prodcat}: {len(df1)} products scraped....")

    output_dir = '../data'
    os.makedirs(output_dir, exist_ok=True)
    df.to_csv(os.path.join(output_dir, 'tennispoint_products.csv'), index=False)

    if len(df) < MIN_PRODUCTS_THRESHOLD:
        raise RuntimeError(
            f'{SOURCE}: only {len(df)} products scraped in total, expected at '
            f'least {MIN_PRODUCTS_THRESHOLD} - likely a broken selector, not a real inventory drop.'
        )


if __name__ == '__main__':
    runCrawler()
