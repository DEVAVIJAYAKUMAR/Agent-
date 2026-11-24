"""
Bull / Bear / Judge debate engine (paper trading, virtual money)

Setup:
    pip install anthropic
    export ANTHROPIC_API_KEY=your_key
    export DEBATE_MODEL=claude-sonnet-5-5     # optional

Usage:
    python debate_engine.py trade AXISBANK BUY 1180 1195 1172 50 "Results at 14:30 today"
    python debate_engine.py close 1 1190
    python debate_engine.py stats

    MOCK=1 python debate_engine.py trade ...   # test without API key
"""
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

MODEL = os.getenv("DEBATE_MODEL", "claude-sonnet-5-5")
DATA = Path(os.getenv("DEBATE_DATA", "data"))
DATA.mkdir(exist_ok=True)
START_CASH = 1_000_000  # Rs 10 lakh virtual money

# ----------------------------------------------------------------- prompts
BULL_SYS = (
    "You are BULL CLAUDE. Your ONLY job is to argue FOR taking this trade. "
    "Give the 3 strongest reasons (trend, levels, risk:reward, catalysts). "
    "Be specific, use the numbers given. Max 120 words."
)
BEAR_SYS = (
    "You are BEAR CLAUDE. Your ONLY job is to argue AGAINST this trade. "
    "Hunt for what a simple chart view misses: scheduled events (results, RBI, "
    "expiry), gap risk, target too small vs expected move, weak risk:reward, "
    "past mistakes in the journal. Max 120 words."
)
JUDGE_SYS = (
    "You are the JUDGE. You read the Bull and Bear arguments and decide TAKE or SKIP. "
    "Do not just average them: find the single deciding point. "
    'Reply with ONLY this JSON: {"decision": "TAKE" or "SKIP", "confidence": 0-100, '
    '"deciding_point": "...", "reason": "..."}'
)
LESSON_SYS = (
    "You write one-line trading journal lessons. Given a closed trade and the debate "
    "behind it, state what the debate got right or missed. Max 25 words."
)


# --------------------------------------------------------------------- LLM
def llm(system: str, user: str, max_tokens: int = 500) -> str:
    if os.getenv("MOCK") == "1":
        if "JUDGE" in system:
            return ('{"decision": "SKIP", "confidence": 58, "deciding_point": '
                    '"Event risk at 14:30 is bigger than the target", "reason": "mock"}')
        if "journal lessons" in system:
            return "Mock lesson: check scheduled events before entry."
        return "Mock argument."
    import anthropic  # lazy import so MOCK mode works without the package

    client = anthropic.Anthropic()
    r = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return r.content[0].text.strip()


# ----------------------------------------------------------------- storage
def load(name, default):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default


def save(name, obj):
    (DATA / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False))


# ------------------------------------------------------------------ models
@dataclass
class TradeIdea:
    symbol: str
    side: str  # BUY or SELL
    entry: float
    target: float
    stop_loss: float
    qty: int
    context: str = ""

    def brief(self) -> str:
        rr = abs(self.target - self.entry) / max(abs(self.entry - self.stop_loss), 1e-9)
        return (
            f"{self.side} {self.symbol} x{self.qty} | entry {self.entry} | "
            f"target {self.target} | SL {self.stop_loss} | risk:reward {rr:.2f}\n"
            f"Context: {self.context or 'none'}"
        )


# ----------------------------------------------------------------- journal
def recent_lessons(n: int = 5) -> str:
    entries = load("journal.json", [])[-n:]
    if not entries:
        return "Journal is empty."
    return "\n".join(f"- {e['symbol']} ({e['outcome']}): {e['lesson']}" for e in entries)


# ------------------------------------------------------------------ debate
def parse_verdict(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        v = json.loads(m.group(0)) if m else {}
        v["decision"] = "TAKE" if str(v.get("decision", "")).upper() == "TAKE" else "SKIP"
        return v
    except json.JSONDecodeError:
        return {"decision": "SKIP", "confidence": 0, "deciding_point": "",
                "reason": "Judge reply was not valid JSON, defaulting to SKIP"}


def run_debate(idea: TradeIdea, live_data: bool = True) -> dict:
    if live_data and os.getenv("MOCK") != "1":
        try:  # add live price / events / news to whatever context the user typed
            from context_builder import build_context

            live = build_context(idea.symbol, idea.entry, idea.target)
            idea.context = f"{idea.context}\n{live}".strip()
        except Exception as e:
            idea.context = f"{idea.context}\n(live data unavailable: {e})".strip()
    base = f"TRADE IDEA:\n{idea.brief()}\n\nPAST LESSONS:\n{recent_lessons()}"
    bull = llm(BULL_SYS, base)
    bear = llm(BEAR_SYS, base)
    judge_in = f"{base}\n\nBULL ARGUES:\n{bull}\n\nBEAR ARGUES:\n{bear}"
    verdict = parse_verdict(llm(JUDGE_SYS, judge_in))
    return {"bull": bull, "bear": bear, "verdict": verdict}


# --------------------------------------------------------------- portfolio
def get_portfolio() -> dict:
    return load("portfolio.json", {"cash": START_CASH, "trades": []})


def open_trade(idea: TradeIdea, debate: dict) -> dict | None:
    pf = get_portfolio()
    cost = idea.entry * idea.qty
    if debate["verdict"]["decision"] != "TAKE":
        trade = {"id": len(pf["trades"]) + 1, "status": "SKIPPED", "idea": asdict(idea),
                 "debate": debate, "opened": datetime.now().isoformat(timespec="seconds")}
        pf["trades"].append(trade)
        save("portfolio.json", pf)
        return None
    if idea.side == "BUY" and cost > pf["cash"]:
        raise ValueError(f"Not enough virtual cash: need {cost:,.0f}, have {pf['cash']:,.0f}")
    if idea.side == "BUY":
        pf["cash"] -= cost
    trade = {"id": len(pf["trades"]) + 1, "status": "OPEN", "idea": asdict(idea),
             "debate": debate, "opened": datetime.now().isoformat(timespec="seconds")}
    pf["trades"].append(trade)
    save("portfolio.json", pf)
    return trade


def close_trade(trade_id: int, exit_price: float) -> dict:
    pf = get_portfolio()
    t = next((x for x in pf["trades"] if x["id"] == trade_id), None)
    if not t or t["status"] != "OPEN":
        raise ValueError("Trade not found or not open")
    i = t["idea"]
    direction = 1 if i["side"] == "BUY" else -1
    pnl = (exit_price - i["entry"]) * i["qty"] * direction
    if i["side"] == "BUY":
        pf["cash"] += exit_price * i["qty"]
    else:
        pf["cash"] += pnl
    t.update(status="CLOSED", exit=exit_price, pnl=round(pnl, 2),
             closed=datetime.now().isoformat(timespec="seconds"))
    save("portfolio.json", pf)

    outcome = "WIN" if pnl > 0 else "LOSS"
    lesson = llm(
        LESSON_SYS,
        f"Trade: {TradeIdea(**i).brief()}\nExit: {exit_price} | P&L: {pnl:.0f} ({outcome})\n"
        f"Judge said: {json.dumps(t['debate']['verdict'])}\nBear said: {t['debate']['bear']}",
        max_tokens=100,
    )
    journal = load("journal.json", [])
    journal.append({"symbol": i["symbol"], "outcome": outcome, "pnl": t["pnl"],
                    "lesson": lesson, "date": t["closed"]})
    save("journal.json", journal)
    return t


def stats() -> str:
    pf = get_portfolio()
    closed = [t for t in pf["trades"] if t["status"] == "CLOSED"]
    skipped = [t for t in pf["trades"] if t["status"] == "SKIPPED"]
    wins = [t for t in closed if t["pnl"] > 0]
    total = sum(t["pnl"] for t in closed)
    rate = f"{len(wins) / len(closed):.0%}" if closed else "n/a"
    return (f"Cash: Rs {pf['cash']:,.0f} | Closed: {len(closed)} | Win rate: {rate} | "
            f"Realised P&L: Rs {total:,.0f} | Skipped by Judge: {len(skipped)}")


# --------------------------------------------------------------------- CLI
def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return
    cmd = argv[1]
    if cmd == "trade":
        sym, side, entry, target, sl, qty = argv[2:8]
        ctx = argv[8] if len(argv) > 8 else ""
        idea = TradeIdea(sym.upper(), side.upper(), float(entry), float(target),
                         float(sl), int(qty), ctx)
        d = run_debate(idea)
        print(f"\nBULL:\n{d['bull']}\n\nBEAR:\n{d['bear']}\n")
        v = d["verdict"]
        print(f"JUDGE: {v['decision']} ({v.get('confidence', '?')}%)")
        print(f"Deciding point: {v.get('deciding_point', '')}\nReason: {v.get('reason', '')}\n")
        t = open_trade(idea, d)
        print(f"Paper trade #{t['id']} opened." if t else "Skipped, no position taken.")
    elif cmd == "close":
        t = close_trade(int(argv[2]), float(argv[3]))
        print(f"Trade #{t['id']} closed. P&L: Rs {t['pnl']:,.0f}")
    elif cmd == "stats":
        print(stats())
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
