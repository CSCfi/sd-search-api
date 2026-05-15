from fastapi import FastAPI
from .api.bigpicture.routes import router
import uvicorn

# uvicorn search_api.main:app --reload


# TODO(improve): support other than Bigpicture Beacons

app = FastAPI(title="CSC Bigpicture Beacon", version="1.0")

app.include_router(router)


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)
