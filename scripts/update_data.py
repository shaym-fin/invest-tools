#!/usr/bin/env python3
"""
מושך נתוני תשואות מהמאגרים הפתוחים של רשות שוק ההון ב-data.gov.il
(פנסיה-נט וגמל-נט) ושומר אותם כקבצי JSON שהאתר קורא.

רץ אוטומטית דרך GitHub Actions. אפשר גם להריץ ידנית:
    python scripts/update_data.py
"""
import json, os, sys, time, urllib.request, urllib.parse
from collections import defaultdict
from datetime import datetime, timezone

API = "https://data.gov.il/api/3/action/"
DATASETS = {"pensia-net": "pension", "gemelnet": "gemel"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
UA = "Mozilla/5.0 (compatible; returns-site-updater/1.0)"
PAGE = 10000

# שמות אפשריים לכל שדה במאגר
FIELDS = {
    "id":      ["FUND_ID", "מספר קופה", "מספר קרן"],
    "name":    ["FUND_NAME", "שם קופה", "שם קרן"],
    "company": ["MANAGING_CORPORATION", "חברה מנהלת"],
    "cls":     ["FUND_CLASSIFICATION", "סיווג"],
    "spec":    ["SPECIALIZATION", "התמחות"],
    "subspec": ["SUB_SPECIALIZATION", "התמחות משנה"],
    "period":  ["REPORT_PERIOD", "תקופת דיווח"],
    "ret":     ["MONTHLY_YIELD", "תשואה חודשית"],
    "assets":  ["TOTAL_ASSETS", "סך נכסים"],
    "fee":     ["AVG_ANNUAL_MANAGEMENT_FEE"],
    "feeDep":  ["AVG_DEPOSIT_FEE"],
}
REQUIRED = ["id", "name", "period", "ret"]


def call(action, **params):
    url = API + action + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            if not data.get("success"):
                raise RuntimeError(data.get("error"))
            return data["result"]
        except Exception as e:
            wait = 5 * (attempt + 1)
            print(f"  ניסיון {attempt+1} נכשל ({e}), מנסה שוב בעוד {wait} שניות", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"הבקשה נכשלה: {url}")


def map_fields(names):
    lower = {n.lower(): n for n in names}
    m = {}
    for key, cands in FIELDS.items():
        for c in cands:
            if c.lower() in lower:
                m[key] = lower[c.lower()]
                break
    missing = [k for k in REQUIRED if k not in m]
    if missing:
        raise RuntimeError(f"לא נמצאו השדות {missing}. השדות במאגר: {names}")
    return m


def num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def period_of(v):
    s = "".join(ch for ch in str(v) if ch.isdigit())
    if len(s) >= 6:
        y, m = int(s[:4]), int(s[4:6])
        if 1990 < y < 2100 and 1 <= m <= 12:
            return y * 100 + m
    return None


def product_of(ds_product, cls):
    if ds_product == "pension":
        return "pension"
    return "hishtalmut" if cls and "השתלמות" in cls else "gemel"


def main():
    funds = {}                    # (product, id) -> פרטי המסלול מהדיווח האחרון
    returns = defaultdict(dict)   # (product, id) -> {period: monthly return %}
    latest = {}

    for slug, ds_product in DATASETS.items():
        pkg = call("package_show", id=slug)
        resources = [r for r in pkg["resources"] if r.get("datastore_active")]
        print(f"{slug}: {len(resources)} טבלאות")
        for res in resources:
            rid, offset, fmap = res["id"], 0, None
            while True:
                r = call("datastore_search", resource_id=rid, limit=PAGE, offset=offset)
                if fmap is None:
                    try:
                        fmap = map_fields([f["id"] for f in r["fields"]])
                    except RuntimeError:
                        # טבלה שאינה טבלת תשואות (למשל מילון שדות) - מדלגים עליה
                        print(f"  מדלג על {res.get('name', rid)}: אין בה נתוני תשואות")
                        break
                    print(f"  {res.get('name', rid)}: {r.get('total')} שורות")
                rows = r["records"]
                for row in rows:
                    fid = str(row.get(fmap["id"], "")).strip()
                    p = period_of(row.get(fmap["period"]))
                    ret = num(row.get(fmap["ret"]))
                    if not fid or p is None or ret is None:
                        continue
                    cls = str(row.get(fmap["cls"], "") or "").strip() if "cls" in fmap else ""
                    key = (product_of(ds_product, cls), fid)
                    returns[key][p] = ret
                    if p >= latest.get(key, 0):
                        latest[key] = p
                        txt = lambda k: str(row.get(fmap[k], "") or "").strip() if k in fmap else ""
                        val = lambda k: num(row.get(fmap[k])) if k in fmap else None
                        funds[key] = {
                            "id": fid, "name": txt("name"), "company": txt("company"),
                            "cls": cls, "spec": txt("spec"), "subspec": txt("subspec"),
                            "assets": val("assets"), "fee": val("fee"), "feeDep": val("feeDep"),
                            "last": p,
                        }
                offset += len(rows)
                if not rows or offset >= r.get("total", 0):
                    break

    if not funds:
        raise RuntimeError("לא נמשכו נתונים בכלל")

    os.makedirs(OUT, exist_ok=True)
    index = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "products": {}}
    for product in ["pension", "gemel", "hishtalmut"]:
        keys = [k for k in funds if k[0] == product]
        if not keys:
            continue
        periods = sorted({p for k in keys for p in returns[k]})
        pos = {p: i for i, p in enumerate(periods)}
        series = {}
        for k in keys:
            ps = sorted(returns[k])
            arr = [None] * (pos[ps[-1]] - pos[ps[0]] + 1)   # חודש חסר נשמר כ-null
            for p in ps:
                arr[pos[p] - pos[ps[0]]] = round(returns[k][p], 4)
            series[k[1]] = {"s": ps[0], "r": arr}
        with open(os.path.join(OUT, f"{product}.json"), "w", encoding="utf-8") as f:
            json.dump({"periods": periods, "funds": [funds[k] for k in keys], "series": series},
                      f, ensure_ascii=False, separators=(",", ":"))
        index["products"][product] = {"count": len(keys), "from": periods[0], "to": periods[-1]}
        print(f"{product}: {len(keys)} מסלולים, {periods[0]}–{periods[-1]}")

    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print("הסתיים בהצלחה")


if __name__ == "__main__":
    main()
