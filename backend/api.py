import os
from datetime import date, datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, put
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import judge

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
        now = datetime.now(timezone.utc)
        # 种子箱变：变甲油样 30 天后到期（有效期内），变乙已过期 10 天。
        n = conn.execute("SELECT COUNT(*) AS n FROM transformers").fetchone()["n"]
        if n == 0:
            today = date.today()
            seed_tx = [
                ("变甲", today + timedelta(days=30)),
                ("变乙", today - timedelta(days=10)),
            ]
            for code, expiry in seed_tx:
                conn.execute(
                    """INSERT INTO transformers (code, oil_expiry_date, updated_by, updated_at)
                       VALUES (%s, %s, 'system', %s)""",
                    (code, expiry, now),
                )
                conn.execute(
                    """INSERT INTO oil_events
                       (transformer_code, event_type, oil_expiry_date, detail, actor, created_at)
                       VALUES (%s, 'set', %s, '台账初始化', 'system', %s)""",
                    (code, expiry, now),
                )
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            samples = [
                ("阵列A-串03", "变甲", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", "变乙", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, tx, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, transformer_code, voc_v, isc_a, fill_factor, status,
                        verdict, reason, created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, tx, voc, isc, ff, verdict, reason, now, now),
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


def need_writer(request: Request, action: str = "该操作"):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail=f"只读账号无权{action}")
    return user


def parse_date(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
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
            """SELECT id, string_code, transformer_code, voc_v, isc_a, fill_factor, status,
                      verdict, reason, created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request, "报送IV扫描")
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    tx_code = (data.get("transformer_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    if not tx_code:
        raise HTTPException(status_code=400, detail="必须选择所属箱变")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        # 锁的是 transformers 这一行（到期日唯一存放处）：
        # 与改期/续检互斥，过期判定与入队在同一事务，撞车时每个请求只有一种结局。
        tx = conn.execute(
            """SELECT code, oil_expiry_date,
                      (oil_expiry_date < CURRENT_DATE) AS expired
               FROM transformers WHERE code = %s FOR UPDATE""",
            (tx_code,),
        ).fetchone()
        if tx is None:
            raise HTTPException(status_code=400, detail=f"箱变 {tx_code} 不存在")
        if tx["expired"]:
            reason = (
                f"箱变{tx['code']}油样已于 {tx['oil_expiry_date'].isoformat()} 过期，"
                f"组串{code}报送被拦，请重新化验（续检）后再报送"
            )
            # 拦住理由写进台账留痕，提交后再返回 409。
            conn.execute(
                """INSERT INTO oil_events
                   (transformer_code, event_type, oil_expiry_date, detail, string_code,
                    actor, created_at)
                   VALUES (%s,'block',%s,%s,%s,%s,%s)""",
                (tx["code"], tx["oil_expiry_date"], reason, code, user["username"], now),
            )
            conn.commit()
            raise HTTPException(status_code=409, detail=reason)
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, transformer_code, voc_v, isc_a, fill_factor, status,
                created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, transformer_code, voc_v, isc_a, fill_factor,
                         status, verdict, reason, created_by, created_at, processed_at""",
            (code, tx["code"], voc, isc, ff, user["username"], now),
        ).fetchone()
        conn.execute(
            """INSERT INTO oil_events
               (transformer_code, event_type, oil_expiry_date, detail, string_code,
                actor, created_at)
               VALUES (%s,'accept',%s,%s,%s,%s,%s)""",
            (
                tx["code"],
                tx["oil_expiry_date"],
                f"组串{code}报送入队，油样有效期至 {tx['oil_expiry_date'].isoformat()}",
                code,
                user["username"],
                now,
            ),
        )
        conn.commit()
        return dump(row)


@get("/api/transformers")
async def list_transformers(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT t.code, t.oil_expiry_date,
                      (t.oil_expiry_date < CURRENT_DATE) AS expired,
                      t.updated_by, t.updated_at,
                      b.detail        AS last_block_reason,
                      b.created_at    AS last_block_at,
                      b.string_code   AS last_block_string
               FROM transformers t
               LEFT JOIN LATERAL (
                   SELECT detail, created_at, string_code
                   FROM oil_events
                   WHERE transformer_code = t.code AND event_type = 'block'
                   ORDER BY id DESC LIMIT 1
               ) b ON true
               ORDER BY t.code"""
        ).fetchall()
        return [dump(r) for r in rows]


@get("/api/oil-events")
async def list_oil_events(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, transformer_code, event_type, oil_expiry_date, detail,
                      string_code, actor, created_at
               FROM oil_events ORDER BY id DESC LIMIT 200"""
        ).fetchall()
        return [dump(r) for r in rows]


@put("/api/transformers/{code:str}/expiry")
async def set_expiry(code: str, request: Request) -> dict:
    user = need_writer(request, "修改油样到期日")
    data = await request.json()
    expiry = parse_date(data.get("oil_expiry_date"))
    now = datetime.now(timezone.utc)
    with connect() as conn:
        # 同一把行锁：改期与报送/续检串行，过期判定永远基于最新到期日。
        tx = conn.execute(
            "SELECT code, oil_expiry_date FROM transformers WHERE code = %s FOR UPDATE",
            (code,),
        ).fetchone()
        if tx is None:
            raise HTTPException(status_code=404, detail=f"箱变 {code} 不存在")
        conn.execute(
            "UPDATE transformers SET oil_expiry_date=%s, updated_by=%s, updated_at=%s WHERE code=%s",
            (expiry, user["username"], now, code),
        )
        conn.execute(
            """INSERT INTO oil_events
               (transformer_code, event_type, oil_expiry_date, detail, actor, created_at)
               VALUES (%s,'set',%s,%s,%s,%s)""",
            (
                code,
                expiry,
                f"到期日由 {tx['oil_expiry_date'].isoformat()} 改为 {expiry.isoformat()}",
                user["username"],
                now,
            ),
        )
        conn.commit()
    return {"code": code, "oil_expiry_date": expiry.isoformat()}


@post("/api/transformers/{code:str}/renew")
async def renew_oil(code: str, request: Request) -> dict:
    """重新化验后续检，默认有效期自今日起一年；与报送共用行锁，结局唯一。"""
    user = need_writer(request, "登记续检")
    data = await request.json() if request.headers.get("content-type") else {}
    now = datetime.now(timezone.utc)
    with connect() as conn:
        tx = conn.execute(
            "SELECT code, oil_expiry_date FROM transformers WHERE code = %s FOR UPDATE",
            (code,),
        ).fetchone()
        if tx is None:
            raise HTTPException(status_code=404, detail=f"箱变 {code} 不存在")
        if data and data.get("oil_expiry_date"):
            expiry = parse_date(data["oil_expiry_date"])
        else:
            row = conn.execute(
                "SELECT (CURRENT_DATE + INTERVAL '1 year')::date AS d"
            ).fetchone()
            expiry = row["d"]
        conn.execute(
            "UPDATE transformers SET oil_expiry_date=%s, updated_by=%s, updated_at=%s WHERE code=%s",
            (expiry, user["username"], now, code),
        )
        conn.execute(
            """INSERT INTO oil_events
               (transformer_code, event_type, oil_expiry_date, detail, actor, created_at)
               VALUES (%s,'renew',%s,%s,%s,%s)""",
            (
                code,
                expiry,
                f"油样重新化验合格，续检至 {expiry.isoformat()}"
                f"（原到期 {tx['oil_expiry_date'].isoformat()}）",
                user["username"],
                now,
            ),
        )
        conn.commit()
    return {"code": code, "oil_expiry_date": expiry.isoformat()}


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        list_transformers,
        list_oil_events,
        create_log,
        set_expiry,
        renew_oil,
    ]
)
