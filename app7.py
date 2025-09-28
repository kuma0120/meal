import streamlit as st
import pandas as pd
import datetime as dt
import altair as alt
from itertools import combinations
from math import floor
from supabase import create_client

st.set_page_config(
    page_title="Dietary",
    layout="centered"
)

# ===============================
# Supabase 認証（secrets ガード）
# ===============================
def _create_supabase():
    try:
        url = st.secrets["SUPABASE_URL"].strip()
        key = st.secrets["SUPABASE_ANON_KEY"].strip()
    except Exception:
        st.error("SupabaseのURL/KEYが未設定です（.streamlit/secrets.toml を確認）")
        st.stop()
    return create_client(url, key)

supabase = _create_supabase()

# ===============================
# PostgREST にアクセストークンを適用（RLS用）
# ===============================
def _ensure_postgrest_auth():
    try:
        sess = supabase.auth.get_session()
        token = getattr(sess, "access_token", None)
        if not token:
            token = st.session_state.get("sb_access_token")
        if token:
            supabase.postgrest.auth(token)
    except Exception:
        pass

# ===============================
# Auth UI
# ===============================
def login_ui():
    st.title("ログイン")
    email = st.text_input("メールアドレス")
    pw = st.text_input("パスワード", type="password")
    col1, col2 = st.columns(2)

    with col1:
        if st.button("ログイン", disabled=(not email or not pw)):
            try:
                auth = supabase.auth.sign_in_with_password({"email": email, "password": pw})
                if not auth or not getattr(auth, "user", None):
                    st.error("サインインに失敗。メール未確認/資格情報誤りの可能性があります。")
                    return
                st.session_state["sb_access_token"] = getattr(getattr(auth, "session", None), "access_token", None)
                _ensure_postgrest_auth()
                me = supabase.auth.get_user().user
                if not me:
                    st.error("セッション取得に失敗。ブラウザのCookieやPC時刻を確認してください。")
                    return
                st.write("DEBUG user.id (after login) =", me.id)
                st.session_state["user"] = me
                st.rerun()
            except Exception as e:
                st.error(f"ログイン失敗: {e}")

    with col2:
        if st.button("新規登録", disabled=(not email or not pw)):
            try:
                redirect_to = st.secrets.get("EMAIL_REDIRECT_TO", "http://localhost:8501")
                supabase.auth.sign_up({
                    "email": email,
                    "password": pw,
                    "options": {"email_redirect_to": redirect_to},
                })
                st.success("登録しました！メールのリンクを開いてからログインしてください。")
            except Exception as e:
                st.error(f"登録失敗: {e}")

def logout():
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    st.session_state.pop("user", None)
    st.rerun()

def current_user():
    try:
        return supabase.auth.get_user().user
    except Exception:
        return None

# --- 認証チェック ---
if "user" not in st.session_state:
    u = current_user()
    if u is None:
        login_ui(); st.stop()
    _ensure_postgrest_auth()
    st.session_state["user"] = u
else:
    _ensure_postgrest_auth()

# ===============================
# DB: profiles 読み書き
# ===============================
def load_profile(user_id: str):
    _ensure_postgrest_auth()
    try:
        res = supabase.table("profiles").select("*").eq("id", user_id).single().execute()
        return res.data
    except Exception:
        return None

def save_profile(user_id: str, age, sex, height, weight_now, weight_goal, deadline, activity, daily_budget):
    _ensure_postgrest_auth()
    if not user_id:
        raise ValueError("ユーザー未ログインのため保存できません。")
    row = {
        "id": user_id,
        "age": int(age) if age is not None else None,
        "sex": sex,
        "height_cm": int(height) if height is not None else None,
        "weight_now": float(weight_now) if weight_now is not None else None,
        "weight_goal": float(weight_goal) if weight_goal is not None else None,
        "deadline": str(deadline) if deadline else None,
        "activity": activity,  # 表示名を保存（例: "軽い運動 (1.375)"）
        "daily_budget": int(daily_budget) if daily_budget is not None else None,
    }
    supabase.table("profiles").upsert(row, on_conflict="id").execute()

# ===============================
# DB: 体重ログ 読み書き
# ===============================
def log_weight(user_id: str, date: dt.date, weight: float):
    """同一(user_id,date)なら上書き保存"""
    _ensure_postgrest_auth()
    row = {"user_id": user_id, "date": str(date), "weight_kg": float(weight)}
    supabase.table("weights").upsert(row, on_conflict="user_id,date").execute()

def load_weight_history(user_id: str) -> pd.DataFrame:
    _ensure_postgrest_auth()
    res = supabase.table("weights").select("*").eq("user_id", user_id).order("date").execute()
    return pd.DataFrame(res.data or [])

# ===============================
# 商品取得
# ===============================
REQUIRED_COLS_DEFAULTS = {
    "store": "",
    "category": "",
    "name": "",
    "kcal": 0,
    "price_jpy": 0,
    "meal_slot_hint": "any",
    "protein_g": 0.0,
    "fat_g": 0.0,
    "carb_g": 0.0,
    "fiber_g": 0.0,
    "image_url": "",
    "url": ""
}
NUM_COLS = ["kcal", "price_jpy", "protein_g", "fat_g", "carb_g", "fiber_g"]

def _coerce_and_fill(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col, default in REQUIRED_COLS_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
    for c in NUM_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["meal_slot_hint"] = df["meal_slot_hint"].fillna("any")
    df["store"] = df["store"].astype(str)
    return df[list(REQUIRED_COLS_DEFAULTS.keys())]

@st.cache_data(ttl=300)
def load_products_for(store_name: str, slots: list[str]) -> pd.DataFrame:
    _ensure_postgrest_auth()
    res = (
        supabase.table("products")
        .select("store,category,name,kcal,price_jpy,meal_slot_hint,protein_g,fat_g,carb_g,fiber_g,image_url,url")
        .eq("store", store_name)
        .in_("meal_slot_hint", slots)
        .execute()
    )
    df = pd.DataFrame(res.data or [])
    return _coerce_and_fill(df)

# ===============================
# 活動量レベル（わかりやすい表示）
# ===============================
ACTIVITY_LEVELS = [
    {
        "key": "sedentary",
        "label": "ほぼ運動しない",
        "factor": 1.2,
        "hint": "デスクワーク中心。通勤や日常生活のみ",
        "weekly": "週0〜1回・〜20分の軽い散歩など"
    },
    {
        "key": "light",
        "label": "軽い運動",
        "factor": 1.375,
        "hint": "軽い運動を時々行う",
        "weekly": "週1〜3回・20〜40分（早歩き/ヨガ/軽い筋トレ）"
    },
    {
        "key": "moderate",
        "label": "中程度の運動",
        "factor": 1.55,
        "hint": "定期的に運動する",
        "weekly": "週3〜5回・30〜60分（ジョギング/筋トレ）"
    },
    {
        "key": "active",
        "label": "激しい運動",
        "factor": 1.725,
        "hint": "ほぼ毎日トレーニング",
        "weekly": "週6〜7回・45〜90分（高強度インターバル/競技スポーツ）"
    },
    {
        "key": "very_active",
        "label": "非常に激しい",
        "factor": 1.9,
        "hint": "二部練や重労働を伴う",
        "weekly": "毎日2回以上/重労働（アスリート/建設現場など）"
    },
]

# 表示名（例: "軽い運動 (1.375)"）を用意
ACTIVITY_DISPLAY = [f"{lvl['label']} ({lvl['factor']})" for lvl in ACTIVITY_LEVELS]
ACTIVITY_MAP = {f"{lvl['label']} ({lvl['factor']})": lvl["factor"] for lvl in ACTIVITY_LEVELS}
DEFAULT_ACTIVITY_DISPLAY = f"{ACTIVITY_LEVELS[1]['label']} ({ACTIVITY_LEVELS[1]['factor']})"  # 軽い運動(1.375)

def normalize_saved_activity(saved_display: str) -> str:
    """旧ラベル（例: '軽い運動(1.375)'）も新表示になるべく寄せる"""
    if saved_display in ACTIVITY_MAP:
        return saved_display
    import re
    m = re.search(r'([0-9]\.\d+)', str(saved_display))
    if m:
        try:
            f = float(m.group(1))
            for disp, fac in ACTIVITY_MAP.items():
                if abs(fac - f) < 1e-6:
                    return disp
        except Exception:
            pass
    base = str(saved_display).split("(")[0].replace("（", "(").replace("）", ")").strip()
    for disp in ACTIVITY_DISPLAY:
        if base and base in disp:
            return disp
    return DEFAULT_ACTIVITY_DISPLAY

def get_activity_factor(display: str) -> float:
    return ACTIVITY_MAP.get(display, ACTIVITY_LEVELS[1]["factor"])

# ===============================
# TDEE 計算まわり
# ===============================
def bmr_harris_benedict_revised(age, sex, height_cm, weight_kg):
    if sex == "male":
        return 88.362 + 13.397*weight_kg + 4.799*height_cm - 5.677*age
    else:
        return 447.593 + 9.247*weight_kg + 3.098*height_cm - 4.330*age

def tdee_kcal(age, sex, height_cm, weight_kg, activity_display):
    bmr = bmr_harris_benedict_revised(age, sex, height_cm, weight_kg)
    factor = get_activity_factor(activity_display)
    return floor(bmr * factor)

def calc_target_intake(age, sex, height, weight_now, weight_goal, deadline, activity_display):
    tdee = tdee_kcal(age, sex, height, weight_now, activity_display)
    days = max(1, (deadline - dt.date.today()).days)
    delta_w = max(0, weight_now - weight_goal)
    deficit_total = delta_w * 7700.0
    deficit_per_day = deficit_total / days
    intake = max(1200, int(tdee - deficit_per_day))
    return intake, tdee, int(deficit_per_day), days

def target_pfc_grams(intake_kcal, weight_kg, p_per_kg=1.6, f_ratio=0.25):
    p_g = weight_kg * p_per_kg
    f_g = (intake_kcal * f_ratio) / 9.0
    c_kcal = intake_kcal - (p_g*4 + f_g*9)
    c_g = max(0, c_kcal / 4.0)
    return p_g, f_g, c_g

FIBER_MIN_G = 18

# ===============================
# 組合せ生成＆最適化
# ===============================
def generate_item_combos(df_slot, budget, max_items=3):
    items = df_slot.to_dict("records")
    combos = []
    for r in range(1, min(max_items, len(items)) + 1):
        for comb in combinations(items, r):
            kcal  = sum(x["kcal"] for x in comb)
            price = sum(x["price_jpy"] for x in comb)
            if price <= budget:
                combos.append({
                    "kcal": kcal, "price": price, "items": comb,
                    "protein": sum(x["protein_g"] for x in comb),
                    "fat":     sum(x["fat_g"]     for x in comb),
                    "carb":    sum(x["carb_g"]    for x in comb),
                    "fiber":   sum(x["fiber_g"]   for x in comb),
                })
    return combos

def top_candidates_by_target(combos, target_kcal, keep_top=60):
    scored = [{"kcal":c["kcal"], "price":c["price"], "items":c["items"],
               "protein":c["protein"], "fat":c["fat"], "carb":c["carb"], "fiber":c["fiber"],
               "absdiff":abs(c["kcal"]-target_kcal)} for c in combos]
    scored.sort(key=lambda x: (x["absdiff"], x["price"]))
    return scored[:keep_top]

def plan_score(plan, tg_kcal, tg_p, tg_f, tg_c, fiber_min=FIBER_MIN_G,
               w_kcal=1.0, w_p=0.8, w_f=0.6, w_c=0.4, w_fiber=0.5, over_penalty=0.5):
    kcal = plan["kcal_total"]
    p = plan["protein_total"]; f = plan["fat_total"]; c = plan["carb_total"]; fiber = plan["fiber_total"]
    score = w_kcal * abs(kcal - tg_kcal)
    p_min, p_max = tg_p*0.90, tg_p*1.15
    f_min, f_max = tg_f*0.85, tg_f*1.15
    c_min, c_max = tg_c*0.85, tg_c*1.15
    if p < p_min: score += w_p * (p_min - p)
    elif p > p_max: score += w_p * over_penalty * (p - p_max)
    if f < f_min: score += w_f * (f_min - f)
    elif f > f_max: score += w_f * over_penalty * (f - f_max)
    if c < c_min: score += w_c * (c_min - c)
    elif c > c_max: score += w_c * over_penalty * (c - c_max)
    if fiber < fiber_min: score += w_fiber * (fiber_min - fiber)
    return score

def names_set(combo):
    return set(x["name"] for x in combo["items"])

def optimize_day_fixed_score_no_overlap(combos_b, combos_l, combos_d, intake, budget, weight_kg):
    # ★ 20/40/40 に変更
    t_b = int(intake*0.20); t_l = int(intake*0.40); t_d = intake - t_b - t_l
    tg_p, tg_f, tg_c = target_pfc_grams(intake, weight_kg)
    cands_b = top_candidates_by_target(combos_b, t_b)
    cands_l = top_candidates_by_target(combos_l, t_l)
    cands_d = top_candidates_by_target(combos_d, t_d)
    best, best_score = None, float("inf")
    for cb in cands_b:
        names_b = names_set(cb)
        for cl in cands_l:
            if names_b & names_set(cl): continue
            price_bl = cb["price"] + cl["price"]
            if price_bl > budget: continue
            kcal_bl = cb["kcal"] + cl["kcal"]
            p_bl = cb["protein"] + cl["protein"]
            f_bl = cb["fat"] + cl["fat"]
            c_bl = cb["carb"] + cl["carb"]
            fiber_bl = cb["fiber"] + cl["fiber"]
            names_bl = names_b | names_set(cl)
            remain = intake - kcal_bl
            for cd in sorted(cands_d, key=lambda x:(abs(x["kcal"]-remain), x["price"]))[:60]:
                if names_bl & names_set(cd): continue
                price_total = price_bl + cd["price"]
                if price_total > budget: continue
                plan = {
                    "breakfast": cb, "lunch": cl, "dinner": cd,
                    "kcal_total": kcal_bl + cd["kcal"],
                    "protein_total": p_bl + cd["protein"],
                    "fat_total":     f_bl + cd["fat"],
                    "carb_total":    c_bl + cd["carb"],
                    "fiber_total":   fiber_bl + cd["fiber"],
                    "price_total": price_total,
                }
                score = plan_score(plan, intake, tg_p, tg_f, tg_c)
                if (score < best_score) or (score == best_score and price_total < (best["price_total"] if best else 1e18)):
                    best, best_score = plan, score
    return best, best_score

# ===============================
# 見やすいカードUI（画像＋テキスト横並び）
# ===============================
IMG_WIDTH = 150  # 画像の標準幅（ここを好みで大きくできます）

def _fmt_price(yen: float) -> str:
    try:
        return f"¥{int(yen):,}"
    except Exception:
        return "-"

def _slot_header(title: str, target_kcal: int, picked_kcal: int):
    cols = st.columns([1, 1])
    with cols[0]:
        st.markdown(f"### {title}")
    with cols[1]:
        st.metric(label=f"{title} 目標", value=f"{picked_kcal} kcal", delta=f"{picked_kcal - target_kcal:+} kcal")

def _render_item_card(it: dict):
    """
    画像を左、テキストを右に配置。
    画像なしの場合はプレースホルダーを表示。
    """
    with st.container(border=True):
        c1, c2 = st.columns([1, 3], vertical_alignment="center")
        with c1:
            if it.get("image_url"):
                st.image(it["image_url"], width=IMG_WIDTH)
            else:
                st.markdown(
                    f"<div style='width:{IMG_WIDTH}px;height:{IMG_WIDTH}px;border:1px solid #eee;"
                    "display:flex;align-items:center;justify-content:center;border-radius:8px;'>画像なし</div>",
                    unsafe_allow_html=True
                )
        with c2:
            name = it.get("name","")
            store = it.get("store","")
            cat = it.get("category","")
            url = it.get("url","")
            kcal = it.get("kcal",0)
            p = it.get("protein_g",0)
            f = it.get("fat_g",0)
            c = it.get("carb_g",0)
            fib = it.get("fiber_g",0)
            price = it.get("price_jpy",0)

            st.markdown(f"**{name}**")
            st.caption(f"{store}｜{cat}")
            st.markdown(
                f"カロリー: **{kcal} kcal** / "
                f"P: **{p:.0f} g**・F: **{f:.0f} g**・C: **{c:.0f} g**・食物繊維: **{fib:.1f} g**"
            )
            cols = st.columns([1, 2])
            with cols[0]:
                st.markdown(_fmt_price(price))
            with cols[1]:
                if url:
                    st.markdown(f"[公式ページを開く]({url})")

def _render_slot_cards(slot_key: str, jp_title: str, best_plan: dict, target_kcal: int):
    combo = best_plan[slot_key]
    picked = int(combo["kcal"])
    _slot_header(jp_title, target_kcal, picked)
    # 複数品のときはカードを縦に並べる
    for it in combo["items"]:
        _render_item_card(it)
    st.markdown(
        f"**小計:** {picked} kcal / {_fmt_price(combo['price'])}・"
        f"P{combo['protein']:.0f}・F{combo['fat']:.0f}・C{combo['carb']:.0f}・食物繊維{combo['fiber']:.1f} g"
    )

# ===============================
# ページ：マイページ
# ===============================
def page_my_page():
    st.title("マイページ")

    user = st.session_state["user"]
    if "profile_initialized" not in st.session_state:
        prof = load_profile(user.id)
        st.session_state["form_age"]          = (prof or {}).get("age", 33)
        st.session_state["form_sex"]          = (prof or {}).get("sex", "male")
        st.session_state["form_height"]       = (prof or {}).get("height_cm", 173)
        st.session_state["form_weight_now"]   = (prof or {}).get("weight_now", 70.0)
        st.session_state["form_weight_goal"]  = (prof or {}).get("weight_goal", 65.0)
        _deadline = (prof or {}).get("deadline", dt.date.today() + dt.timedelta(days=60))
        if isinstance(_deadline, str):
            _deadline = dt.date.fromisoformat(_deadline)
        st.session_state["form_deadline"]     = _deadline

        # ★ 活動量は新フォーマットに正規化
        saved_activity = (prof or {}).get("activity", DEFAULT_ACTIVITY_DISPLAY)
        st.session_state["form_activity"] = normalize_saved_activity(saved_activity)

        st.session_state["form_budget"]       = (prof or {}).get("daily_budget", 1200)
        st.session_state.setdefault("store_b_input", "セブンイレブン")
        st.session_state.setdefault("store_l_input", "ファミリーマート")
        st.session_state.setdefault("store_d_input", "ほっともっと")
        st.session_state["profile_initialized"] = True

    with st.expander("諸々基礎条件入力", expanded=True):
        with st.form("profile_form", clear_on_submit=False):
            c1, c2 = st.columns(2)
            with c1:
                age = st.number_input("年齢", 18, 80, value=int(st.session_state["form_age"]), key="age_input")
                sex = st.radio("性別", ["male","female"], horizontal=True,
                            index=0 if st.session_state["form_sex"]=="male" else 1, key="sex_input")
                height = st.number_input("身長(cm)", 140, 210, value=int(st.session_state["form_height"]), key="height_input")
                weight_now = st.number_input("初期体重(kg)", 35.0, 150.0, value=float(st.session_state["form_weight_now"]), step=0.1, key="weight_now_input")
                weight_goal = st.number_input("目標体重(kg)", 35.0, 150.0, value=float(st.session_state["form_weight_goal"]), step=0.1, key="weight_goal_input")
            with c2:
                deadline = st.date_input("期限日付", value=st.session_state["form_deadline"], key="deadline_input")

                # ★ 活動量セレクト（週◯回の目安つき）
                default_index = ACTIVITY_DISPLAY.index(st.session_state["form_activity"]) \
                                if st.session_state["form_activity"] in ACTIVITY_DISPLAY else ACTIVITY_DISPLAY.index(DEFAULT_ACTIVITY_DISPLAY)
                activity_display = st.selectbox(
                    "活動量（TDEEの係数）",
                    ACTIVITY_DISPLAY,
                    index=default_index,
                    key="activity_input",
                    help="週あたりの運動回数・時間の目安は下の表を参照"
                )
                # 選択中の説明を添える
                _fac = get_activity_factor(activity_display)
                _label = activity_display.split("(")[0].strip()
                st.caption(f"選択中: **{_label}**｜係数 **{_fac}**｜目安: " +
                           next((lvl['weekly'] for lvl in ACTIVITY_LEVELS if abs(lvl['factor']-_fac)<1e-6), ""))

                daily_budget = st.number_input("1日予算(円)", 300, 4000, value=int(st.session_state["form_budget"]), step=10, key="budget_input")

            # ★ 活動量の目安テーブル
            with st.expander("活動量レベルの目安（週あたりの運動イメージ）", expanded=False):
                df_lv = pd.DataFrame([{
                    "レベル": lvl["label"],
                    "係数": lvl["factor"],
                    "週あたりの目安": lvl["weekly"],
                    "具体像": lvl["hint"]
                } for lvl in ACTIVITY_LEVELS])
                st.dataframe(df_lv, use_container_width=True, hide_index=True)

            saved = st.form_submit_button("プロフィール保存")
            if saved:
                try:
                    save_profile(
                        user.id,
                        st.session_state["age_input"],
                        st.session_state["sex_input"],
                        st.session_state["height_input"],
                        st.session_state["weight_now_input"],
                        st.session_state["weight_goal_input"],
                        st.session_state["deadline_input"],
                        st.session_state["activity_input"],  # 表示名を保存
                        st.session_state["budget_input"],
                    )
                    st.session_state["form_age"]        = st.session_state["age_input"]
                    st.session_state["form_sex"]        = st.session_state["sex_input"]
                    st.session_state["form_height"]     = st.session_state["height_input"]
                    st.session_state["form_weight_now"] = st.session_state["weight_now_input"]
                    st.session_state["form_weight_goal"]= st.session_state["weight_goal_input"]
                    st.session_state["form_deadline"]   = st.session_state["deadline_input"]
                    st.session_state["form_activity"]   = st.session_state["activity_input"]
                    st.session_state["form_budget"]     = st.session_state["budget_input"]
                    st.success("プロフィールを保存しました ✅")
                except Exception as e:
                    st.error(f"保存エラー: {e}")

    # ここから計算と体重グラフ
    intake, tdee, deficit_day, days = calc_target_intake(
        st.session_state["form_age"], st.session_state["form_sex"],
        st.session_state["form_height"], st.session_state["form_weight_now"],
        st.session_state["form_weight_goal"], st.session_state["form_deadline"],
        st.session_state["form_activity"]
    )

    # ★ 20/40/40 の内訳も一緒に表示
    b20 = int(intake*0.20); l40 = int(intake*0.40); d40 = intake - b20 - l40
    st.info(
        f"あなたの1日目標摂取カロリー： **{intake} kcal**"
        f"（消費: {tdee} kcal / 赤字目安: {deficit_day} kcal/日 × {days}日）\n\n"
        f"**内訳目安：朝 {b20} kcal（20%）｜昼 {l40} kcal（40%）｜夜 {d40} kcal（40%）**"
    )

    # ===== 体重の推移（“日ごと”に表示） =====
    df_w = load_weight_history(user.id)
    if not df_w.empty:
        df_w["date"] = pd.to_datetime(df_w["date"]).dt.normalize()
        df_daily = df_w.groupby("date", as_index=False)["weight_kg"].mean()

        line = alt.Chart(df_daily).mark_line(point=True).encode(
            x=alt.X("date:T", timeUnit="yearmonthdate",
                    axis=alt.Axis(title="日付", format="%m/%d")),
            y=alt.Y("weight_kg:Q", title="体重(kg)")
        )
        target_line = alt.Chart(
            pd.DataFrame({"y": [st.session_state["form_weight_goal"]]})
        ).mark_rule(color="red", strokeDash=[5,5]).encode(y="y:Q")

        chart = line + target_line
        st.altair_chart(chart, use_container_width=True)

        latest_weight = df_daily.sort_values("date").iloc[-1]["weight_kg"]
        goal_weight = st.session_state["form_weight_goal"]
        diff = latest_weight - goal_weight
        if diff > 0:
            st.success(f"目標まであと **{diff:.1f} kg** 減量！")
        elif diff < 0:
            st.info(f"目標を **{-diff:.1f} kg** 下回りました 🎉")
        else:
            st.balloons()
            st.success("目標体重を達成しました！🎉")
    else:
        st.info("まだ体重ログがありません。『今日の食事提案』で記録されます。")

# ===============================
# ページ：今日の食事提案
# ===============================
def page_today_plan():
    st.title("今日の食事提案")

    age = st.session_state.get("form_age", 33)
    sex = st.session_state.get("form_sex", "male")
    height = st.session_state.get("form_height", 173)
    weight_goal = st.session_state.get("form_weight_goal", 65.0)
    deadline = st.session_state.get("form_deadline", dt.date.today() + dt.timedelta(days=60))
    activity = st.session_state.get("form_activity", DEFAULT_ACTIVITY_DISPLAY)
    default_budget = st.session_state.get("form_budget", 1200)

    STORE_OPTIONS = ["セブンイレブン", "ファミリーマート", "ほっともっと"]

    with st.form("today_form", clear_on_submit=False):
        c = st.columns(2)
        with c[0]:
            weight_today = st.number_input("今日の体重(kg)", 35.0, 150.0,
                                           value=float(st.session_state.get("weight_today", st.session_state.get("form_weight_now", 70.0))),
                                           step=0.1, key="weight_today_input")
        with c[1]:
            budget_today = st.number_input("今日の予算(円)", 300, 4000,
                                           value=int(st.session_state.get("budget_today", default_budget)),
                                           step=10, key="budget_today_input")

        col_store_b, col_store_l, col_store_d = st.columns(3)
        with col_store_b:
            st.selectbox("朝の店舗", STORE_OPTIONS, key="store_b_input",
                         index=STORE_OPTIONS.index(st.session_state.get("store_b_input","セブンイレブン")))
        with col_store_l:
            st.selectbox("昼の店舗", STORE_OPTIONS, key="store_l_input",
                         index=STORE_OPTIONS.index(st.session_state.get("store_l_input","ファミリーマート")))
        with col_store_d:
            st.selectbox("夜の店舗", STORE_OPTIONS, key="store_d_input",
                         index=STORE_OPTIONS.index(st.session_state.get("store_d_input","ほっともっと")))

        make_clicked = st.form_submit_button("今日の3食プランを作る")

    intake, tdee, deficit_day, days = calc_target_intake(
        age, sex, height, weight_today, weight_goal, deadline, activity
    )

    # ★ 20/40/40 の内訳も表示
    b20 = int(intake*0.20); l40 = int(intake*0.40); d40 = intake - b20 - l40
    st.info(f"あなたの1日目標摂取カロリー： **{intake} kcal**｜内訳：朝 {b20} / 昼 {l40} / 夜 {d40}")

    if make_clicked:
        st.session_state["weight_today"] = weight_today
        st.session_state["budget_today"] = budget_today
        try:
            log_weight(st.session_state["user"].id, dt.date.today(), weight_today)
        except Exception as e:
            st.warning(f"体重ログの保存に失敗しました: {e}")

        store_b = st.session_state.get("store_b_input", "セブンイレブン")
        store_l = st.session_state.get("store_l_input", "ファミリーマート")
        store_d = st.session_state.get("store_d_input", "ほっともっと")

        # 候補取得：スロット専用 + any
        df_b = load_products_for(store_b, ["breakfast", "any"])
        df_l = load_products_for(store_l, ["lunch", "any"])
        df_d = load_products_for(store_d, ["dinner", "any"])

        missing = []
        if df_b.empty: missing.append("朝")
        if df_l.empty: missing.append("昼")
        if df_d.empty: missing.append("夜")
        if missing:
            st.error(f"{'・'.join(missing)} の候補商品がありません。")
            st.stop()

        # ★ 20/40/40 ターゲット
        t_b = b20; t_l = l40; t_d = d40

        # ==== ここを変更：スロット一致を優先してからカロリー近さ ====
        def _trim(df, target_kcal, slot_key, n=40):
            df2 = df.assign(
                _absdiff=(df["kcal"] - target_kcal).abs(),
                _prio=(df["meal_slot_hint"] == slot_key).astype(int)  # 一致=1, any=0
            )
            return df2.sort_values(by=["_prio", "_absdiff"], ascending=[False, True]) \
                      .head(n).drop(columns=["_absdiff", "_prio"])

        df_b = _trim(df_b, t_b, "breakfast", n=40)
        df_l = _trim(df_l, t_l, "lunch",     n=40)
        df_d = _trim(df_d, t_d, "dinner",    n=40)

        combos_b = generate_item_combos(df_b, budget=budget_today, max_items=3)
        combos_l = generate_item_combos(df_l, budget=budget_today, max_items=3)
        combos_d = generate_item_combos(df_d, budget=budget_today, max_items=3)

        if not (combos_b and combos_l and combos_d):
            st.warning("候補が不足しています。")
            st.stop()

        best, score = optimize_day_fixed_score_no_overlap(
            combos_b, combos_l, combos_d, intake, budget_today, weight_kg=weight_today
        )

        if best:
            # ====== 視覚的なカードUIで表示（画像横並び） ======
            st.subheader("提案結果（同一商品の重複なし）")

            _render_slot_cards("breakfast", "朝ごはん", best, t_b)
            _render_slot_cards("lunch", "昼ごはん", best, t_l)
            _render_slot_cards("dinner", "夜ごはん", best, t_d)

            # ====== 日合計のサマリー ======
            st.markdown("---")
            st.markdown(
                f"### 日合計\n"
                f"**{best['kcal_total']} kcal / {_fmt_price(best['price_total'])}**  \n"
                f"**P:** {best['protein_total']:.0f} g / "
                f"**F:** {best['fat_total']:.0f} g / "
                f"**C:** {best['carb_total']:.0f} g / "
                f"**Fiber:** {best['fiber_total']:.1f} g"
            )
            delta = best["kcal_total"] - intake
            st.metric("目標カロリー差", f"{delta:+} kcal")

            # ====== コピペ用の表（画像列つき）も残す ======
            def explode_slot(slot, jp):
                rows = []
                for it in best[slot]["items"]:
                    rows.append([
                        jp, it["store"], it["category"], it["name"], it["kcal"],
                        it["protein_g"], it["fat_g"], it["carb_g"], it["fiber_g"],
                        it["price_jpy"], it.get("url",""), it.get("image_url",""),
                    ])
                return rows

            rows = []
            rows += explode_slot("breakfast","朝")
            rows += explode_slot("lunch","昼")
            rows += explode_slot("dinner","夜")

            res = pd.DataFrame(
                rows,
                columns=["食事区分","店舗","カテゴリ","商品名",
                         "カロリー(kcal)","タンパク質(g)","脂質(g)","炭水化物(g)","食物繊維(g)",
                         "価格(円)","商品URL","image_url"]
            )

            with st.expander("コピペ・共有用テーブル（画像列あり）", expanded=False):
                st.dataframe(
                    res,
                    use_container_width=True,
                    column_config={
                        "商品URL": st.column_config.LinkColumn("商品ページ", display_text="公式ページを開く"),
                        "image_url": st.column_config.ImageColumn("商品画像")
                    }
                )
        else:
            st.error("条件に合うプランが見つかりませんでした。")

# ===============================
# サイドバー（共通）
# ===============================
st.sidebar.image("logo.png", use_container_width=True)
st.sidebar.markdown("### Dietary")
st.sidebar.write(f"ログイン情報\n{st.session_state['user'].email}")
st.sidebar.button("ログアウト", on_click=logout)

nav = st.sidebar.radio("ナビゲーション", ["マイページ", "今日の食事提案"])

# ===============================
# ルーティング
# ===============================
if nav == "マイページ":
    page_my_page()
else:
    page_today_plan()
