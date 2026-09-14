import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / 'Crawlers'))
from data import loadRawProducts

# Fixed slot order (validated categorical palette) so a given source always
# gets the same color across every product page, regardless of which sources
# happen to be present for any one product.
SOURCE_COLORS = {
    'Direct Tennis UK': '#2a78d6',
    'Tennis Nuts': '#1baf7a',
    'Tennis Point': '#eda100',
    'Tennis Warehouse Europe': '#008300',
}

st.set_page_config(page_title="Product Detail", layout="wide")

product_id = st.query_params.get("id")

if not product_id:
    st.title("Product Detail")
    st.write("No product selected. Go back to the main page and select a product from the table.")
    st.page_link("app.py", label="← Back to product list")
    st.stop()

history = loadRawProducts()
history = history[history['product_id'] == product_id]

if history.empty:
    st.title("Product Detail")
    st.write("No history found for this product.")
    st.page_link("app.py", label="← Back to product list")
    st.stop()

latest = history.sort_values('Time Added').iloc[-1]

# One product can be sold by more than one source, so its latest data is
# per-source, not a single row - most recent snapshot for each.
perSource = history.sort_values('Time Added').groupby('Source').last().reset_index()

st.page_link("app.py", label="← Back to product list")
st.title(latest['Product Name'])
st.caption(f"{latest['Brand']} · {latest['Product Cat']}")

st.write("Available at:")
sourceCols = st.columns(len(perSource))
for col, (_, row) in zip(sourceCols, perSource.iterrows()):
    col.link_button(f"{row['Source']} - {row['Product Price']}", row['Product URL'])

cutoff = pd.Timestamp.now() - pd.Timedelta(days=365)
last_52w = history[history['Time Added'] >= cutoff]

col1, col2, col3 = st.columns(3)
col1.metric("Current price", f"£{latest['Price (numeric)']:.2f}" if pd.notna(latest['Price (numeric)']) else "N/A")
col2.metric("52-week low", f"£{last_52w['Price (numeric)'].min():.2f}" if not last_52w.empty else "N/A")
col3.metric("52-week high", f"£{last_52w['Price (numeric)'].max():.2f}" if not last_52w.empty else "N/A")

st.subheader("52-week price trend")
chart_data = last_52w.dropna(subset=['Price (numeric)'])[['Time Added', 'Source', 'Price (numeric)']]

if len(chart_data) < 2:
    st.caption("Not enough price history yet to chart a trend.")
else:
    present_sources = [s for s in SOURCE_COLORS if s in chart_data['Source'].unique()]

    chart = alt.Chart(chart_data).mark_line(strokeWidth=2, interpolate='monotone').encode(
        x=alt.X('Time Added:T', title=None),
        y=alt.Y('Price (numeric):Q', title='Price (£)', scale=alt.Scale(zero=False)),
        color=alt.Color(
            'Source:N',
            scale=alt.Scale(domain=present_sources, range=[SOURCE_COLORS[s] for s in present_sources]),
            legend=alt.Legend(title=None, orient='bottom') if len(present_sources) > 1 else None,
        ),
    ).properties(height=350)
    st.altair_chart(chart, use_container_width=True)

st.subheader("Price history")
st.dataframe(
    history.sort_values('Time Added', ascending=False)[['Time Added', 'Source', 'Product Price', 'Old Price']],
    hide_index=True,
    use_container_width=True,
)
