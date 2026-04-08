from .base import BaseCrawler
from .kin import KinHandler
from .blog import BlogCrawlHandler
from .serp import BlogSerpHandler
from .area import AreaAnalysisHandler
from .deep import DeepAnalysisHandler
from .rank import DailyRankHandler
from .instagram import InstagramProfileHandler

HANDLERS = {
    "kin_analysis": KinHandler,
    "blog_crawl": BlogCrawlHandler,
    "blog_serp": BlogSerpHandler,
    "area_analysis": AreaAnalysisHandler,
    "deep_analysis": DeepAnalysisHandler,
    "daily_rank": DailyRankHandler,
    "rank_check": DailyRankHandler,  # 기존 타입 호환
    "instagram_profile": InstagramProfileHandler,
}
