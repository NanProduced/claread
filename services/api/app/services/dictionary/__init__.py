from app.services.dictionary.errors import WordNotFoundError, ServiceUnavailableError
from app.services.dictionary.service import DictionaryService, get_service

__all__ = ["DictionaryService", "WordNotFoundError", "ServiceUnavailableError", "get_service"]
