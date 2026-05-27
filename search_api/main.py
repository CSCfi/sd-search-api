from fastapi import FastAPI
import uvicorn

from search_api.api.bigpicture.routes import router

# uvicorn search_api.main:app --reload

# TODO(improve): support other than Bigpicture Beacons

app = FastAPI(title="CSC Bigpicture Beacon", version="1.0")

app.include_router(router)


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)
