import hashlib
import re

import pandas as pd
import streamlit as st
from pymongo import MongoClient

import config

MONGODB_NAME = 'tennis_prod'
MONGODB_COLLECTION = 'tennisprod'
MONGODB_MAPPING_COLLECTION = 'product_mapping'


def parsePrice(value):
    if not isinstance(value, str):
        return None
    match = re.search(r'[\d.]+', value.replace(',', ''))
    return float(match.group()) if match else None


def makeListingId(source, productUrl):
    # Stable id for one retailer's specific listing - same (Source, Product URL)
    # always hashes to the same id, so it stays consistent across re-crawls of
    # the same product even though each crawl inserts a fresh Mongo document.
    return hashlib.md5(f'{source}|{productUrl}'.encode()).hexdigest()[:12]


@st.cache_data(ttl=300)
def loadProductMapping():
    # listing_id -> product_id, produced offline by product_matcher.py. Not
    # every listing is necessarily in here yet (e.g. crawled after the last
    # matcher run), so callers should fall back to listing_id for anything missing.
    client = MongoClient(config.mongo_cnx_string)
    collection = client[MONGODB_NAME][MONGODB_MAPPING_COLLECTION]
    return {doc['listing_id']: doc['product_id']
            for doc in collection.find({}, {'_id': 0, 'listing_id': 1, 'product_id': 1})}


@st.cache_data(ttl=300)
def loadRawProducts():
    # Every crawl run inserts new rows rather than overwriting, so this is the
    # full historical price log - one row per (product, crawl date). The main
    # table dedupes this down to latest-only; the detail page needs the full
    # history to compute things like 52-week min/max.
    client = MongoClient(config.mongo_cnx_string)
    collection = client[MONGODB_NAME][MONGODB_COLLECTION]
    df = pd.DataFrame(list(collection.find({}, {'_id': 0})))
    df['Price (numeric)'] = df['Product Price'].apply(parsePrice)
    df['Time Added'] = pd.to_datetime(df['Time Added'])
    df['listing_id'] = df.apply(lambda r: makeListingId(r['Source'], r['Product URL']), axis=1)

    # product_id identifies the real-world product, which can span multiple
    # listings across sources (e.g. the same racket sold by both Direct Tennis
    # and Tennis Nuts) - see product_matcher.py for how that mapping is built.
    # Listings not yet covered by it (e.g. crawled after the last matcher run)
    # fall back to being their own product, same as before.
    productMapping = loadProductMapping()
    df['product_id'] = df['listing_id'].map(productMapping).fillna(df['listing_id'])

    return df


@st.cache_data(ttl=300)
def loadProducts():
    df = loadRawProducts()
    df = df.sort_values('Time Added').drop_duplicates(subset=['product_id'], keep='last')
    return df


@st.cache_data(ttl=300)
def loadStaleListings(staleDays=14):
    # A listing whose own last-seen crawl date has fallen well behind its
    # source's most recent crawl is presumed delisted or moved (e.g. the
    # retailer changed its URL) - see the discussion in product_matcher.py
    # about why this isn't auto-resolved: flagging for manual review is safer
    # than guessing. Threshold is relative to each source's own latest crawl,
    # not "today", since sources aren't crawled on a shared schedule.
    df = loadRawProducts()

    perListing = df.groupby('listing_id').agg(**{
        'Product Name': ('Product Name', 'last'),
        'Brand': ('Brand', 'last'),
        'Product Cat': ('Product Cat', 'last'),
        'Source': ('Source', 'last'),
        'Product URL': ('Product URL', 'last'),
        'product_id': ('product_id', 'last'),
        'Last Seen': ('Time Added', 'max'),
    }).reset_index()

    latestPerSource = df.groupby('Source')['Time Added'].max().rename('Source Last Crawled')
    perListing = perListing.merge(latestPerSource, on='Source', how='left')

    perListing['Days Since Last Seen'] = (perListing['Source Last Crawled'] - perListing['Last Seen']).dt.days
    stale = perListing[perListing['Days Since Last Seen'] >= staleDays]
    return stale.sort_values('Days Since Last Seen', ascending=False)
