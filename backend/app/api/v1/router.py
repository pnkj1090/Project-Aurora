from fastapi import APIRouter
from app.api.v1.endpoints import (
    health, chat, memories, memory_candidates, tasks, commitments, auth,
    reminders, location, calendar, contacts, assets, proactive, agenda,
    schedule_recovery
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])

api_router.include_router(health.router, tags=["Health"])
api_router.include_router(chat.router, tags=["Chat"])
api_router.include_router(memory_candidates.router, prefix="/memories/candidates", tags=["Memory Candidates"])
api_router.include_router(memories.router, prefix="/memories", tags=["Memories"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["Tasks"])
api_router.include_router(commitments.router, prefix="/commitments", tags=["Commitments"])
api_router.include_router(reminders.router, prefix="/reminders", tags=["Reminders"])
api_router.include_router(location.router, prefix="/location", tags=["Location"])
api_router.include_router(calendar.router, prefix="/calendar", tags=["Calendar"])
api_router.include_router(contacts.router, prefix="/contacts", tags=["Contacts"])
api_router.include_router(assets.router, prefix="/assets", tags=["Assets"])
api_router.include_router(proactive.router, prefix="/proactive", tags=["Proactive"])
api_router.include_router(agenda.router, prefix="/agenda", tags=["Agenda"])
api_router.include_router(schedule_recovery.router, prefix="/schedule/recovery", tags=["Schedule Recovery"])


