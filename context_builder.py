"""
Builds the live "context" text (price action, indicators, earnings date, news)
that Bull/Bear/Judge read. Uses Yahoo Finance via yfinance, NSE symbols by default.

    pip install yfinance
    python context_builder.py AXISBANK 1180 1195
"""
import sys


def _ticker(symbol: str):
    import yfinance as yf

    s = symbol.upper()
    if not s.endswith((".NS", ".BO")):
        s += ".NS"  # NSE by default
    return yf.Ticker(s)


def _price_block(t, entry=None, target=None) -> list[str]:
    h = t.history(period="6mo", interval="1d")
    if h.empty:
        return ["Price data: unavailable"]
    close = h["Close"]
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    sma20 = float(close.tail(20).mean())
    sma50 = float(close.tail(50).mean())

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)

    avg_range = float(((h["High"] - h["Low"]) / h["Close"]).tail(14).mean() * 100)
    vol_ratio = float(h["Volume"].iloc[-1] / max(h["Volume"].tail(20).mean(), 1))
    hi20, lo20 = float(h["High"].tail(20).max()), float(h["Low"].tail(20).min())

    lines = [
        f"Last close {last:.2f} ({(last / prev - 1) * 100:+.2f}% vs previous day)",
        f"SMA20 {sma20:.2f}, SMA50 {sma50:.2f}, price is "
        f"{'above' if last > sma20 else 'below'} SMA20 and {'above' if last > sma50 else 'below'} SMA50",
        f"RSI(14) {rsi:.0f}",
        f"20-day high {hi20:.2f}, low {lo20:.2f}",
        f"Average daily range (14d) {avg_range:.2f}% of price",
        f"Latest volume is {vol_ratio:.1f}x the 20-day average",
    ]
    if entry and target:
        target_pct = abs(target - entry) / entry * 100
        lines.append(
            f"Target is {target_pct:.2f}% away vs typical daily range {avg_range:.2f}% "
            f"(target/range = {target_pct / avg_range:.2f})"
        )
    return lines


def _events_block(t) -> list[str]:
    try:
        cal = t.calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if dates:
            return ["Upcoming earnings date: " + ", ".join(str(d) for d in dates)]
    except Exception:
        pass
    return ["Upcoming earnings date: not found (check manually)"]


def _news_block(t, n=5) -> list[str]:
    try:
        items = t.news or []
    except Exception:
        return ["News: unavailable"]
    titles = []
    for item in items[:n]:
        title = item.get("title") or (item.get("content") or {}).get("title")
        if title:
            titles.append(f"- {title}")
    return ["Recent headlines:"] + titles if titles else ["News: none found"]


def build_context(symbol: str, entry=None, target=None) -> str:
    t = _ticker(symbol)
    parts = []
    for fn, args in ((_price_block, (t, entry, target)), (_events_block, (t,)), (_news_block, (t,))):
        try:
            parts += fn(*args)
        except Exception as e:  # one failing source should not kill the debate
            parts.append(f"({fn.__name__} failed: {e})")
    return "LIVE DATA\n" + "\n".join(parts)


if __name__ == "__main__":
    sym = sys.argv[1]
    e = float(sys.argv[2]) if len(sys.argv) > 2 else None
    tg = float(sys.argv[3]) if len(sys.argv) > 3 else None
    print(build_context(sym, e, tg))
