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


def _addDerivedColumns(df):
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
def loadLatestListings():
    # One row per listing (its most recent crawl only), computed server-side
    # by MongoDB instead of pulling the full multi-month history into memory.
    # Time Added is stored as a 'YYYY-MM-DD HH:MM:SS' string, which sorts
    # chronologically as plain text, so a string sort here is correct.
    client = MongoClient(config.mongo_cnx_string)
    collection = client[MONGODB_NAME][MONGODB_COLLECTION]
    # $top sorts within each group only (bounded by that listing's own crawl
    # count), not the whole collection - a preceding $sort stage over all
    # 200k+ documents hits Atlas's shared-tier limit (sorts that spill to disk
    # aren't allowed on M0) and fails outright, even with allowDiskUse=True.
    pipeline = [
        {'$group': {
            '_id': {'Source': '$Source', 'Product URL': '$Product URL'},
            'doc': {'$top': {'sortBy': {'Time Added': -1}, 'output': '$$ROOT'}},
        }},
        {'$replaceRoot': {'newRoot': '$doc'}},
        {'$project': {'_id': 0}},
    ]
    df = pd.DataFrame(list(collection.aggregate(pipeline)))
    return _addDerivedColumns(df)


@st.cache_data(ttl=300)
def loadProductHistory(product_id):
    # Full multi-crawl history for one product only, queried directly by its
    # listing_id(s) instead of loading the entire collection just to filter
    # down to one product in pandas afterward - which is what pushed memory
    # past Render's 512MB limit whenever the Product Detail page was visited.
    # Requires every document to carry a 'listing_id' field (crawlers write
    # this going forward; pre-existing documents need the one-time backfill).
    mapping = loadProductMapping()
    listingIds = [lid for lid, pid in mapping.items() if pid == product_id]
    if not listingIds:
        # Not in product_mapping yet (e.g. crawled after the last matcher
        # run) - the fallback elsewhere is "product_id defaults to its own
        # listing_id", so treat the id itself as the listing_id to look up.
        listingIds = [product_id]

    client = MongoClient(config.mongo_cnx_string)
    collection = client[MONGODB_NAME][MONGODB_COLLECTION]
    df = pd.DataFrame(list(collection.find({'listing_id': {'$in': listingIds}}, {'_id': 0})))
    if df.empty:
        return df
    return _addDerivedColumns(df)


@st.cache_data(ttl=300)
def loadProducts():
    df = loadLatestListings()
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
    df = loadLatestListings()

    perListing = df.rename(columns={'Time Added': 'Last Seen'})[
        ['listing_id', 'Product Name', 'Brand', 'Product Cat', 'Source', 'Product URL', 'product_id', 'Last Seen']
    ]

    latestPerSource = df.groupby('Source')['Time Added'].max().rename('Source Last Crawled')
    perListing = perListing.merge(latestPerSource, on='Source', how='left')

    perListing['Days Since Last Seen'] = (perListing['Source Last Crawled'] - perListing['Last Seen']).dt.days
    stale = perListing[perListing['Days Since Last Seen'] >= staleDays]
    return stale.sort_values('Days Since Last Seen', ascending=False)
