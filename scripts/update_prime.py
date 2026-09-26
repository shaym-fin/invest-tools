#!/usr/bin/env python3
"""
בודק את ריבית בנק ישראל ושומר את ריבית הפריים (ריבית בנק ישראל + 1.5%) בקובץ data/prime.json.

רץ כל יום חול בערב דרך GitHub Actions, אבל פונה לבנק ישראל רק כשהגיע מועד
החלטת הריבית הבא (לפי התאריך שבנק ישראל עצמו מפרסם), או אם עבר חודש מהבדיקה האחרונה.
"""
import json, os, sys, urllib.request
from datetime import datetime, timezone, timedelta

URL = "https://www.boi.org.il/PublicApi/GetInterest"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "prime.json")
SPREAD = 1.5
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def load():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def due(cur, now):
    if not cur or "--force" in sys.argv:
        return True
    nxt = cur.get("nextDecision")
    if not nxt or now.date() >= datetime.fromisoformat(nxt).date():
        return True
    checked = cur.get("checked")
    return not checked or now - datetime.fromisoformat(checked) > timedelta(days=30)


def main():
    now = datetime.now(timezone.utc)
    cur = load()
    if not due(cur, now):
        print(f"עוד לא הגיע מועד ההחלטה הבא ({cur.get('nextDecision')}). אין מה לבדוק.")
        return
    req = urllib.request.Request(URL, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        boi = float(data["currentInterest"])
    except Exception as e:
        # לא מכשילים את ההרצה: נשארים עם הערך האחרון ומנסים שוב בפעם הבאה
        print(f"לא הצלחתי לקרוא את הריבית מבנק ישראל ({e}). נשאר הערך הקודם.")
        return
    nxt = data.get("nextInterestDate")
    nxt = nxt[:10] if nxt else None
    out = {
        "boi": boi,
        "prime": round(boi + SPREAD, 2),
        "nextDecision": nxt,
        "checked": now.isoformat(timespec="seconds"),
        "changed": cur.get("changed") if cur and cur.get("boi") == boi else now.strftime("%Y-%m-%d"),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"ריבית בנק ישראל {boi}%, פריים {out['prime']}%, ההחלטה הבאה {nxt}")


if __name__ == "__main__":
    main()
