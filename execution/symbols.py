"""Ticker spelling: the database uses Yahoo's class separator (BRK-B), Alpaca a dot (BRK.B)."""


def to_alpaca(ticker: str) -> str:
    return ticker.replace("-", ".")


def from_alpaca(symbol: str) -> str:
    return symbol.replace(".", "-")
