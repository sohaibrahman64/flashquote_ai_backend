import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from app.routers.users import router as users_router
from app.routers.subscriptions import router as subscriptions_router

app = FastAPI()
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

cors_origins_env = os.getenv("CORS_ALLOW_ORIGINS", "*")
cors_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]

allow_all_origins = len(cors_origins) == 1 and cors_origins[0] == "*"

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins if cors_origins else ["*"],
    allow_credentials=not allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users_router)
app.include_router(subscriptions_router)

@app.get("/")
async def root():
    return {"message": "Hello World"}