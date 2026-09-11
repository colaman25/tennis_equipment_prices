import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')

mongo_cnx_string = os.environ['MONGO_CNX_STRING']

# Webshare proxies for TennisNutsCrawler and TennisPointCrawler (requests-based).
# Comma-separated in .env, e.g. 'http://user:pass@host1:port1,http://user:pass@host2:port2'.
# Leave WEBSHARE_PROXY_URLS unset/empty to disable - crawlers fall back to no
# proxy. Not used by DirectTennisCrawler or TennisWarehouseEuropeCrawler (Selenium).
proxy_urls = [url for url in os.environ.get('WEBSHARE_PROXY_URLS', '').split(',') if url]
