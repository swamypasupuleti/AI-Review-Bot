import json
import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from app.config import get_settings
from app.review_service import run_review, should_trigger_review
from app.security import verify_github_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger("review-bot")

app = FastAPI(title="Review Bot", version="1.0.0")


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(default=None),
):
    print("request received")
    settings = get_settings()
    raw = await request.body()

    if not verify_github_signature(settings.github_webhook_secret, raw, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = json.loads(raw)
    if not should_trigger_review(payload, settings.bot_username):
        return {"ignored": True, "action": payload.get("action")}

    pr_number = payload["pull_request"]["number"]
    logger.info("Queueing review for PR #%s", pr_number)
    background_tasks.add_task(run_review, payload, settings)
    return {"queued": True, "pr": pr_number}
