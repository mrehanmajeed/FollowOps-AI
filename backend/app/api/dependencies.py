from functools import lru_cache

from fastapi import Depends
from google import genai

from app.core.config import Settings, get_settings
from app.repositories.supabase import SupabaseRepository, create_supabase_client
from app.services.ai_service import GeminiAIService
from app.services.crm import CRMService
from app.services.execution import ExecutionService
from app.services.imap_client import ZohoImapClient
from app.services.reply_processor import ReplyProcessor
from app.services.scheduler import ReplyPollingScheduler
from app.services.tasks import TaskService
from app.services.workflow import WorkflowService
from app.services.zoho import ZohoEmailService

_repository: SupabaseRepository | None = None


@lru_cache
def get_gemini_client() -> genai.Client:
    settings = get_settings()
    return genai.Client(api_key=settings.gemini_api_key.get_secret_value())


@lru_cache
def get_scheduler() -> ReplyPollingScheduler:
    return ReplyPollingScheduler(get_settings())


async def get_repository() -> SupabaseRepository:
    """One Supabase client for the process; it is an async HTTP client."""
    global _repository
    if _repository is None:
        _repository = SupabaseRepository(await create_supabase_client())
    return _repository


def get_email_service(
    settings: Settings = Depends(get_settings),
) -> ZohoEmailService:
    return ZohoEmailService(settings)


def get_imap_client(settings: Settings = Depends(get_settings)) -> ZohoImapClient:
    return ZohoImapClient(settings)


async def get_reply_processor(
    repository: SupabaseRepository = Depends(get_repository),
    imap_client: ZohoImapClient = Depends(get_imap_client),
) -> ReplyProcessor:
    return ReplyProcessor(repository, imap_client)


def get_workflow_service(
    settings: Settings = Depends(get_settings),
    repository: SupabaseRepository = Depends(get_repository),
) -> WorkflowService:
    return WorkflowService(
        repository=repository,
        ai_service=GeminiAIService(get_gemini_client(), settings),
        email_service=ZohoEmailService(settings),
        task_service=TaskService(repository),
        crm_service=CRMService(repository, settings),
        settings=settings,
        execution_service=ExecutionService(repository),
    )
