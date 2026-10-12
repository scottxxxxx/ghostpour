import asyncio, json, httpx
from app.services import app_store_server_api as A
from app.services.apple_notifications import decode_and_verify_jws
from app.config import get_settings

s = get_settings()
print("configured:", A.is_configured(), "| app_store_environment:", s.app_store_environment, "| bid:", A._bid())

# 2026-08-27T00:00Z .. 2026-08-29T00:00Z
START = 1787788800000
END   = 1787961600000

async def hist(base, label):
    url = f"{base}/inApps/v1/notifications/history"
    body = {"startDate": START, "endDate": END}
    out = []
    token = None
    async with httpx.AsyncClient(timeout=30.0) as c:
        for _ in range(5):
            u = url + (f"?paginationToken={token}" if token else "")
            r = await c.post(u, json=body, headers={"Authorization": f"Bearer {A._signed_jwt()}"})
            if r.status_code != 200:
                print(f"[{label}] HTTP {r.status_code}: {r.text[:300]}")
                return
            d = r.json()
            out.extend(d.get("notificationHistory", []))
            token = d.get("paginationToken")
            if not d.get("hasMore"):
                break
    print(f"[{label}] {len(out)} notifications in window")
    for n in out:
        sp = n.get("signedPayload")
        try:
            p = decode_and_verify_jws(sp, s.apple_bundle_id)
        except Exception as e:
            print(f"  [{label}] undecodable: {e}")
            continue
        ntype = p.get("notificationType"); sub = p.get("subtype")
        data = p.get("data") or {}
        env = data.get("environment")
        otid = None; tid = None; prod = None; sd = None
        st = data.get("signedTransactionInfo")
        if st:
            try:
                ti = decode_and_verify_jws(st, s.apple_bundle_id)
                otid = ti.get("originalTransactionId"); tid = ti.get("transactionId")
                prod = ti.get("productId"); sd = ti.get("appAccountToken")
            except Exception as e:
                print("   txn decode err", e)
        print(f"  [{label}] {ntype}/{sub} env={env} otid={otid} txn={tid} product={prod} appAccountToken={sd}")

asyncio.run(hist(A._PROD_BASE, "PROD"))
asyncio.run(hist(A._SANDBOX_BASE, "SANDBOX"))
