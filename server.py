from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.concurrency import asynccontextmanager
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from fastapi.middleware.cors import CORSMiddleware

def startup(_app: FastAPI):
    load_dotenv()
    print("Starting up server...")


def shutdown(_app: FastAPI):
    print("Shutting down server...")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup(_app=_app)
    yield
    shutdown(_app=_app)


# primary FastAPI app used by route modules
app = FastAPI(lifespan=lifespan, name="Libram Price Feed API", version="1.0.0")

from routes.entities import router as entities_router
from routes.prices import router as prices_router
from routes.indicators import router as indicators_router
from routes.compare import router as compare_router
from routes.fundamentals import router as fundamentals_router
from routes.portfolios import router as portfolios_router
from routes.dividends import router as dividends_router

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
    routes=[*mcp_app.routes],
    lifespan=combine_lifespans(lifespan, mcp_app.lifespan),
)


combined_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from cli_schedule import build_all_tasks


def build_all_tasks_no_args():
    build_all_tasks(None)


# Create and start the scheduler with the same schedule as before
scheduler = BackgroundScheduler()
scheduler.add_job(build_all_tasks_no_args, CronTrigger(hour="8,20", minute="0"))
scheduler.start()