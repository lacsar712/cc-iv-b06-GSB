import os
from datetime import date, datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, put
from litestar.exceptions import HTTPException
from litestar.status_codes import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_409_CONFLICT,
)
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import judge, oil_block_reason

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        tn = conn.execute("SELECT COUNT(*) AS n FROM transformers").fetchone()["n"]
        if tn == 0:
            today = datetime.now(timezone.utc).date()
            conn.execute(
                "INSERT INTO transformers (name, oil_expiry_date) VALUES (%s,%s),(%s,%s)",
                ("变甲", today + timedelta(days=30), "变乙", today - timedelta(days=1)),
            )
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            tr = conn.execute(
                "SELECT id FROM transformers WHERE name=%s", ("变甲",)
            ).fetchone()
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, transformer_id, voc_v, isc_a, fill_factor, status,
                        verdict, reason, created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, tr["id"], voc, isc, ff, verdict, reason, now, now),
                )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request, action: str = "此操作"):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail=f"旁观账号只读，无权{action}")
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login", status_code=200)
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.id, s.string_code, s.transformer_id, t.name AS transformer_name,
                      s.voc_v, s.isc_a, s.fill_factor, s.status, s.verdict, s.reason,
                      s.created_by, s.created_at, s.processed_at
               FROM iv_scans s
               LEFT JOIN transformers t ON t.id = s.transformer_id
               ORDER BY s.id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request, "报送IV扫描")
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        transformer_id = int(data.get("transformer_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="请选择该组串所属箱变")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")

    now = datetime.now(timezone.utc)
    blocked = None
    with connect() as conn:
        # 行锁串行：续检与报送撞同一箱变时必须一先一后，
        # 先看到过期的一方按过期处理，不会出现一边收下一边还挂过期。
        tr = conn.execute(
            "SELECT id, name, oil_expiry_date FROM transformers WHERE id=%s FOR UPDATE",
            (transformer_id,),
        ).fetchone()
        if tr is None:
            raise HTTPException(status_code=400, detail="箱变不存在")
        reason = oil_block_reason(tr["oil_expiry_date"], now.date())
        if reason:
            detail = f"报送组串 {code} 被拦：{reason}"
            conn.execute(
                """UPDATE transformers
                   SET last_block_reason=%s, last_block_at=%s WHERE id=%s""",
                (detail, now, tr["id"]),
            )
            conn.execute(
                """INSERT INTO oil_events
                   (transformer_id, event_type, detail, created_by, created_at)
                   VALUES (%s,'blocked',%s,%s,%s)""",
                (tr["id"], detail, user["username"], now),
            )
            conn.commit()
            blocked = reason
        else:
            with conn.transaction():
                row = conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, transformer_id, voc_v, isc_a, fill_factor, status,
                        created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
                       RETURNING id, string_code, transformer_id, voc_v, isc_a,
                                 fill_factor, status, verdict, reason,
                                 created_by, created_at, processed_at""",
                    (code, tr["id"], voc, isc, ff, user["username"], now),
                ).fetchone()
                conn.execute(
                    """INSERT INTO oil_events
                       (transformer_id, event_type, detail, scan_id, created_by, created_at)
                       VALUES (%s,'submitted',%s,%s,%s,%s)""",
                    (tr["id"], f"组串 {code} 报送入队", row["id"], user["username"], now),
                )
            conn.commit()
            out = dump(row)
            out["transformer_name"] = tr["name"]
    if blocked is not None:
        # 报送一律不得入队：不写 iv_scans、不触发通知，只留下拦截痕迹。
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail=blocked)
    return out


def _ledger_rows(conn):
    today = datetime.now(timezone.utc).date()
    rows = conn.execute(
        """SELECT id, name, oil_expiry_date, renewed_at, renewed_by,
                  last_block_reason, last_block_at
           FROM transformers ORDER BY id"""
    ).fetchall()
    out = []
    for r in rows:
        d = dump(r)
        d["expired"] = oil_block_reason(r["oil_expiry_date"], today) is not None
        out.append(d)
    return out


@get("/api/transformers")
async def list_transformers(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        return _ledger_rows(conn)


@get("/api/transformers/{tid:int}/events")
async def transformer_events(request: Request, tid: int) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, event_type, detail, scan_id, created_by, created_at
               FROM oil_events WHERE transformer_id=%s ORDER BY id DESC LIMIT 100""",
            (tid,),
        ).fetchall()
        return [dump(r) for r in rows]


@put("/api/transformers/{tid:int}/expiry")
async def set_expiry(request: Request, tid: int) -> list:
    user = need_writer(request, "修改油样到期日")
    data = await request.json()
    try:
        new_date = date.fromisoformat(str(data.get("oil_expiry_date") or "").strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="到期日格式应为 YYYY-MM-DD")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        with conn.transaction():
            tr = conn.execute(
                "SELECT id, oil_expiry_date FROM transformers WHERE id=%s FOR UPDATE",
                (tid,),
            ).fetchone()
            if tr is None:
                raise HTTPException(status_code=404, detail="箱变不存在")
            conn.execute(
                "UPDATE transformers SET oil_expiry_date=%s WHERE id=%s",
                (new_date, tid),
            )
            conn.execute(
                """INSERT INTO oil_events
                   (transformer_id, event_type, detail, created_by, created_at)
                   VALUES (%s,'expiry_changed',%s,%s,%s)""",
                (tid,
                 f"油样到期日由 {tr['oil_expiry_date'].isoformat()} 改为 {new_date.isoformat()}",
                 user["username"], now),
            )
        conn.commit()
        return _ledger_rows(conn)


@post("/api/transformers/{tid:int}/renew", status_code=200)
async def renew_oil(request: Request, tid: int) -> list:
    user = need_writer(request, "登记油样续检")
    data = await request.json() if request.headers.get("content-type", "").startswith(
        "application/json"
    ) else {}
    months = 12
    if data:
        try:
            months = int(data.get("months", 12))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="续检月数必须是整数")
    if not 1 <= months <= 60:
        raise HTTPException(status_code=400, detail="续检月数应在 1 至 60 之间")
    now = datetime.now(timezone.utc)
    today = now.date()
    with connect() as conn:
        # 与报送拦截口同一把行锁：续检提交的瞬间，并发报送只能在锁外等，
        # 拿到锁后看到的必是续检后的新到期日，结局唯一。
        with conn.transaction():
            tr = conn.execute(
                "SELECT id, oil_expiry_date FROM transformers WHERE id=%s FOR UPDATE",
                (tid,),
            ).fetchone()
            if tr is None:
                raise HTTPException(status_code=404, detail="箱变不存在")
            base = max(today, tr["oil_expiry_date"])
            new_date = base + timedelta(days=30 * months)
            conn.execute(
                """UPDATE transformers
                   SET oil_expiry_date=%s, renewed_at=%s, renewed_by=%s WHERE id=%s""",
                (new_date, now, user["username"], tid),
            )
            conn.execute(
                """INSERT INTO oil_events
                   (transformer_id, event_type, detail, created_by, created_at)
                   VALUES (%s,'renewed',%s,%s,%s)""",
                (tid, f"油样续检合格，到期日延至 {new_date.isoformat()}（{months} 个月）",
                 user["username"], now),
            )
        conn.commit()
        return _ledger_rows(conn)


app = Litestar(route_handlers=[
    health, login, list_logs, create_log,
    list_transformers, transformer_events, set_expiry, renew_oil,
])
