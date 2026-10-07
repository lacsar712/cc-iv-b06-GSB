<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。油样过期箱变底下的组串一律不准入队。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>
    <div v-else>
      <nav class="topbar">
        <span class="who">已登录：{{ session.username }}（{{ isWriter ? "可提交" : "只读" }}）</span>
        <button :class="{ active: tab === 'report' }" @click="tab = 'report'">组串报送</button>
        <button :class="{ active: tab === 'ledger' }" @click="tab = 'ledger'">油样台账</button>
        <button class="secondary" @click="logout">退出</button>
        <button class="secondary" @click="refresh">刷新</button>
      </nav>

      <section v-if="tab === 'report'">
        <div v-if="isWriter">
          <label>所属箱变</label>
          <select v-model="txCode">
            <option value="" disabled>请选择箱变</option>
            <option v-for="t in transformers" :key="t.code" :value="t.code">
              {{ t.code }}（油样{{ t.expired ? "已过期 " + fmtDay(t.oil_expiry_date) : "有效至 " + fmtDay(t.oil_expiry_date) }}）
            </option>
          </select>
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
          <p v-if="okMsg" class="okmsg">{{ okMsg }}</p>
        </div>
        <p v-else class="sub">旁观账号只读，不能报送。</p>
        <table>
          <thead>
            <tr><th>编号</th><th>组串</th><th>箱变</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in logs" :key="row.id">
              <td>{{ row.id }}</td>
              <td>{{ row.string_code }}</td>
              <td>{{ row.transformer_code || "—" }}</td>
              <td>{{ row.voc_v }}</td>
              <td>{{ row.isc_a }}</td>
              <td>{{ row.fill_factor }}</td>
              <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
              <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="tab === 'ledger'">
        <h2>油样台账</h2>
        <p class="sub">到期日是拦截口与本页共同读取的唯一台账；过期箱变底下所有组串报送一律拦截，需重新化验后续检。</p>
        <table>
          <thead>
            <tr>
              <th>箱变</th><th>油样到期日</th><th>状态</th><th>最近一次拦住理由</th><th>拦截时间</th>
              <th v-if="isWriter">台账操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in transformers" :key="t.code">
              <td>{{ t.code }}</td>
              <td>{{ fmtDay(t.oil_expiry_date) }}</td>
              <td><span class="tag" :class="t.expired ? 'bad' : 'ok'">{{ t.expired ? "已过期" : "有效期内" }}</span></td>
              <td>{{ t.last_block_reason || "—" }}</td>
              <td>{{ t.last_block_at ? fmtTime(t.last_block_at) : "—" }}</td>
              <td v-if="isWriter">
                <input class="inline-date" type="date" v-model="dateEdits[t.code]" />
                <button class="mini" @click="saveExpiry(t)">改到期日</button>
                <button class="mini" @click="renew(t)">续检（+1年）</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="ledgerMsg" :class="ledgerError ? 'err' : 'okmsg'">{{ ledgerMsg }}</p>

        <h3>油样留痕（改期 / 续检 / 拦住 / 入队）</h3>
        <table>
          <thead>
            <tr><th>时间</th><th>箱变</th><th>类型</th><th>到期日</th><th>说明</th><th>组串</th><th>操作人</th></tr>
          </thead>
          <tbody>
            <tr v-for="e in oilEvents" :key="e.id">
              <td>{{ fmtTime(e.created_at) }}</td>
              <td>{{ e.transformer_code }}</td>
              <td><span class="tag" :class="eventClass(e.event_type)">{{ eventLabel(e.event_type) }}</span></td>
              <td>{{ e.oil_expiry_date ? fmtDay(e.oil_expiry_date) : "—" }}</td>
              <td>{{ e.detail }}</td>
              <td>{{ e.string_code || "—" }}</td>
              <td>{{ e.actor }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
const session = ref(null);
const logs = ref([]);
const transformers = ref([]);
const oilEvents = ref([]);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const tab = ref("report");
const txCode = ref("");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const okMsg = ref("");
const ledgerMsg = ref("");
const ledgerError = ref(false);
const loading = ref(false);
const dateEdits = reactive({});
let timer;
const isWriter = computed(() => session.value?.role === "writer");
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmtDay(v) {
  return v ? String(v).slice(0, 10) : "—";
}
function fmtTime(v) {
  return v ? new Date(v).toLocaleString("zh-CN", { hour12: false }) : "—";
}
function eventLabel(t) {
  return { set: "改期", renew: "续检", block: "拦住", accept: "入队" }[t] || t;
}
function eventClass(t) {
  return { block: "bad", accept: "ok", renew: "ok", set: "pending" }[t] || "pending";
}
async function getJson(url) {
  const res = await fetch(url, { headers: headers() });
  if (res.status === 401) { logout(); return null; }
  return res.ok ? await res.json() : null;
}
async function refresh() {
  if (!session.value) return;
  const [l, t, e] = await Promise.all([
    getJson("/api/logs"),
    getJson("/api/transformers"),
    getJson("/api/oil-events"),
  ]);
  if (l) logs.value = l;
  if (t) {
    transformers.value = t;
    for (const x of t) if (!(x.code in dateEdits)) dateEdits[x.code] = fmtDay(x.oil_expiry_date);
  }
  if (e) oilEvents.value = e;
}
async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refresh();
    timer = setInterval(refresh, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  transformers.value = [];
  oilEvents.value = [];
  localStorage.removeItem("pv_session");
}
async function submit() {
  error.value = "";
  okMsg.value = "";
  if (!txCode.value) { error.value = "必须选择所属箱变"; return; }
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        transformer_code: txCode.value,
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      error.value = (res.status === 409 ? "【拦截】" : "") + (data.detail || "提交失败");
      await refresh();
      return;
    }
    okMsg.value = `组串 ${data.string_code} 已入队（编号 ${data.id}）`;
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refresh();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
async function saveExpiry(t) {
  ledgerMsg.value = "";
  const d = dateEdits[t.code];
  if (!d) { ledgerError.value = true; ledgerMsg.value = "请先选择日期"; return; }
  const res = await fetch(`/api/transformers/${encodeURIComponent(t.code)}/expiry`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...headers() },
    body: JSON.stringify({ oil_expiry_date: d }),
  });
  const data = await res.json().catch(() => ({}));
  ledgerError.value = !res.ok;
  ledgerMsg.value = res.ok ? `${t.code} 到期日已改为 ${d}` : data.detail || "改期失败";
  await refresh();
}
async function renew(t) {
  ledgerMsg.value = "";
  const res = await fetch(`/api/transformers/${encodeURIComponent(t.code)}/renew`, {
    method: "POST",
    headers: { ...headers() },
  });
  const data = await res.json().catch(() => ({}));
  ledgerError.value = !res.ok;
  ledgerMsg.value = res.ok ? `${t.code} 已续检，新到期日 ${data.oil_expiry_date}` : data.detail || "续检失败";
  await refresh();
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refresh();
      timer = setInterval(refresh, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1080px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.25rem; }
h2 { margin: 0 0 0.5rem; color: #bbf7d0; }
h3 { margin: 1.25rem 0 0.5rem; color: #bbf7d0; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
.topbar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; }
.topbar .who { margin-right: auto; color: #a7f3d0; }
nav button.active { background: #22c55e; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.mini { padding: 0.3rem 0.6rem; font-size: 0.8rem; margin: 0.15rem 0.3rem 0.15rem 0; }
.inline-date { width: auto; margin-bottom: 0; display: inline-block; }
.err { color: #fecaca; }
.okmsg { color: #bbf7d0; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: top; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; white-space: nowrap; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
</style>
