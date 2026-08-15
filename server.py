import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_RUNNING
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans

from cli_schedule import build_all_tasks

logger = logging.getLogger(f"uvicorn.{__name__}")

# scheduler setup
def build_all_tasks_no_args():
    build_all_tasks(None)

scheduler = BackgroundScheduler()
scheduler.add_job(build_all_tasks_no_args, CronTrigger(hour="8,20", minute="0"))

# server lifecycle events for startup and shutdown
def startup(_app: FastAPI):
    load_dotenv()
    logger.info("Starting up server...")
    try:
        # Start the scheduler as part of the app startup lifecycle.
        # Guard against double-starts (e.g. autoreload) by checking state.
        logger.info("Starting scheduler...")
        if scheduler.state != STATE_RUNNING:
            scheduler.start()
            logger.info("Scheduler started.")
        else:
            logger.info("Scheduler already running.")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")


def shutdown(_app: FastAPI):
    logger.info("Shutting down server...")
    try:
        # Shut down the scheduler during app shutdown. Use non-blocking wait.
        logger.info("Shutting down scheduler...")
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shutdown initiated.")
    except Exception as e:
        logger.error(f"Failed to shutdown scheduler: {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup(_app=_app)
    yield
    shutdown(_app=_app)


# primary FastAPI app used by route modules
app = FastAPI(lifespan=lifespan, name="Libram Price Feed API", version="1.0.0")

from routes.compare import router as compare_router
from routes.dividends import router as dividends_router
from routes.entities import router as entities_router
from routes.fundamentals import router as fundamentals_router
from routes.indicators import router as indicators_router
from routes.portfolios import router as portfolios_router
from routes.prices import router as prices_router

app.include_router(entities_router)
app.include_router(prices_router)
app.include_router(indicators_router)
app.include_router(compare_router)
app.include_router(fundamentals_router)
app.include_router(portfolios_router)
app.include_router(dividends_router)

# MCP setup: expose MCP under /mcp as a stateless HTTP transport
mcp = FastMCP.from_fastapi(app=app, name="Libram Price Feed MCP", version="1.0.0")
mcp_app = mcp.http_app(path="/mcp", stateless_http=True, transport="http")


# combined app that merges MCP routes and original API routes
combined_app = FastAPI(
    name="Libram Price Feed API with MCP",
    routes=[
        *mcp_app.routes,  # MCP routes
        *app.routes,  # Original API routes
    ],
    lifespan=combine_lifespans(lifespan, mcp_app.lifespan),
)

# CORS middleware setup for the combined app
combined_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
