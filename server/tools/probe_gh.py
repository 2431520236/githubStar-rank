import json
import requests
from app import config as cfg

tok = cfg.GITHUB_TOKEN
print("token len=%d prefix=%s" % (len(tok), tok[:6]))
H = {"Accept": "application/vnd.github+json", "User-Agent": "GitHubStarRadar/1.0"}
if tok:
    H["Authorization"] = "Bearer " + tok

def call(url):
    try:
        r = requests.get(url, headers=H, timeout=25)
        return r.status_code, dict(r.headers), r.text
    except Exception as e:
        return 0, {}, str(e)[:150]

st, h, body = call("https://api.github.com/rate_limit")
print("[rate_limit] HTTP=%s limit=%s remaining=%s" % (st, h.get("X-RateLimit-Limit"), h.get("X-RateLimit-Remaining")))
if st == 200:
    c = json.loads(body).get("resources", {}).get("core", {})
    print("  core limit=%s used=%s remaining=%s" % (c.get("limit"), c.get("used"), c.get("remaining")))
else:
    print("  body:", body[:150].replace(chr(10), " "))

st2, h2, b2 = call("https://api.github.com/repos/torvalds/linux/stargazers/history")
print("[stargazers/history] HTTP=%s" % st2)
print("  Link:", (h2.get("Link") or "(none)")[:120])
if st2 == 200:
    d = json.loads(b2)
    print("  type=%s len=%s" % (type(d).__name__, len(d)))
    if isinstance(d, list) and d:
        print("  first_keys:", list(d[0].keys()))
        print("  first:", json.dumps(d[0], ensure_ascii=False)[:350])
        print("  last:", json.dumps(d[-1], ensure_ascii=False)[:200])
else:
    print("  body:", b2[:150].replace(chr(10), " "))
