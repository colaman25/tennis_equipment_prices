import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'Crawlers'))
from data import loadStaleListings

st.set_page_config(page_title="Stale Listings", layout="wide")

st.page_link("app.py", label="← Back to product list")
st.title("Stale Listings")
st.caption(
    "Listings that haven't shown up in their source's most recent crawls - likely delisted, "
    "or the retailer changed the product's URL (which would otherwise silently split its price "
    "history into a disconnected old/new pair). Flagged here for manual review rather than "
    "auto-merged, since there's no reliable way to confirm it's the same product."
)

staleDays = st.number_input("Flag listings not seen for at least this many days", min_value=1, value=14)

stale = loadStaleListings(staleDays=staleDays)
st.write(f"{len(stale)} stale listings")

table_columns = ['Product Name', 'Brand', 'Product Cat', 'Source', 'Last Seen', 'Days Since Last Seen']
event = st.dataframe(
    stale[table_columns],
    hide_index=True,
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-row",
)

selected_rows = event.selection.rows
if selected_rows:
    selected = stale.iloc[selected_rows[0]]
    detail_url = f"/Product_Detail?id={selected['product_id']}"
    st.markdown(f"**Selected:** {selected['Product Name']} — [View price history →]({detail_url})")
    st.link_button("View on retailer site", selected['Product URL'])
