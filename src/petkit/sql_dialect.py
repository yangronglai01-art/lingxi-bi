"""Pure SQL formatting; no connection, model client or legacy engine import."""
from src import config


def date_fmt(column,fmt="%Y-%m"):
    function="date_format" if config.QUERY_ENGINE=="starrocks" else "strftime"
    return f"{function}({column}, '{fmt}')"
