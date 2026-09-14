import re
import uuid
from collections import Counter
from itertools import combinations

from pymongo import MongoClient, UpdateOne
from rapidfuzz import fuzz

import config
from data import loadProducts

MONGODB_NAME = 'tennis_prod'
MONGODB_MAPPING_COLLECTION = 'product_mapping'

# rapidfuzz score is 0-100.
SIMILARITY_THRESHOLD = 85

# token_set_ratio scores a short generic name as a *perfect* match against any
# longer name that contains it (e.g. "Pure Drive" vs "Pure Drive Junior 26
# Gen 11 Tennis Racket - Blue" scores 100), because it only measures whether
# the shorter name's words are a subset of the longer one's - it's blind to
# the fact that the extra word is exactly what makes it a different, cheaper,
# different-audience racket. Confirmed against real mismatches in production
# data (Pure Drive/Junior, EZONE 98/Plus, CX 400/Tour). A pair is disqualified
# regardless of fuzzy score when one side has a word from one of these groups
# and the other side has none of that group's synonyms.
VARIANT_WORD_GROUPS = [
    {'junior', 'jr', 'kids', 'youth'},
    {'lite', 'superlite'},
    {'team'},
    {'tour'},
    {'plus'},
    {'pro'},
]

# Version/generation markers ("V3.0", "V5", "Gen4", "Gen 11") - if both names
# carry one and they don't share any marker, they're different model
# generations described two ways, not the same racket.
VERSION_PATTERN = re.compile(r'\bv\s?\d+(?:\.\d+)?\b|\bgen\s?\d+\b')


def isDisqualified(nameA, nameB):
    tokensA = set(re.findall(r'[a-z0-9.]+', nameA.lower()))
    tokensB = set(re.findall(r'[a-z0-9.]+', nameB.lower()))

    for group in VARIANT_WORD_GROUPS:
        if bool(tokensA & group) != bool(tokensB & group):
            return True

    versionsA = set(VERSION_PATTERN.findall(nameA.lower()))
    versionsB = set(VERSION_PATTERN.findall(nameB.lower()))
    if versionsA and versionsB and versionsA.isdisjoint(versionsB):
        return True

    return False


class UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        rootA, rootB = self.find(a), self.find(b)
        if rootA != rootB:
            self.parent[rootB] = rootA


def bestMatchesBetween(rowsA, rowsB):
    # For every item in A, the single highest-scoring item in B (not "anything
    # above threshold") - top-1 nearest neighbor, keyed by listing_id. Variant-
    # disqualified candidates are excluded before picking the best, not just
    # filtered after, so a real match sitting right there isn't shadowed by a
    # disqualified candidate that token_set_ratio happened to score higher.
    best = {}
    for a in rowsA:
        candidates = [
            (b['listing_id'], fuzz.token_set_ratio(a['Product Name'], b['Product Name']))
            for b in rowsB
            if not isDisqualified(a['Product Name'], b['Product Name'])
        ]
        if candidates:
            best[a['listing_id']] = max(candidates, key=lambda c: c[1])
    return best


def findClusters(df):
    uf = UnionFind(df['listing_id'].tolist())

    # Plain "above threshold, then transitively merge" chains false positives
    # together through generic shared words (e.g. a giant "Babolat Tennis
    # Rackets" cluster wrongly merging Junior/Pure Strike/Pure Drive/Ballfighter
    # rackets just because they share words like "Junior" or "Drive"). Instead,
    # for each pair of sources within a block, only match a listing to its
    # single best counterpart, and only keep the match if it's *mutual* - A's
    # best match in the other source is B, and B's best match back is A. This
    # "stable pairing" is far more conservative and doesn't chain.
    matchable = df[(df['Brand'].fillna('') != '') & (df['Product Cat'].fillna('') != '')]
    for (brand, category), group in matchable.groupby(['Brand', 'Product Cat']):
        sources = group['Source'].unique()
        if len(sources) < 2:
            continue

        for source_a, source_b in combinations(sources, 2):
            rowsA = group[group['Source'] == source_a][['listing_id', 'Product Name']].to_dict('records')
            rowsB = group[group['Source'] == source_b][['listing_id', 'Product Name']].to_dict('records')
            if not rowsA or not rowsB:
                continue

            bestFromA = bestMatchesBetween(rowsA, rowsB)
            bestFromB = bestMatchesBetween(rowsB, rowsA)

            for aId, (bId, score) in bestFromA.items():
                if score < SIMILARITY_THRESHOLD:
                    continue
                mutual = bestFromB.get(bId)
                if mutual and mutual[0] == aId:
                    uf.union(aId, bId)

    clusters = {}
    for listing_id in df['listing_id']:
        root = uf.find(listing_id)
        clusters.setdefault(root, []).append(listing_id)

    return list(clusters.values())


def loadExistingMapping():
    client = MongoClient(config.mongo_cnx_string)
    collection = client[MONGODB_NAME][MONGODB_MAPPING_COLLECTION]
    return {doc['listing_id']: doc['product_id']
            for doc in collection.find({}, {'_id': 0, 'listing_id': 1, 'product_id': 1})}


def resolveProductIds(clusters, existingMapping):
    # product_id must be sticky across runs - it can't be derived from current
    # cluster membership (e.g. hash of sorted members), because membership
    # changes every time a new listing joins an existing group, which would
    # mint a new id for every listing already in it. Instead: reuse whatever
    # id(s) the cluster's members already had, and only mint a fresh one when
    # none of them have ever been assigned one before.
    mapping = {}
    for members in clusters:
        priorIds = {existingMapping[m] for m in members if m in existingMapping}

        if not priorIds:
            productId = uuid.uuid4().hex[:12]
        elif len(priorIds) == 1:
            productId = next(iter(priorIds))
        else:
            # Members disagree - two previously-separate products are now
            # judged the same. Keep the lowest id deterministically and note it.
            productId = min(priorIds)
            print(f"  merging previously-distinct products {sorted(priorIds)} into {productId}")

        for listing_id in members:
            mapping[listing_id] = productId

    return mapping


def matchProducts():
    df = loadProducts()
    clusters = findClusters(df)
    existingMapping = loadExistingMapping()
    return resolveProductIds(clusters, existingMapping)


def saveMapping(mapping):
    client = MongoClient(config.mongo_cnx_string)
    collection = client[MONGODB_NAME][MONGODB_MAPPING_COLLECTION]
    collection.create_index('listing_id', unique=True)
    operations = [
        UpdateOne({'listing_id': listing_id}, {'$set': {'product_id': product_id}}, upsert=True)
        for listing_id, product_id in mapping.items()
    ]
    if operations:
        collection.bulk_write(operations, ordered=False)


if __name__ == '__main__':
    mapping = matchProducts()
    saveMapping(mapping)

    cluster_sizes = Counter(mapping.values())
    num_multi_listing = sum(1 for size in cluster_sizes.values() if size > 1)
    print(f"{len(mapping)} listings matched into {len(cluster_sizes)} products "
          f"({num_multi_listing} products span more than one listing)")
