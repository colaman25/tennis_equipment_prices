from pymongo import MongoClient, UpdateOne

import config
from data import (
    MONGODB_NAME, MONGODB_EMBEDDINGS_COLLECTION, embedTexts, loadProducts,
)


def buildEmbeddingText(row):
    return f"{row['Brand']} {row['Product Name']} {row['Product Cat']}"


def run():
    client = MongoClient(config.mongo_cnx_string)
    collection = client[MONGODB_NAME][MONGODB_EMBEDDINGS_COLLECTION]
    collection.create_index('product_id', unique=True)

    products = loadProducts().dropna(subset=['Product Name', 'Brand', 'Product Cat'])
    products = products.drop_duplicates(subset=['product_id'])

    # Only (re-)embed what's new or whose text has changed since last run -
    # a normal weekly run should just be the handful of newly-matched
    # products, not the whole ~29k-product catalog every time.
    existing = {doc['product_id']: doc['text']
                for doc in collection.find({}, {'_id': 0, 'product_id': 1, 'text': 1})}

    toEmbed = []
    for _, row in products.iterrows():
        text = buildEmbeddingText(row)
        if existing.get(row['product_id']) != text:
            toEmbed.append((row['product_id'], text))

    print(f'{len(products)} current products, {len(toEmbed)} need (re-)embedding')
    if not toEmbed:
        return

    # Write each batch to Mongo right after embedding it, rather than
    # accumulating everything and writing once at the end - a run of this
    # size takes long enough that the Mongo connection can drop before a
    # single final write, which would otherwise lose every embedding from
    # the whole run instead of just the batch in flight.
    totalSaved = 0
    for i in range(0, len(toEmbed), 100):
        chunk = toEmbed[i:i + 100]
        texts = [text for _, text in chunk]
        embeddings = embedTexts(texts)
        operations = [
            UpdateOne(
                {'product_id': productId},
                {'$set': {'product_id': productId, 'text': text, 'embedding': embedding.tolist()}},
                upsert=True,
            )
            for (productId, text), embedding in zip(chunk, embeddings)
        ]
        result = collection.bulk_write(operations, ordered=False)
        totalSaved += result.upserted_count + result.modified_count
        print(f'  embedded and saved {min(i + 100, len(toEmbed))}/{len(toEmbed)}')

    print(f'upserted {totalSaved} embeddings total')


if __name__ == '__main__':
    run()
