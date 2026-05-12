class WordNotFoundError(Exception):
    """词典查询失败（词不存在）。"""


class ServiceUnavailableError(Exception):
    """词典服务不可用（数据库未连接等）。"""
