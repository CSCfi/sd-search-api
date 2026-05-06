from fastapi import FastAPI
from .api.bigpicture.routes import router

# uvicorn search_api.main:app --reload


app = FastAPI(title="BigPicture Image Beacon", version="2.0")

app.include_router(router)
