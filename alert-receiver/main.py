from fastapi import FastAPI, Request
import json

app = FastAPI()


@app.post("/alerts")
async def receive_alert(request: Request):
    payload = await request.json()
    print(json.dumps(payload, indent=2))
    return {"status": "ok"}
