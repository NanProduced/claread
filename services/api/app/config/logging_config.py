"""
日志配置模块。

使用 Python logging 标准配置，支持：
- 控制台输出 + 文件轮转
- 统一日志格式：[模块] | level | message
- 业务模块分级：dict / tasks / auth / workflow / db / spacy / app
- 可配置日志级别（DEBUG / INFO / WARNING）

使用规范：
    from app.config.logging_config import get_business_logger
    logger = get_business_logger("dict")  # 获取词典模块 logger
    logger.info("spaCy 模型加载成功")

日志格式：
    2026-04-12 10:30:01 | INFO  | [dict]     | spaCy 模型加载成功
    2026-04-12 10:30:02 | INFO | [tasks]    | 任务执行成功
"""

import logging
from logging import DEBUG, INFO, WARNING, Formatter, LogRecord, StreamHandler, getLogger
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config.settings import Settings, get_settings

# 业务模块名称映射（Python path 前缀 -> 业务模块名）
# 使用 getLogger(__name__) 时，名称会自动映射为 [模块] 子模块名
_MODULE_PREFIX_MAP = {
    "app.services.dictionary": "dict",
    "app.services.dictionary.providers": "dict",
    "app.services.analysis": "tasks",
    "app.services.auth": "auth",
    "app.workflow": "workflow",
    "app.database": "db",
    "app.api": "api",
    "app.main": "app",
    "app.observability": "app",
}


# ANSI 颜色码（只给 level 上色）
class _ColorCodes:
    RESET = "\033[0m"
    DEBUG = "\033[90m"      # 亮黑色（灰色）
    INFO = "\033[92m"       # 亮绿色
    WARNING = "\033[93m"    # 亮黄色
    ERROR = "\033[91m"      # 亮红色
    CRITICAL = "\033[95m"   # 亮紫色


class BusinessModuleFormatter(Formatter):
    """业务模块格式器：将 Python logger 名称转为短模块名。"""

    def format(self, record: LogRecord) -> str:
        # 把 app.services.dictionary.nlp -> dict
        name = record.name
        for prefix, module in _MODULE_PREFIX_MAP.items():
            if name.startswith(prefix):
                # 取前缀后的部分，如 nlp
                suffix = name[len(prefix):].strip(".")
                if suffix:
                    record.name = f"[{module}] {suffix}"
                else:
                    record.name = f"[{module}]"
                break
        else:
            # 未匹配的显示原名（取最后一段）
            parts = name.split(".")
            record.name = f"[{parts[-1]}]"

        return super().format(record)


class ColoredLevelFormatter(BusinessModuleFormatter):
    """只给 level 上色的格式器。"""

    _LEVEL_COLORS = {
        "DEBUG": _ColorCodes.DEBUG,
        "INFO": _ColorCodes.INFO,
        "WARNING": _ColorCodes.WARNING,
        "ERROR": _ColorCodes.ERROR,
        "CRITICAL": _ColorCodes.CRITICAL,
    }

    def format(self, record: LogRecord) -> str:
        # 先用父类格式化（拿到处理过的 record.name）
        # 由于 Formatter.format 会修改 record.msg，我们需要手动拼接
        color = self._LEVEL_COLORS.get(record.levelname, _ColorCodes.RESET)
        reset = _ColorCodes.RESET

        # 保存原始 levelname
        raw_level = record.levelname
        # 用带颜色的版本临时替换
        record.levelname = f"{color}{raw_level:<8}{reset}"

        try:
            return super().format(record)
        finally:
            record.levelname = raw_level


def get_business_logger(module: str) -> "logging.Logger":
    """
    获取业务模块 logger。

    Args:
        module: 业务模块名，可选值：
            - dict: 词典服务
            - tasks: 任务服务
            - auth: 认证服务
            - workflow: 工作流
            - db: 数据库
            - api: API 路由
            - spacy: spaCy 相关
            - app: 应用启动/关闭

    Returns:
        配置好的 logger 实例
    """
    logger = getLogger(f"app.{module}")
    return logger


def setup_logging(settings: Settings | None = None) -> None:
    """配置应用日志系统。"""
    if settings is None:
        settings = get_settings()

    # 日志级别映射
    level_map = {
        "DEBUG": DEBUG,
        "INFO": INFO,
        "WARNING": WARNING,
    }
    app_level = level_map.get(settings.log_level.upper(), INFO)

    # --- formatter ---
    # 格式：时间 | 级别 | [模块] | 消息
    detailed_formatter = ColoredLevelFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 文件日志不使用颜色（方便日志分析工具处理）
    file_formatter = BusinessModuleFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    simple_formatter = Formatter(
        fmt="%(levelname)-8s | %(message)s",
    )

    # --- console handler ---
    console_handler = StreamHandler()
    console_handler.setLevel(app_level)
    console_handler.setFormatter(detailed_formatter)

    # --- file handler（可选，保留最近 10MB 日志） ---
    log_dir = Path(__file__).parent.parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(DEBUG)
    file_handler.setFormatter(file_formatter)

    # --- 根 logger（uvicorn 系列） ---
    root_logger = getLogger()
    root_logger.setLevel(WARNING)  # uvicorn access/钟式日志只显示 WARNING+
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # --- app logger ---
    app_logger = getLogger("app")
    app_logger.setLevel(app_level)
    app_logger.handlers.clear()
    app_logger.addHandler(console_handler)
    app_logger.addHandler(file_handler)
    # 防止日志向上传播到 root，避免重复输出
    app_logger.propagate = False

    # --- uvicorn / fastapi 日志降级 ---
    # uvicorn 默认输出太多，改为只显示 WARNING+
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        uv_logger = getLogger(logger_name)
        uv_logger.setLevel(WARNING)
        uv_logger.handlers.clear()
        # 仅控制台，不写文件
        handler = StreamHandler()
        handler.setFormatter(simple_formatter)
        uv_logger.addHandler(handler)
        uv_logger.propagate = False

    # --- third-party loggers（降低噪声） ---
    noisy_loggers = {
        "sqlalchemy.engine": WARNING,
        "httpx": WARNING,
        "openai": WARNING,
        "langchain": WARNING,
        "temporal": WARNING,
    }
    for name, level in noisy_loggers.items():
        lg = getLogger(name)
        lg.setLevel(level)
        lg.propagate = True  # 继承 root handler
