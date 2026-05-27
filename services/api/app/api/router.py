from fastapi import APIRouter

from app.api.routes.analyze import router as analyze_router
from app.api.routes.auth import router as auth_router
from app.api.routes.daily_reader import router as daily_reader_router
from app.api.routes.daily_reader_admin import router as daily_reader_admin_router
from app.api.routes.dict import router as dict_router
from app.api.routes.favorites import router as favorites_router
from app.api.routes.feedback import router as feedback_router
from app.api.routes.health import router as health_router
from app.api.routes.internal_feedback import router as internal_feedback_router
from app.api.routes.prompt_debug import router as prompt_debug_router
from app.api.routes.quota import router as quota_router
from app.api.routes.reader_ask import router as reader_ask_router
from app.api.routes.reader_notes import router as reader_notes_router
from app.api.routes.records import router as records_router
from app.api.routes.tasks import router as tasks_router
from app.api.routes.user_annotations import router as user_annotations_router
from app.api.routes.vocabulary import router as vocabulary_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(analyze_router)
api_router.include_router(dict_router)
api_router.include_router(auth_router)
api_router.include_router(records_router)
api_router.include_router(tasks_router)
api_router.include_router(quota_router)
api_router.include_router(reader_ask_router)
api_router.include_router(favorites_router)
api_router.include_router(reader_notes_router)
api_router.include_router(vocabulary_router)
api_router.include_router(feedback_router)
api_router.include_router(internal_feedback_router)
api_router.include_router(daily_reader_router)
api_router.include_router(daily_reader_admin_router)
api_router.include_router(prompt_debug_router)
api_router.include_router(user_annotations_router)
