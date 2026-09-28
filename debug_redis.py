"""调试：登录后检查 Redis 里 token 是否存在。"""
import sys
import requests  # 用 urllib 也行，这里用 requests 简洁；若无则用标准库

import redis as r

R = r.Redis(
    host="182.254.156.80", port=6379, password="Snowbb661", db=1,
    decode_responses=True, socket_connect_timeout=5,
)

print("=== Redis 连通 ===")
print("PING:", R.ping())

print("\n=== 当前 token 键 ===")
keys = R.keys("token:*")
print(f"共 {len(keys)} 个 token 键:")
for k in keys:
    print(f"  {k} = {R.get(k)}  TTL={R.ttl(k)}s")

if not keys:
    print("  (空 —— 说明登录时没写入，或已全部过期/被删)")

# 做一次登录，立即查 Redis
print("\n=== 模拟登录并立即检查 ===")
import json, urllib.request
data = json.dumps({"account": "bob", "password": "Pass1234"}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:5000/login", data=data,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req) as resp:
    body = json.loads(resp.read())
token = body["data"]["token"]
print("登录返回 token:", token)

print("\n登录后 Redis token 键:")
keys = R.keys("token:*")
for k in keys:
    print(f"  {k} = {R.get(k)}  TTL={R.ttl(k)}s")

print(f"\n校验该 token 是否在 Redis: {R.get('token:' + token)}")

# 用该 token 访问 /user
print("\n=== 用该 token 访问 /user ===")
req2 = urllib.request.Request(
    "http://127.0.0.1:5000/user",
    headers={"Authorization": f"Bearer {token}"},
)
try:
    with urllib.request.urlopen(req2) as resp:
        print("状态:", resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print("状态:", e.code)
    print(e.read().decode())
