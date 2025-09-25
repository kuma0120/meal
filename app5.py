# app.py
import streamlit as st
import pandas as pd
import datetime as dt
from itertools import combinations
from math import floor
from supabase import create_client

st.set_page_config(
    page_title="食事改善アプリ（3:4:3固定・カロリー主軸＋栄養考慮・重複禁止）",
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
        "activity": activity,
        "daily_budget": int(daily_budget) if daily_budget is not None else None,
    }
    supabase.table("profiles").upsert(row, on_conflict="id").execute()

# ===============================
# 商品取得：店舗＋スロットでDB側を絞って取得（高速化）
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
        .select("store,category,name,kcal,price_jpy,meal_slot_hint,protein_g,fat_g,carb_g,fiber_g,image_url")
        .eq("store", store_name)
        .in_("meal_slot_hint", slots)
        .execute()
    )
    df = pd.DataFrame(res.data or [])
    return _coerce_and_fill(df)

# ===============================
# TDEE 計算まわり
# ===============================
ACTIVITY_FACTOR = {
    "ほぼ運動しない(1.2)": 1.2,
    "軽い運動(1.375)": 1.375,
    "中程度の運動(1.55)": 1.55,
    "激しい運動(1.725)": 1.725,
    "非常に激しい(1.9)": 1.9,
}

def bmr_harris_benedict_revised(age, sex, height_cm, weight_kg):
    if sex == "male":
        return 88.362 + 13.397*weight_kg + 4.799*height_cm - 5.677*age
    else:
        return 447.593 + 9.247*weight_kg + 3.098*height_cm - 4.330*age

def tdee_kcal(age, sex, height_cm, weight_kg, activity_label):
    bmr = bmr_harris_benedict_revised(age, sex, height_cm, weight_kg)
    factor = ACTIVITY_FACTOR[activity_label]
    return floor(bmr * factor)

def calc_target_intake(age, sex, height, weight_now, weight_goal, deadline, activity_label):
    tdee = tdee_kcal(age, sex, height, weight_now, activity_label)
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
# 組合せ生成＆最適化（計算量削減版）
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

def top_candidates_by_target(combos, target_kcal, keep_top=60):  # ← 140→60に縮小
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
    t_b = int(intake*0.30); t_l = int(intake*0.40); t_d = intake - t_b - t_l
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
            # 夕食候補の走査幅を 60 に制限
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
        st.session_state["form_activity"]     = (prof or {}).get("activity", "軽い運動(1.375)")
        st.session_state["form_budget"]       = (prof or {}).get("daily_budget", 1200)
        # DBの日本語に合わせたデフォルト
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
                activity = st.selectbox(
                    "活動量（TDEEの係数）",
                    list(ACTIVITY_FACTOR.keys()),
                    index=list(ACTIVITY_FACTOR.keys()).index(st.session_state["form_activity"]),
                    key="activity_input",
                )
                daily_budget = st.number_input("1日予算(円)", 300, 4000, value=int(st.session_state["form_budget"]), step=10, key="budget_input")

            saved = st.form_submit_button("プロフィール保存")
            if saved:
                try:
                    save_profile(
                        st.session_state["user"].id,
                        st.session_state["age_input"],
                        st.session_state["sex_input"],
                        st.session_state["height_input"],
                        st.session_state["weight_now_input"],
                        st.session_state["weight_goal_input"],
                        st.session_state["deadline_input"],
                        st.session_state["activity_input"],
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

    intake, tdee, deficit_day, days = calc_target_intake(
        st.session_state["form_age"], st.session_state["form_sex"],
        st.session_state["form_height"], st.session_state["form_weight_now"],
        st.session_state["form_weight_goal"], st.session_state["form_deadline"],
        st.session_state["form_activity"]
    )
    st.info(f"あなたの1日目標摂取カロリー： **{intake} kcal**（1日の消費カロリー: {tdee}kcal / 赤字目安: {deficit_day}kcal/日 × {days}日）")

# ===============================
# ページ：今日の食事提案
# ===============================
def page_today_plan():
    st.title("今日の食事提案")

    # プロフィール（保存済み）を取得
    age = st.session_state.get("form_age", 33)
    sex = st.session_state.get("form_sex", "male")
    height = st.session_state.get("form_height", 173)
    weight_goal = st.session_state.get("form_weight_goal", 65.0)
    deadline = st.session_state.get("form_deadline", dt.date.today() + dt.timedelta(days=60))
    activity = st.session_state.get("form_activity", "軽い運動(1.375)")
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

    # 目標摂取カロリー（今日の体重で再計算）
    intake, tdee, deficit_day, days = calc_target_intake(
        age, sex, height, weight_today, weight_goal, deadline, activity
    )
    st.info(f"あなたの1日目標摂取カロリー： **{intake} kcal**")

    if make_clicked:
        # セッション保持（次回の初期値に使う）
        st.session_state["weight_today"] = weight_today
        st.session_state["budget_today"] = budget_today

        store_b = st.session_state.get("store_b_input", "セブンイレブン")
        store_l = st.session_state.get("store_l_input", "ファミリーマート")
        store_d = st.session_state.get("store_d_input", "ほっともっと")

        # DB側で条件絞り込みして取得（高速）
        df_b = load_products_for(store_b, ["breakfast", "any"])
        df_l = load_products_for(store_l, ["lunch", "any"])
        df_d = load_products_for(store_d, ["dinner", "any"])

        missing = []
        if df_b.empty: missing.append("朝")
        if df_l.empty: missing.append("昼")
        if df_d.empty: missing.append("夜")
        if missing:
            st.error(f"{'・'.join(missing)} の候補商品がありません。店舗や予算を見直すか、商品を増やしてください。")
            st.stop()

        # さらに目標kcalに近い順で40件に事前絞り込み（計算量ダウン）
        t_b = int(intake*0.30); t_l = int(intake*0.40); t_d = intake - t_b - t_l
        def _trim(df, target_kcal, n=40):
            return df.assign(_absdiff=(df["kcal"]-target_kcal).abs()) \
                     .sort_values("_absdiff").head(n).drop(columns="_absdiff")
        df_b = _trim(df_b, t_b, n=40)
        df_l = _trim(df_l, t_l, n=40)
        df_d = _trim(df_d, t_d, n=40)

        # 組合せ生成（必要なら max_items=2 にすると更に速い）
        combos_b = generate_item_combos(df_b, budget=budget_today, max_items=3)
        combos_l = generate_item_combos(df_l, budget=budget_today, max_items=3)
        combos_d = generate_item_combos(df_d, budget=budget_today, max_items=3)

        if not (combos_b and combos_l and combos_d):
            st.warning("候補が不足しています。商品を増やすか予算を調整してください。")
            st.stop()

        best, score = optimize_day_fixed_score_no_overlap(
            combos_b, combos_l, combos_d, intake, budget_today, weight_kg=weight_today
        )

        if best:
            def explode_slot(slot, jp):
                rows = []
                for it in best[slot]["items"]:
                    rows.append([
                        jp,               # meal_slot（日本語）
                        it["store"],      # 店舗
                        it["category"],   # カテゴリ
                        it["name"],
                        it["kcal"],
                        it["protein_g"],
                        it["fat_g"],
                        it["carb_g"],
                        it["fiber_g"],
                        it["price_jpy"],
                    ])
                return rows

            rows = []
            rows += explode_slot("breakfast","朝")
            rows += explode_slot("lunch","昼")
            rows += explode_slot("dinner","夜")

            res = pd.DataFrame(
                rows,
                columns=["meal_slot","store","category","name","kcal","P(g)","F(g)","C(g)","Fiber(g)","price_jpy"]
            )

            st.subheader("提案結果（同一商品の重複なし）")
            st.dataframe(res, use_container_width=True)

            st.markdown(
                f"### 日合計\n"
                f"**{best['kcal_total']} kcal / ¥{best['price_total']}**  \n"
                f"**P:** {best['protein_total']:.0f} g / "
                f"**F:** {best['fat_total']:.0f} g / "
                f"**C:** {best['carb_total']:.0f} g / "
                f"**Fiber:** {best['fiber_total']:.1f} g"
            )
            delta = best["kcal_total"] - intake
            st.metric("目標カロリー差", f"{delta:+} kcal")
            st.caption("配分（固定）：朝 30% / 昼 40% / 夜 30%（商品名の重複禁止）")
            if abs(delta) > 100:
                st.warning("±100kcalに収まらない場合、低/高カロリーの選択肢をさらに追加すると精度UP。")
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
