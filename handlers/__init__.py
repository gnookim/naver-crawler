from .base import BaseCrawler
from .kin import KinHandler
from .kin_post import KinPostHandler
from .blog import BlogCrawlHandler
from .serp import BlogSerpHandler
from .area import AreaAnalysisHandler
from .deep import DeepAnalysisHandler
from .rank import DailyRankHandler
from .instagram import InstagramProfileHandler

try:
    from .instagram_post import InstagramPostHandler
except ImportError:
    InstagramPostHandler = None  # type: ignore

try:
    from .oclick import OclickSyncHandler
except ImportError:
    OclickSyncHandler = None  # type: ignore

HANDLERS = {
    "kin_analysis": KinHandler,
    "kin_post": KinPostHandler,
    "blog_crawl": BlogCrawlHandler,
    "blog_serp": BlogSerpHandler,
    "area_analysis": AreaAnalysisHandler,
    "deep_analysis": DeepAnalysisHandler,
    "daily_rank": DailyRankHandler,
    "rank_check": DailyRankHandler,
    "instagram_profile": InstagramProfileHandler,
    "instagram_login_test": InstagramProfileHandler,
}

if InstagramPostHandler is not None:
    HANDLERS["instagram_post"] = InstagramPostHandler

if OclickSyncHandler is not None:
    HANDLERS["oclick_sync"] = OclickSyncHandler
