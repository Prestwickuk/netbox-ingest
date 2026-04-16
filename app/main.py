from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import engine
from app.models.db import Base
from app.api import jobs, upload, netbox_instances


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="HAROLD", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(jobs.router)
app.include_router(upload.router)
app.include_router(netbox_instances.router)
