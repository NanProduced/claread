from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.daily_reader import router as daily_reader_router
from app.api.routes.daily_reader_admin import router as daily_reader_admin_router
from app.api.routes.dict import router as dict_router
from app.api.routes.email_auth import router as email_auth_router
from app.api.routes.favorites import router as favorites_router
from app.api.routes.feedback import router as feedback_router
from app.api.routes.health import router as health_router
from app.api.routes.internal_feedback import router as internal_feedback_router
from app.api.routes.prompt_debug import router as prompt_debug_router
from app.api.routes.quota import router as quota_router
from app.api.routes.reader_image_overrides import router as reader_image_overrides_router
from app.api.routes.reader_notes import router as reader_notes_router
from app.api.routes.reader_orchestration import router as reader_orchestration_router
from app.api.routes.reader_record_ask import router as reader_record_ask_router
from app.api.routes.reader_recovery import router as reader_recovery_router
from app.api.routes.user_annotations import router as user_annotations_router
from app.api.routes.vocabulary import router as vocabulary_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(dict_router)
api_router.include_router(auth_router)
api_router.include_router(email_auth_router)
api_router.include_router(quota_router)
api_router.include_router(reader_orchestration_router)
api_router.include_router(reader_image_overrides_router)
api_router.include_router(reader_record_ask_router)
api_router.include_router(reader_recovery_router)
api_router.include_router(favorites_router)
api_router.include_router(reader_notes_router)
api_router.include_router(vocabulary_router)
api_router.include_router(feedback_router)
api_router.include_router(internal_feedback_router)
api_router.include_router(daily_reader_router)
api_router.include_router(daily_reader_admin_router)
api_router.include_router(prompt_debug_router)
api_router.include_router(user_annotations_router)
