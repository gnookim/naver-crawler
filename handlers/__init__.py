from .base import BaseCrawler
from .kin import KinHandler
from .blog import BlogCrawlHandler
from .serp import BlogSerpHandler

HANDLERS = {
    "kin_analysis": KinHandler,
    "blog_crawl": BlogCrawlHandler,
    "blog_serp": BlogSerpHandler,
}
