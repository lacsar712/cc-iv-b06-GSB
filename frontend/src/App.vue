<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>
    <div v-else>
      <header class="topbar">
        <nav>
          <button class="tab" :class="{ active: tab === 'scan' }" @click="switchTab('scan')">扫描报送</button>
          <button class="tab" :class="{ active: tab === 'ledger' }" @click="switchTab('ledger')">油样台账</button>
        </nav>
        <div class="who">
          {{ session.username }}（{{ isWriter ? "可写" : "只读旁观" }}）
          <button class="secondary" @click="logout">退出</button>
        </div>
      </header>

      <!-- ============ 扫描报送 ============ -->
      <div v-show="tab === 'scan'">
        <p class="sub">油样过期箱变底下的组串一律不得入队，报送前先查油样台账到期日。</p>
        <section v-if="isWriter">
          <label>所属箱变</label>
          <select v-model="transformerId">
            <option :value="null">请选择箱变</option>
            <option v-for="t in transformers" :key="t.id" :value="t.id">
              {{ t.name }}（油样{{ t.expired ? "已过期 " + t.oil_expiry_date : "有效至 " + t.oil_expiry_date }}）
            </option>
          </select>
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">报送入队</button>
          <p v-if="error" class="err">⛔ {{ error }}</p>
        </section>
        <section>
          <div class="section-head">
            <h3>报送记录</h3>
            <button class="secondary" @click="refreshLogs">刷新</button>
          </div>
          <table>
            <thead>
              <tr><th>编号</th><th>箱变</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.transformer_name || "—" }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td><span class="tag" :class="statusClass(row.status)">{{ statusText(row.status) }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
              <tr v-if="!logs.length"><td colspan="8" class="empty">暂无报送记录</td></tr>
            </tbody>
          </table>
        </section>
      </div>

      <!-- ============ 油样台账 ============ -->
      <div v-show="tab === 'ledger'">
        <p class="sub">
          台账到期日即拦截口依据：过期箱变底下的报送一律挡回并提示重新化验；
          {{ isWriter ? "可写账号可登记续检、修改到期日。" : "旁观账号只读，所有写操作均不可用。" }}
        </p>
        <section>
          <div class="section-head">
            <h3>各箱变油样台账</h3>
            <button class="secondary" @click="refreshLedger">刷新</button>
          </div>
          <table>
            <thead>
              <tr>
                <th>箱变</th><th>油样到期日</th><th>状态</th><th>最近一次拦住理由</th><th>最近续检</th>
                <th v-if="isWriter">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in transformers" :key="t.id"
                  :class="{ picked: pickedId === t.id, expiredrow: t.expired }"
                  @click="pick(t.id)">
                <td><strong>{{ t.name }}</strong></td>
                <td>
                  <span v-if="!isWriter || editingId !== t.id">{{ t.oil_expiry_date.slice(0, 10) }}</span>
                  <input v-else type="date" v-model="editDate" class="date-input" @click.stop />
                </td>
                <td>
                  <span class="tag" :class="t.expired ? 'bad' : 'ok'">
                    {{ t.expired ? "已过期 · 拦截中" : "有效" }}
                  </span>
                </td>
                <td class="reason-cell">
                  <template v-if="t.last_block_reason">
                    <div>{{ t.last_block_reason }}</div>
                    <div class="muted">{{ fmt(t.last_block_at) }}</div>
                  </template>
                  <span v-else class="muted">—</span>
                </td>
                <td class="muted">
                  <template v-if="t.renewed_at">{{ fmt(t.renewed_at) }}（{{ t.renewed_by }}）</template>
                  <span v-else>—</span>
                </td>
                <td v-if="isWriter" @click.stop>
                  <template v-if="editingId === t.id">
                    <button class="secondary" @click="saveExpiry(t)">保存到期日</button>
                    <button class="secondary" @click="editingId = null">取消</button>
                  </template>
                  <template v-else>
                    <button class="secondary" @click="startEdit(t)">改到期日</button>
                    <select v-model="renewMonths[t.id]" class="months">
                      <option :value="6">6个月</option>
                      <option :value="12">12个月</option>
                      <option :value="24">24个月</option>
                    </select>
                    <button @click="renew(t)">登记续检</button>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section v-if="picked">
          <div class="section-head">
            <h3>{{ picked.name }} · 油样痕迹（拦截 / 续检 / 改期 / 报送入队）</h3>
          </div>
          <table>
            <thead><tr><th>时间</th><th>类型</th><th>事由</th><th>操作人</th></tr></thead>
            <tbody>
              <tr v-for="e in events" :key="e.id">
                <td class="muted">{{ fmt(e.created_at) }}</td>
                <td><span class="tag" :class="eventClass(e.event_type)">{{ eventText(e.event_type) }}</span></td>
                <td>{{ e.detail }}</td>
                <td>{{ e.created_by }}</td>
              </tr>
              <tr v-if="!events.length"><td colspan="4" class="empty">暂无痕迹</td></tr>
            </tbody>
          </table>
        </section>
      </div>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const tab = ref("scan");
const logs = ref([]);
const transformers = ref([]);
const pickedId = ref(null);
const events = ref([]);
const editingId = ref(null);
const editDate = ref("");
const renewMonths = ref({});
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const transformerId = ref(null);
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const loading = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");
const picked = computed(() => transformers.value.find((t) => t.id === pickedId.value) || null);

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(ts) {
  if (!ts) return "";
  return ts.replace("T", " ").slice(0, 19) + " UTC";
}
function statusText(s) {
  return { pending: "待处理", done: "已完成", blocked: "已拦截" }[s] || s;
}
function statusClass(s) {
  return { pending: "pending", done: "ok", blocked: "bad" }[s] || "pending";
}
function eventText(k) {
  return { blocked: "拦截", renewed: "续检", expiry_changed: "改到期日", submitted: "报送入队" }[k] || k;
}
function eventClass(k) {
  return { blocked: "bad", renewed: "ok", expiry_changed: "pending", submitted: "ok" }[k] || "pending";
}

async function refreshLogs() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function refreshLedger() {
  if (!session.value) return;
  const res = await fetch("/api/transformers", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) {
    transformers.value = await res.json();
    for (const t of transformers.value) {
      if (renewMonths.value[t.id] === undefined) renewMonths.value[t.id] = 12;
    }
    if (pickedId.value) await loadEvents(pickedId.value);
  }
}
async function loadEvents(id) {
  const res = await fetch(`/api/transformers/${id}/events`, { headers: headers() });
  if (res.ok) events.value = await res.json();
}
function switchTab(t) {
  tab.value = t;
  error.value = "";
  if (t === "ledger") refreshLedger();
}
async function pick(id) {
  pickedId.value = id;
  await loadEvents(id);
}
function startEdit(t) {
  editingId.value = t.id;
  editDate.value = t.oil_expiry_date.slice(0, 10);
}
async function saveExpiry(t) {
  error.value = "";
  if (!editDate.value) { error.value = "请选择到期日"; return; }
  const res = await fetch(`/api/transformers/${t.id}/expiry`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...headers() },
    body: JSON.stringify({ oil_expiry_date: editDate.value }),
  });
  const data = await res.json();
  if (!res.ok) { error.value = data.detail || "修改失败"; return; }
  transformers.value = data;
  editingId.value = null;
  await loadEvents(t.id);
}
async function renew(t) {
  error.value = "";
  const res = await fetch(`/api/transformers/${t.id}/renew`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers() },
    body: JSON.stringify({ months: renewMonths.value[t.id] || 12 }),
  });
  const data = await res.json();
  if (!res.ok) { error.value = data.detail || "续检登记失败"; return; }
  transformers.value = data;
  await loadEvents(t.id);
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
    await Promise.all([refreshLogs(), refreshLedger()]);
    timer = setInterval(tick, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  transformers.value = [];
  pickedId.value = null;
  localStorage.removeItem("pv_session");
}
async function tick() {
  if (tab.value === "ledger") await refreshLedger();
  else await refreshLogs();
}
async function submit() {
  error.value = "";
  if (transformerId.value === null) { error.value = "请选择所属箱变"; return; }
  if (!stringCode.value.trim()) { error.value = "组串编号不能为空"; return; }
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        transformer_id: transformerId.value,
        string_code: stringCode.value.trim(),
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      error.value = data.detail || "报送被拒";
      await refreshLedger();
      return;
    }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await Promise.all([refreshLogs(), refreshLedger()]);
  } catch { error.value = "报送时网络异常"; }
  finally { loading.value = false; }
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      Promise.all([refreshLogs(), refreshLedger()]);
      timer = setInterval(tick, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1080px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.75rem; }
h3 { margin: 0; color: #bbf7d0; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary, button.tab { background: #365314; }
button.tab.active { background: #16a34a; }
.err { color: #fecaca; font-weight: 600; }
.muted { color: #86cb92; font-size: 0.82rem; }
.empty { color: #86cb92; text-align: center; }
.topbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem; flex-wrap: wrap; gap: 0.5rem; }
.who { color: #a7f3d0; font-size: 0.9rem; }
.section-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.6rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: top; }
.reason-cell { max-width: 300px; }
tr.picked { background: #166534; }
tr.expiredrow { box-shadow: inset 3px 0 0 #f87171; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; white-space: nowrap; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.date-input { width: 150px; margin-bottom: 0; }
.months { width: auto; margin: 0 0.4rem 0 0.4rem; }
</style>
