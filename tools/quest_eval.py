"""Quest 브라우저 탭에서 JS 를 실행하고 결과를 출력한다 (adb forward tcp:9222 필요).
사용법: python tools/quest_eval.py "<JS 식>" [탭 번호 또는 URL 일부]"""
import json, sys, urllib.request
import websocket

expr = sys.argv[1]
want = sys.argv[2] if len(sys.argv) > 2 else "xr.html"
tabs = json.load(urllib.request.urlopen("http://localhost:9222/json"))
tabs = [t for t in tabs if t["type"] == "page" and (want in t["url"] or t["id"] == want)]
if not tabs:
    sys.exit("탭을 찾지 못했어요")
tab = max(tabs, key=lambda t: int(t["id"]) if t["id"].isdigit() else -1)
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                    "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
while True:
    msg = json.loads(ws.recv())
    if msg.get("id") == 1:
        break
r = msg.get("result", {})
if "exceptionDetails" in r:
    print("오류:", r["exceptionDetails"].get("exception", {}).get("description") or r["exceptionDetails"])
else:
    v = r.get("result", {}).get("value")
    print(json.dumps(v, ensure_ascii=False, indent=1) if not isinstance(v, str) else v)
print(f"(탭 {tab['id']}: {tab['title']})", file=sys.stderr)
