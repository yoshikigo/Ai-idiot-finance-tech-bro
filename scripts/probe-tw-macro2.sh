#!/usr/bin/env bash
# 台灣總經資料來源探測（第二輪）：列出下載網址、抓前幾行、主計總處 SDMX 維度、央行頁面表格。
# 用法：./scripts/probe-tw-macro2.sh > tw-macro-probe2.txt  然後把輸出貼給 Claude
set -u
OUT=$(mktemp -d)
UA="fin-terminal (curl)"
get() { curl -sS -L -m 60 -A "$UA" -o "$2" -w '%{http_code} %{content_type} %{size_download}' "$1" 2>&1 || true; }

echo "########## 1. data.gov.tw 資料集的下載網址與檔案開頭"
for id in 6099 6019 13228 130490 "$@"; do
  echo "=== dataset $id"
  get "https://data.gov.tw/api/v2/rest/dataset/$id" "$OUT/ds.json" >/dev/null
  python3 - "$OUT/ds.json" <<'PY' > "$OUT/urls.txt"
import json, sys
d = json.load(open(sys.argv[1]))["result"]
print("#", d.get("title"), "|", d.get("type"), "|", d.get("modifiedDate"))
for r in d.get("distribution", []):
    print(r.get("resourceFormat"), "\t", r.get("resourceDescription", "")[:60].replace("\n", " "), "\t", r.get("resourceDownloadUrl"))
PY
  cat "$OUT/urls.txt"
  awk -F'\t' 'NR>1 && $3 ~ /http/ {print $3}' "$OUT/urls.txt" | head -2 | while read -r u; do
    u=$(echo "$u" | xargs)
    echo "--- head of $u"
    echo "HTTP: $(get "$u" "$OUT/f")"
    python3 - "$OUT/f" <<'PY'
import sys
b = open(sys.argv[1], "rb").read()
for enc in ("utf-8-sig", "cp950", "big5"):
    try:
        t = b.decode(enc); break
    except UnicodeDecodeError:
        continue
else:
    t = b.decode("utf-8", "replace")
lines = t.splitlines()
print("\n".join(lines[:6])); print("..."); print("\n".join(lines[-3:]))
PY
  done
done

echo; echo "########## 2. 主計總處 SDMX：GDP（季）維度與期間"
get "https://nstatdb.dgbas.gov.tw/dgbasAll/webMain.aspx?sdmx/A018102060" "$OUT/gdp.json" >/dev/null
python3 - "$OUT/gdp.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
st = d["data"]["structure"] if "structure" in d.get("data", {}) else d.get("structure", {})
print("top keys:", list(d), "| data keys:", list(d.get("data", {})))
dims = st.get("dimensions", {})
for kind in ("series", "observation"):
    for dim in dims.get(kind, []):
        vals = dim.get("values", [])
        print(kind, dim.get("id"), dim.get("name"), len(vals), "values; first:", [(v.get("id"), v.get("name")) for v in vals[:8]], "last:", [(v.get("id"), v.get("name")) for v in vals[-2:]])
ser = d["data"]["dataSets"][0]["series"]
print("series keys (first 10):", list(ser)[:10], "count", len(ser))
PY
echo; echo "########## 3. 主計總處 SDMX：CPI 與失業率候選表"
for f in A030101011 A030101012 A030101013 A030101014 A030101015 A030101016 A030101017 A030101018 A030101019 A030101020 A030102010 A030102015 A046101010 A046101020 A046101030 A046102010 A046102020; do
  code=$(get "https://nstatdb.dgbas.gov.tw/dgbasAll/webMain.aspx?sdmx/$f" "$OUT/t.json")
  python3 - "$OUT/t.json" "$f" "$code" <<'PY'
import json, sys
f, code = sys.argv[2], sys.argv[3]
try:
    d = json.load(open(sys.argv[1]))
    st = d["data"]["structure"] if "structure" in d.get("data", {}) else d.get("structure", {})
    dims = st.get("dimensions", {})
    s = [(dim.get("name"), [v.get("name") for v in dim.get("values", [])[:6]]) for dim in dims.get("series", [])]
    o = dims.get("observation", [{}])[0].get("values", [])
    print(f, "OK", st.get("name") or "", "| series dims:", s, "| periods:", o[0].get("id") if o else None, "→", o[-1].get("id") if o else None)
except Exception:
    print(f, code, open(sys.argv[1], "rb").read()[:80])
PY
done

echo; echo "########## 4. 央行頁面表格"
for p in 640 643 641; do
  echo "=== lp-$p"
  get "https://www.cbc.gov.tw/tw/lp-$p-1.html" "$OUT/cbc.html" >/dev/null
  python3 - "$OUT/cbc.html" <<'PY'
import re, sys, html
t = open(sys.argv[1], encoding="utf-8", errors="replace").read()
for m in re.finditer(r"<table.*?</table>", t, re.S):
    rows = re.findall(r"<tr.*?</tr>", m.group(0), re.S)
    for r in rows[:8]:
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S)]
        print(" | ".join(cells))
    print("rows:", len(rows)); break
else:
    i = t.find("重貼現率") if "重貼現率" in t else 0
    print(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t[i:i + 3000]))[:1500])
links = sorted(set(re.findall(r'href="([^"]+\.(?:csv|xls|xlsx|json|ods))"', t)))
print("download links:", links[:10])
PY
done
rm -rf "$OUT"
