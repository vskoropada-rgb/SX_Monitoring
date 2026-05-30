from fastapi import FastAPI
from contextlib import asynccontextmanager

from database import init_db
from routers import metrics, commands, status


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="SX Monitor Server", lifespan=lifespan)

app.include_router(metrics.router)
app.include_router(commands.router)
app.include_router(status.router)


@app.get("/health")
def health():
    return {"ok": True}
