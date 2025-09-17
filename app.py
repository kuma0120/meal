import streamlit as st
import pandas as pd
from supabase import create_client
import datetime as dt
# ← 他に必要なimportがあればここに追加

# --- Supabaseクライアントの初期化 ---
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_ANON_KEY"]
supabase = create_client(url, key)

# --- ログインUIの定義 ---
def login_ui():
    st.title("ログイン")
    email = st.text_input("メールアドレス")
    pw = st.text_input("パスワード", type="password")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("ログイン"):
            try:
                auth = supabase.auth.sign_in_with_password({"email": email, "password": pw})
                st.session_state["user"] = auth.user
                st.rerun()
            except Exception as e:
                st.error(f"ログイン失敗: {e}")
    with col2:
        if st.button("新規登録"):
            try:
                supabase.auth.sign_up({"email": email, "password": pw})
                st.success("登録しました！")
            except Exception as e:
                st.error(f"登録失敗: {e}")

def logout():
    supabase.auth.sign_out()
    st.session_state.pop("user", None)
    st.rerun()

# --- 認証チェック ---
if "user" not in st.session_state:
    login_ui()
    st.stop()  # ログインしてなければアプリ本体は止める
else:
    st.sidebar.write(f"ログイン中: {st.session_state['user'].email}")
    st.sidebar.button("ログアウト", on_click=logout)

# ===============================
# ここから下が「既存のアプリ本体」
# ===============================
st.success("ログイン済み！本編アプリを使えます ✨")

# 例: ここに食事プラン生成の処理を続ける


# app.py — 朝/昼/夜の比率を自動最適化（PFCなし / Supabaseなし）
import streamlit as st
import pandas as pd
import datetime as dt
from itertools import combinations
from math import floor

st.set_page_config(page_title="食事改善アプリ（配分自動最適化・±100kcal寄せ）", layout="centered")

# -----------------------------
# 商品データ（微調整品多め）
# store, category, name, kcal, price_jpy, meal_slot_hint
# -----------------------------
PRODUCTS = [
    # Seven
    ("seven","foods","おにぎり 紅しゃけ",180,140,"breakfast"),
    ("seven","foods","おにぎり ツナマヨ",230,150,"breakfast"),
    ("seven","foods","おにぎり 昆布",180,120,"breakfast"),
    ("seven","foods","サラダチキン プレーン",114,248,"any"),
    ("seven","foods","サラダチキン ハーブ",125,258,"any"),
    ("seven","foods","野菜たっぷりチキンサラダ",210,420,"lunch"),
    ("seven","foods","低糖質パン",150,160,"breakfast"),
    ("seven","foods","サンドイッチ（ハムたまご）",320,330,"breakfast"),
    ("seven","foods","鯖の塩焼き",280,360,"dinner"),
    ("seven","foods","グリルチキン",220,320,"dinner"),
    # 微調整品
    ("seven","foods","味噌汁",35,120,"any"),
    ("seven","foods","わかめスープ",20,110,"any"),
    ("seven","foods","ゆでたまご",68,84,"any"),
    ("seven","foods","枝豆（小）",120,200,"any"),
    ("seven","foods","ミニサラダ（小）",60,150,"any"),
    ("seven","foods","カットフルーツ",90,300,"any"),

    # FamilyMart
    ("familymart","foods","鮭おにぎり",185,150,"breakfast"),
    ("familymart","foods","明太子おにぎり",180,140,"breakfast"),
    ("familymart","foods","グリルチキン(ハーブ)",165,220,"any"),
    ("familymart","foods","ライザップチキンサラダ",210,398,"lunch"),
    ("familymart","foods","スパゲティ ナポリタン(小)",420,430,"lunch"),
    ("familymart","foods","さば塩焼き",280,350,"dinner"),
    # 微調整品
    ("familymart","foods","味噌汁",40,120,"any"),
    ("familymart","foods","豚汁",150,260,"any"),
    ("familymart","foods","ゆでたまご",70,90,"any"),
    ("familymart","foods","枝豆",120,200,"any"),
    ("familymart","foods","ミニサラダ（小）",60,150,"any"),

    # HottoMotto
    ("hottomotto","bento","のり弁",700,420,"lunch"),
    ("hottomotto","bento","から揚弁当(ライス小)",650,480,"lunch"),
    ("hottomotto","bento","銀鮭弁当(ライス小)",540,560,"lunch"),
    # 微調整品
    ("hottomotto","bento","みそ汁",40,110,"any"),
    ("hottomotto","bento","サラダ(小)",90,150,"any"),
    ("hottomotto","bento","白身フライ単品",250,180,"any"),
    ("hottomotto","bento","から揚単品(2個)",220,170,"any"),
]

@st.cache_data
def load_products_df():
    return pd.DataFrame(PRODUCTS, columns=["store","category","name","kcal","price_jpy","meal_slot_hint"])

# -----------------------------
# 目標摂取カロリー（期限 & 目標体重から逆算）
# -----------------------------
def tdee_kcal(age, sex, height_cm, weight_kg, activity="med"):
    s = 5 if sex == "male" else -161
    bmr = 10*weight_kg + 6.25*height_cm - 5*age + s
    factor = {"low":1.2,"med":1.375,"high":1.55}[activity]
    return floor(bmr * factor)

def calc_target_intake(age, sex, height, weight_now, weight_goal, deadline, activity):
    tdee = tdee_kcal(age, sex, height, weight_now, activity)
    days = max(1, (deadline - dt.date.today()).days)
    delta_w = max(0, weight_now - weight_goal)
    deficit_total = delta_w * 7700.0
    deficit_per_day = deficit_total / days
    intake = max(1200, int(tdee - deficit_per_day))
    return intake, tdee, int(deficit_per_day), days

# -----------------------------
# 組合せ（1〜3品）を事前生成 → 後でターゲットに合わせて並べる
# -----------------------------
def generate_item_combos(df_slot, budget, max_items=3):
    items = df_slot.to_dict("records")
    combos = []
    for r in range(1, min(max_items, len(items)) + 1):
        for comb in combinations(items, r):
            kcal = sum(x["kcal"] for x in comb)
            price = sum(x["price_jpy"] for x in comb)
            if price <= budget:
                combos.append({"kcal":kcal, "price":price, "items":comb})
    return combos

def top_candidates_by_target(combos, target_kcal, keep_top=120):
    scored = [{"kcal":c["kcal"], "price":c["price"], "items":c["items"],
               "absdiff":abs(c["kcal"]-target_kcal)} for c in combos]
    scored.sort(key=lambda x: (x["absdiff"], x["price"]))
    # 簡易パレート：同absdiff帯で最安だけ残す
    pareto, seen = [], {}
    for s in scored:
        d = s["absdiff"]
        if d not in seen or s["price"] < seen[d]:
            pareto.append(s); seen[d] = s["price"]
    pareto.sort(key=lambda x: (x["absdiff"], x["price"]))
    return pareto[:keep_top]

# -----------------------------
# 3食トータル最適化（配分の自動探索）
# -----------------------------
def optimize_day_with_split(combos_b, combos_l, combos_d, intake, budget,
                            min_b=10, max_b=50, min_l=20, max_l=60, step=5):
    """
    朝b%・昼l%・夜(100-b-l)% を step%刻みで総当たり。
    各配分で候補を上位抽出し、日合計誤差（abs(total - intake)）最小を採用。
    """
    best = None
    best_diff = 10**9
    best_split = None

    for b in range(min_b, max_b+1, step):
        for l in range(min_l, max_l+1, step):
            d = 100 - b - l
            if d < 10 or d > 60:
                continue
            t_b = int(intake*b/100); t_l = int(intake*l/100); t_d = intake - t_b - t_l

            cands_b = top_candidates_by_target(combos_b, t_b)
            cands_l = top_candidates_by_target(combos_l, t_l)
            cands_d = top_candidates_by_target(combos_d, t_d)

            # 3食最適化（夕食は残りを狙って限定検索）
            local_best, local_diff = None, 10**9
            for cb in cands_b:
                for cl in cands_l:
                    price_bl = cb["price"] + cl["price"]
                    if price_bl > budget: continue
                    kcal_bl = cb["kcal"] + cl["kcal"]
                    remain = intake - kcal_bl
                    for cd in sorted(cands_d, key=lambda x:(abs(x["kcal"]-remain), x["price"]))[:150]:
                        price_total = price_bl + cd["price"]
                        if price_total > budget: continue
                        kcal_total = kcal_bl + cd["kcal"]
                        diff = abs(kcal_total - intake)
                        if diff < local_diff or (diff == local_diff and price_total < local_best["price_total"]):
                            local_best = {
                                "breakfast": cb, "lunch": cl, "dinner": cd,
                                "kcal_total": kcal_total, "price_total": price_total
                            }
                            local_diff = diff

            if local_best and (local_diff < best_diff or
                               (local_diff == best_diff and local_best["price_total"] < best["price_total"])):
                best, best_diff, best_split = local_best, local_diff, (b, l, d)

    return best, best_diff, best_split

# 夕食の微調整
def fine_tune_dinner(best_plan, tuner_df, intake, budget, max_add=2):
    if best_plan is None: return None
    remain_budget = budget - best_plan["price_total"]
    if remain_budget <= 0: return best_plan

    base = best_plan
    best, best_diff = base, abs(base["kcal_total"] - intake)
    tuners = tuner_df.to_dict("records")

    # 1品追加
    for r in tuners:
        if r["price_jpy"] <= remain_budget:
            kcal_total = base["kcal_total"] + r["kcal"]
            price_total = base["price_total"] + r["price_jpy"]
            diff = abs(kcal_total - intake)
            if diff < best_diff:
                best, best_diff = {**base, "kcal_total":kcal_total, "price_total":price_total}, diff
                best["dinner"] = {"items": tuple(list(base["dinner"]["items"]) + [r]),
                                  "kcal": base["dinner"]["kcal"] + r["kcal"],
                                  "price": base["dinner"]["price"] + r["price_jpy"]}

    # 2品追加
    if max_add >= 2:
        from itertools import combinations as comb2
        for c in comb2(tuners, 2):
            price = c[0]["price_jpy"] + c[1]["price_jpy"]
            if price > remain_budget: 
                continue
            kcal_add = c[0]["kcal"] + c[1]["kcal"]
            kcal_total = base["kcal_total"] + kcal_add
            price_total = base["price_total"] + price
            diff = abs(kcal_total - intake)
            if diff < best_diff:
                best, best_diff = {**base, "kcal_total":kcal_total, "price_total":price_total}, diff
                best["dinner"] = {"items": tuple(list(base["dinner"]["items"]) + list(c)),
                                  "kcal": base["dinner"]["kcal"] + kcal_add,
                                  "price": base["dinner"]["price"] + price}
    return best

# -----------------------------
# UI
# -----------------------------
st.title("食事改善アプリ（配分自動最適化・±100kcal寄せ）")

with st.expander("条件入力", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        age = st.number_input("年齢", 18, 80, 33)
        sex = st.radio("性別", ["male","female"], horizontal=True)
        height = st.number_input("身長(cm)", 140, 210, 173)
        weight_now = st.number_input("現在体重(kg)", 35.0, 150.0, 78.0, step=0.1)
        weight_goal = st.number_input("目標体重(kg)", 35.0, 150.0, 70.0, step=0.1)
    with c2:
        deadline = st.date_input("期限日付", dt.date.today() + dt.timedelta(days=60))
        activity = st.selectbox("活動量", ["low","med","high"], index=1)
        daily_budget = st.number_input("1日予算(円)", 300, 3000, 1000, step=10)
        store = st.selectbox("カテゴリ/店舗", ["seven","familymart","hottomotto"])

# 計算
intake, tdee, deficit_day, days = calc_target_intake(age, sex, height, weight_now, weight_goal, deadline, activity)
st.info(
    f"基礎TDEE: {tdee} kcal /日\n"
    f"必要赤字: {deficit_day} kcal /日 × {days}日\n"
    f"目標摂取カロリー: **{intake} kcal /日**"
)

if st.button("きょうの3食プランを作る", type="primary"):
    df = load_products_df()
    df = df[df["store"] == store].reset_index(drop=True)
    if df.empty:
        st.error("店舗データがありません。"); st.stop()

    # 枠ごとの候補集合（ターゲット非依存）
    df_b = df[df["meal_slot_hint"].isin(["breakfast","any"])]
    df_l = df[df["meal_slot_hint"].isin(["lunch","any"])]
    df_d = df[df["meal_slot_hint"].isin(["dinner","any"])]

    combos_b = generate_item_combos(df_b, budget=daily_budget)
    combos_l = generate_item_combos(df_l, budget=daily_budget)
    combos_d = generate_item_combos(df_d, budget=daily_budget)

    if not (combos_b and combos_l and combos_d):
        st.warning("候補が不足。商品を増やすか予算を調整してください。"); st.stop()

    # 配分自動最適化
    best, diff, split = optimize_day_with_split(
        combos_b, combos_l, combos_d, intake, daily_budget,
        min_b=10, max_b=50, min_l=20, max_l=60, step=5
    )

    # 夕食 微調整
    tuner_df = df[df["name"].isin([
        "味噌汁","わかめスープ","ゆでたまご","枝豆（小）","ミニサラダ（小）",
        "豚汁","サラダ(小)","みそ汁","カットフルーツ"
    ])]
    best = fine_tune_dinner(best, tuner_df, intake, daily_budget)
    final_diff = abs(best["kcal_total"] - intake) if best else None

    if best:
        # 表示
        def explode_slot(slot, jp):
            rows = []
            for it in best[slot]["items"]:
                rows.append([jp, it["name"], it["kcal"], it["price_jpy"]])
            return rows

        rows = []
        rows += explode_slot("breakfast","朝")
        rows += explode_slot("lunch","昼")
        rows += explode_slot("dinner","夜")
        res = pd.DataFrame(rows, columns=["meal_slot","name","kcal","price_jpy"])
        st.subheader("提案結果")
        st.dataframe(res, use_container_width=True)

        st.markdown(f"### 合計\n**{best['kcal_total']} kcal / ¥{best['price_total']}**")
        delta = best["kcal_total"] - intake
        st.metric("目標カロリー差", f"{delta:+} kcal")
        if split:
            st.caption(f"採用された配分：朝 {split[0]}% / 昼 {split[1]}% / 夜 {split[2]}%")

        if abs(delta) > 100:
            st.warning("±100kcalに収まらない場合は、候補を増やす/予算を上げると精度が上がります。")
    else:
        st.error("条件に合うプランが見つかりませんでした。")

