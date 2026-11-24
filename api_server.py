"""
HTTP API for the debate engine, so the PaperGrow web app can call it.

Setup (keep this file next to debate_engine.py):
    pip install fastapi uvicorn anthropic
    export ANTHROPIC_API_KEY=your_key
    uvicorn api_server:app --reload --port 8000

Test without API key:  MOCK=1 uvicorn api_server:app --port 8000
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import debate_engine as eng

app = FastAPI(title="PaperGrow AI Debate")
app.add_middleware(  # allow the browser app to call this API (tighten in production)
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


class IdeaIn(BaseModel):
    symbol: str
    side: str  # BUY or SELL
    entry: float
    target: float
    stop_loss: float
    qty: int
    context: str = ""


class CloseIn(BaseModel):
    exit_price: float


@app.post("/debate")
def debate(body: IdeaIn):
    """Runs Bull/Bear/Judge. If Judge says TAKE, a paper trade is opened."""
    if body.side.upper() not in ("BUY", "SELL"):
        raise HTTPException(400, "side must be BUY or SELL")
    idea = eng.TradeIdea(body.symbol.upper(), body.side.upper(), body.entry,
                         body.target, body.stop_loss, body.qty, body.context)
    result = eng.run_debate(idea)
    try:
        eng.open_trade(idea, result)
    except ValueError as e:  # not enough virtual cash
        raise HTTPException(400, str(e))
    trade = eng.get_portfolio()["trades"][-1]
    return {"trade_id": trade["id"], "status": trade["status"], **result}


@app.post("/trades/{trade_id}/close")
def close(trade_id: int, body: CloseIn):
    try:
        t = eng.close_trade(trade_id, body.exit_price)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"trade_id": t["id"], "pnl": t["pnl"], "status": t["status"]}


@app.get("/portfolio")
def portfolio():
    return eng.get_portfolio()


@app.get("/journal")
def journal():
    return eng.load("journal.json", [])


@app.get("/stats")
def stats():
    return {"summary": eng.stats()}
