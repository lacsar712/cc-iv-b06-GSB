# 光伏组串IV扫描台

扫描员提交组串开路电压、短路电流与填充因子。写入后走 PostgreSQL 通知通道叫醒独立工人，工人不轮询空转。填充因子不低于 0.72 为合格，否则衰减。页面是 Vue 3。

每个组串归属于一台箱变。**箱变油样过期后，该箱变底下所有组串一律不准入队**：报送在同一事务内对箱变台账行加 `FOR UPDATE` 行锁、按数据库 `CURRENT_DATE` 判定到期日，过期即返回 409 并提示重新化验，同时在台账留下"拦住"理由。到期日只存于 `transformers.oil_expiry_date` 一列，报送拦截口与顶栏油样台账专页读同一张表，不存在两份台账不一致。

## 技术栈

- 后端：Litestar、Uvicorn、psycopg 同步写入
- 工人：`LISTEN/NOTIFY` 唤醒后认领
- 前端：Vue 3、Vite、nginx 反代 `/api`

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3202 |
| 接口 | http://localhost:8202 |
| PostgreSQL | localhost:54402（库名 `pvivscan`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| scanner | scan123456 | 可报送、可改油样到期日、可续检 |
| watcher | watch123456 | 只读（不能报送、不能改期、不能续检） |

## 启动

```bash
docker compose up --build
```

健康检查：`GET http://localhost:8202/api/health`

## 油样规则与接口

- 到期判定：`oil_expiry_date < CURRENT_DATE` 即过期；到期当天仍有效。
- `POST /api/logs`：报送必须带 `transformer_code`。过期箱变报送返回 `409`，响应明细即台账"最近一次拦住理由"，被拦数据不进队列表，只写 `oil_events` 的 `block` 痕迹。
- `PUT /api/transformers/{code}/expiry`：writer 修改到期日（留 `set` 痕，含新旧值）。
- `POST /api/transformers/{code}/renew`：writer 登记重新化验续检，到期日默认延至一年后（留 `renew` 痕）。
- `GET /api/transformers`：台账专页数据——各箱变到期日、过期状态、最近一次拦住理由与时间。
- `GET /api/oil-events`：`set / renew / block / accept` 全部留痕。
- 改期、续检、报送都对同一箱变行加锁串行：续检与报送撞车时结局只有一种，不会一边收下一边还挂过期。

## 种子

| 组串 | 箱变 | 填充因子 | 结论 |
|------|------|----------|------|
| 阵列A-串03 | 变甲（油样 30 天后到期） | 0.78 | 合格 |
| 阵列B-串11 | 变乙（油样已过期 10 天） | 0.61 | 衰减 |

验收路径：变甲有效期内可报送 → 把变甲到期日改成昨天再送应被 409 拒绝、台账出现拦住理由 → 对变甲续检后再送可入队，并在留痕表看到 `set/block/renew/accept` 全过程；watcher 账号全程只读。
