import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'Crawlers'))
from data import loadProducts

st.set_page_config(page_title="Tennis Equipment Aggregator", layout="wide")

df = loadProducts()

st.title("Tennis Equipment Aggregator")

search_query = st.text_input("Search product name")

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
    filtered = filtered[filtered['Product Name'].str.contains(search_query, case=False, na=False, regex=False)]
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
