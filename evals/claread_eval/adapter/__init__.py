from claread_eval.adapter.factory import AdapterKind, create_adapter_client
from claread_eval.adapter.fake_client import FakeArticleAnalysisAdapterClient
from claread_eval.adapter.in_process_client import InProcessArticleAnalysisAdapterClient
from claread_eval.adapter.protocol import ArticleAnalysisAdapterClient

__all__ = [
    "AdapterKind",
    "ArticleAnalysisAdapterClient",
    "FakeArticleAnalysisAdapterClient",
    "InProcessArticleAnalysisAdapterClient",
    "create_adapter_client",
]
