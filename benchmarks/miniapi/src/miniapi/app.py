"""FastAPI application wiring all module routers together.

Each module registers an APIRouter.  This file includes them all
under their respective prefixes.  Do NOT modify this file — the
benchmark expects this exact router layout.
"""
from fastapi import FastAPI

from miniapi.users import router as users_router
from miniapi.projects import router as projects_router
from miniapi.tasks import router as tasks_router
from miniapi.tags import router as tags_router
from miniapi.search import router as search_router
from miniapi.stats import router as stats_router

app = FastAPI(title="miniapi", version="0.1.0")

app.include_router(users_router, prefix="/users", tags=["users"])
app.include_router(projects_router, prefix="/projects", tags=["projects"])
app.include_router(tasks_router, prefix="/tasks", tags=["tasks"])
app.include_router(tags_router, prefix="/tags", tags=["tags"])
app.include_router(search_router, prefix="/search", tags=["search"])
app.include_router(stats_router, prefix="/stats", tags=["stats"])


@app.get("/health")
def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}
