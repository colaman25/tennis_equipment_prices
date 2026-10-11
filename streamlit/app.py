import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'Crawlers'))
from data import loadProducts, semanticSearchProductIds

st.set_page_config(page_title="Tennis Equipment Aggregator", layout="wide")

st.title("Tennis Equipment Aggregator")

try:
    df = loadProducts()
except Exception as e:
    print(f"[app.py] Failed to load products: {e}")
    st.error("Unable to load product data right now. Please try again in a few minutes.")
    st.stop()

search_query = st.text_input("Search products (e.g. \"beginner tennis racket\", \"clay court shoes\")")

st.sidebar.header("Filters")

categories = sorted(df['Product Cat'].dropna().unique())
selected_categories = st.sidebar.multiselect("Category", categories)

brands = sorted(df['Brand'].dropna().unique())
selected_brands = st.sidebar.multiselect("Brand", brands)

sources = sorted(df['Source'].dropna().unique())
selected_sources = st.sidebar.multiselect("Source", sources)

min_price = float(df['Price (numeric)'].min())
max_price = float(df['Price (numeric)'].max())
price_range = st.sidebar.slider("Price range (£)", min_price, max_price, (min_price, max_price))

filtered = df.copy()
if search_query:
    try:
        matchedProductIds = semanticSearchProductIds(search_query)
    except Exception as e:
        print(f"[app.py] Semantic search failed: {e}")
        st.warning("Search is temporarily unavailable - showing all products instead.")
        matchedProductIds = None

    if matchedProductIds is not None:
        rankById = {pid: rank for rank, pid in enumerate(matchedProductIds)}
        filtered = filtered[filtered['product_id'].isin(rankById)]
        filtered = filtered.assign(_searchRank=filtered['product_id'].map(rankById)).sort_values('_searchRank')
if selected_categories:
    filtered = filtered[filtered['Product Cat'].isin(selected_categories)]
if selected_brands:
    filtered = filtered[filtered['Brand'].isin(selected_brands)]
if selected_sources:
    filtered = filtered[filtered['Source'].isin(selected_sources)]
filtered = filtered[filtered['Price (numeric)'].between(price_range[0], price_range[1])]

st.write(f"Showing {len(filtered)} of {len(df)} products")

table_columns = ['Product Name', 'Brand', 'Product Cat', 'Product Price', 'Source']
event = st.dataframe(
    filtered[table_columns],
    hide_index=True,
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-row",
)

selected_rows = event.selection.rows
if selected_rows:
    selected = filtered.iloc[selected_rows[0]]
    detail_url = f"/Product_Detail?id={selected['product_id']}"
    st.markdown(f"**Selected:** {selected['Product Name']} — [View price history →]({detail_url})")
