"""Broker response contracts shared by monitoring and order reconciliation."""


def book_rows(result, label):
    if not isinstance(result, dict):
        raise ValueError(f"Kotak {label} response not a dict")
    # Kotak documents 5203 as an empty account book, not an auth failure.
    # Match both code and message; other errors must never become empty books.
    if (str(result.get("stCode")) == "5203" and
            str(result.get("errMsg", "")).strip().lower() == "no data" and
            not result.get("data") and not result.get("error")):
        return []
    if str(result.get("stat", "")).lower() not in {"ok", "success"}:
        raise ValueError(f"Kotak {label} unavailable")
    rows = result.get("data")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Kotak {label} returned no valid list")
    return rows


def authentication_rejected(result):
    if not isinstance(result, dict):
        return False
    if str(result.get("stCode")) in {"401", "403"}:
        return True
    errors = result.get("error", [])
    if isinstance(errors, dict):
        errors = [errors]
    if not isinstance(errors, list):
        errors = []
    if any(str(row.get("code")) in {"401", "403"} for row in errors if isinstance(row, dict)):
        return True
    messages = [result.get("errMsg", ""), result.get("desc", "")]
    messages += [row.get("message", "") for row in errors if isinstance(row, dict)]
    return any(phrase in str(message).lower() for message in messages for phrase in (
        "invalid session", "session expired", "session is expired", "invalid token",
        "token expired", "not authenticated", "please re-login"))
