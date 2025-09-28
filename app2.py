# app.py
import streamlit as st
import pandas as pd
import datetime as dt
from itertools import combinations
from math import floor
from supabase import create_client

st.set_page_config(page_title="食事改善アプリ（3:4:3固定・カロリー主軸＋栄養考慮・重複禁止）", layout="centered")

# ===============================
# Supabase 認証（事前に st.secrets に URL/KEY を設定）
# ===============================
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_ANON_KEY"]
supabase = create_client(url, key)

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
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    st.session_state.pop("user", None)
    st.rerun()

# --- 認証チェック ---
if "user" not in st.session_state:
    login_ui()
    st.stop()
else:
    st.sidebar.write(f"ログイン中: {st.session_state['user'].email}")
    st.sidebar.button("ログアウト", on_click=logout)
    st.success("ログイン済み！本編アプリを使えます ✨")

# ===============================
# ここからアプリ本体
# ===============================

# -----------------------------
# 商品データ（P/F/C/Fiber 付き）※値は概算
# store, category, name, kcal, price_jpy, meal_slot_hint, protein_g, fat_g, carb_g, fiber_g
# -----------------------------
PRODUCTS = [
    # --- Seven（既存） ---
    ("seven","foods","おにぎり 紅しゃけ",180,140,"breakfast", 5, 2, 36, 1),
    ("seven","foods","おにぎり ツナマヨ",230,150,"breakfast", 6, 8, 34, 1),
    ("seven","foods","おにぎり 昆布",180,120,"breakfast", 3, 2, 38, 1),
    ("seven","foods","サラダチキン プレーン",114,248,"any", 23, 1, 0, 0),
    ("seven","foods","サラダチキン ハーブ",125,258,"any", 24, 2, 1, 0),
    ("seven","foods","野菜たっぷりチキンサラダ",210,420,"lunch", 12, 9, 12, 5),
    ("seven","foods","低糖質パン",150,160,"breakfast", 7, 6, 18, 6),
    ("seven","foods","サンドイッチ（ハムたまご）",320,330,"breakfast", 15, 16, 28, 3),
    ("seven","foods","鯖の塩焼き",280,360,"dinner", 22, 20, 0, 0),
    ("seven","foods","グリルチキン",220,320,"dinner", 25, 10, 3, 0),
    ("seven","foods","豚汁",150,280,"any", 8, 8, 12, 2),
    ("seven","foods","枝豆",120,210,"any", 10, 5, 8, 4),
    ("seven","foods","ミニグリーンサラダ",70,180,"any", 2, 2, 10, 3),
    ("seven","foods","シーザーサラダ",180,350,"any", 7, 12, 9, 3),
    ("seven","foods","冷やし中華(小)",420,460,"lunch", 15, 12, 60, 4),
    ("seven","foods","焼き鮭弁当",550,580,"lunch", 26, 16, 65, 4),
    ("seven","foods","カットフルーツ",90,300,"any", 1, 0, 22, 2),
    ("seven","foods","ヨーグルト(無糖)",60,140,"breakfast", 5, 3, 4, 0),
    ("seven","foods","味噌汁",40,120,"any", 2, 1, 4, 0.5),
    ("seven","foods","豆腐サラダ",150,280,"any", 12, 9, 6, 3),

    # --- Seven（追加+10） ---
    ("seven","foods","バナナ",90,120,"breakfast", 1, 0, 23, 2),
    ("seven","foods","ギリシャヨーグルト",100,180,"breakfast", 9, 4, 6, 0),
    ("seven","foods","オートミールカップ",230,220,"breakfast", 8, 4, 38, 6),
    ("seven","foods","ツナサラダ巻",210,260,"lunch", 9, 6, 32, 3),
    ("seven","foods","玄米おにぎり",200,150,"any", 4, 2, 42, 3),
    ("seven","foods","さつまいも(中)",180,160,"any", 2, 0, 42, 3),
    ("seven","foods","ひじき煮(小鉢)",90,180,"any", 4, 3, 12, 5),
    ("seven","foods","小松菜おひたし",40,150,"any", 3, 1, 4, 2),
    ("seven","foods","チキンとブロッコリー",230,430,"dinner", 28, 8, 8, 5),
    ("seven","foods","雑穀ロールパン",190,150,"breakfast", 6, 4, 34, 4),

    # --- Seven（追加+20） ---
    ("seven","foods","サーモン寿司(小)",300,420,"lunch", 16, 8, 42, 2),
    ("seven","foods","たまごサンド",280,320,"breakfast", 12, 14, 26, 2),
    ("seven","foods","チキンと雑穀サラダ",260,480,"lunch", 20, 10, 22, 6),
    ("seven","foods","ほうれん草胡麻和え",80,150,"any", 3, 4, 8, 3),
    ("seven","foods","ツナとコーンのサラダ",160,260,"any", 8, 8, 14, 3),
    ("seven","foods","玄米おにぎり(鮭)",210,170,"any", 6, 3, 40, 3),
    ("seven","foods","炙りチキン弁当(小)",520,560,"dinner", 30, 16, 60, 4),
    ("seven","foods","鶏そぼろ丼(小)",480,520,"lunch", 22, 14, 64, 3),
    ("seven","foods","焼きおにぎり×2",300,180,"breakfast", 6, 2, 64, 2),
    ("seven","foods","冷やし蕎麦(小)",360,420,"lunch", 14, 6, 60, 5),
    ("seven","foods","豆乳(200ml)",110,130,"breakfast", 7, 6, 6, 1),
    ("seven","foods","プロテインバー",200,180,"any", 15, 8, 16, 4),
    ("seven","foods","鶏むね唐揚げ(控えめ)",260,330,"any", 22, 12, 14, 1),
    ("seven","foods","野菜スープ",70,160,"any", 3, 2, 10, 2),
    ("seven","foods","鮭ときのこのご飯(小)",420,500,"dinner", 18, 10, 64, 4),
    ("seven","foods","照り焼きチキンサンド",360,380,"lunch", 22, 12, 40, 3),
    ("seven","foods","ブロッコリー(カップ)",60,140,"any", 4, 1, 6, 3),
    ("seven","foods","ツナとひよこ豆サラダ",220,420,"any", 16, 10, 18, 7),
    ("seven","foods","たまご粥(小)",180,260,"breakfast", 8, 4, 30, 1),
    ("seven","foods","雑穀おにぎり(梅)",190,150,"any", 4, 2, 40, 4),

    # --- FamilyMart（既存） ---
    ("familymart","foods","鮭おにぎり",185,150,"breakfast", 5, 2, 37, 1),
    ("familymart","foods","明太子おにぎり",180,140,"breakfast", 4, 2, 38, 1),
    ("familymart","foods","グリルチキン(ハーブ)",165,220,"any", 22, 6, 2, 0),
    ("familymart","foods","ライザップチキンサラダ",210,398,"lunch", 15, 10, 10, 5),
    ("familymart","foods","スパゲティ ナポリタン(小)",420,430,"lunch", 12, 12, 58, 4),
    ("familymart","foods","さば塩焼き",280,350,"dinner", 22, 20, 0, 0),
    ("familymart","foods","とん汁",160,290,"any", 9, 8, 14, 2),
    ("familymart","foods","唐揚げ弁当",650,520,"lunch", 24, 28, 70, 4),
    ("familymart","foods","枝豆",120,200,"any", 10, 5, 8, 4),
    ("familymart","foods","サラダチキン(スモーク)",130,250,"any", 23, 3, 1, 0),
    ("familymart","foods","冷やし中華",450,480,"lunch", 16, 12, 64, 4),
    ("familymart","foods","ミニサラダ",60,150,"any", 2, 2, 8, 3),
    ("familymart","foods","ハンバーグ弁当",720,560,"dinner", 28, 36, 70, 5),
    ("familymart","foods","ヨーグルト(加糖)",110,160,"breakfast", 6, 4, 16, 0),
    ("familymart","foods","味噌汁",35,100,"any", 2, 1, 4, 0.5),
    ("familymart","foods","野菜ジュース",70,130,"any", 1, 0, 16, 2),

    # --- FamilyMart（追加+10） ---
    ("familymart","foods","ツナコーンサンド",320,330,"breakfast", 13, 14, 35, 3),
    ("familymart","foods","グリルサーモン弁当",560,598,"lunch", 28, 16, 70, 4),
    ("familymart","foods","チキンと卵のサラダ",210,380,"lunch", 15, 11, 10, 4),
    ("familymart","foods","豆腐バー",120,160,"any", 13, 6, 4, 2),
    ("familymart","foods","玄米おにぎり(梅)",200,150,"any", 4, 2, 42, 3),
    ("familymart","foods","サバ味噌煮(惣菜)",260,360,"dinner", 20, 16, 10, 0),
    ("familymart","foods","蒸し鶏サラダ",160,340,"any", 18, 6, 8, 3),
    ("familymart","foods","バナナ",90,120,"breakfast", 1, 0, 23, 2),
    ("familymart","foods","雑穀ロール",190,150,"breakfast", 6, 4, 34, 4),
    ("familymart","foods","ささみスモーク",110,210,"any", 23, 1, 1, 0),

    # --- FamilyMart（追加+20） ---
    ("familymart","foods","チキンステーキ弁当(小)",520,560,"dinner", 28, 16, 56, 3),
    ("familymart","foods","鮭とわかめおにぎり",190,150,"any", 6, 2, 38, 2),
    ("familymart","foods","たまごサンド",300,320,"breakfast", 12, 14, 28, 2),
    ("familymart","foods","サーモンサラダ",220,420,"lunch", 16, 10, 12, 5),
    ("familymart","foods","ひじき煮(小)",90,160,"any", 4, 3, 12, 5),
    ("familymart","foods","ほうれん草ナムル",70,150,"any", 3, 3, 6, 3),
    ("familymart","foods","雑穀おにぎり(昆布)",190,150,"any", 4, 2, 40, 4),
    ("familymart","foods","チキンとブロッコリー",230,420,"dinner", 27, 8, 8, 5),
    ("familymart","foods","冷やし蕎麦(小)",350,410,"lunch", 13, 5, 60, 5),
    ("familymart","foods","プロテインヨーグルト",120,190,"breakfast", 10, 3, 12, 0),
    ("familymart","foods","豆乳(200ml)",110,130,"breakfast", 7, 6, 6, 1),
    ("familymart","foods","サラダラップ(チキン)",260,360,"lunch", 14, 9, 30, 5),
    ("familymart","foods","雑穀パン(2枚)",260,220,"breakfast", 10, 5, 44, 6),
    ("familymart","foods","鶏そぼろ丼(小)",470,520,"lunch", 22, 12, 62, 3),
    ("familymart","foods","野菜スープ",70,150,"any", 3, 2, 10, 2),
    ("familymart","foods","シーザーチキンサラダ",240,420,"any", 18, 12, 10, 3),
    ("familymart","foods","オートミールおにぎり",210,180,"any", 6, 3, 40, 5),
    ("familymart","foods","たまご粥(小)",180,250,"breakfast", 8, 4, 30, 1),
    ("familymart","foods","サバ塩焼き弁当(小)",520,560,"dinner", 26, 18, 56, 3),
    ("familymart","foods","ヨーグルト(無糖)",60,140,"breakfast", 5, 3, 4, 0),

    # --- HottoMotto（既存） ---
    ("hottomotto","bento","のり弁",700,420,"lunch", 18, 25, 95, 5),
    ("hottomotto","bento","から揚弁当(ライス小)",650,480,"lunch", 24, 28, 70, 4),
    ("hottomotto","bento","銀鮭弁当(ライス小)",540,560,"lunch", 26, 16, 65, 3),
    ("hottomotto","bento","チキン南蛮弁当",780,590,"dinner", 30, 35, 80, 4),
    ("hottomotto","bento","とんかつ弁当",820,600,"dinner", 28, 40, 75, 4),
    ("hottomotto","bento","焼肉弁当",750,580,"dinner", 26, 32, 80, 4),
    ("hottomotto","bento","サバの味噌煮弁当",610,570,"dinner", 28, 20, 70, 3),
    ("hottomotto","bento","サラダ(小)",90,160,"any", 3, 3, 12, 4),
    ("hottomotto","bento","豚汁",150,210,"any", 8, 8, 12, 2),
    ("hottomotto","bento","味噌汁",35,100,"any", 2, 1, 4, 0.5),
    ("hottomotto","bento","枝豆",110,200,"any", 9, 5, 8, 4),
    ("hottomotto","bento","白身フライ単品",250,180,"any", 10, 16, 16, 1),
    ("hottomotto","bento","から揚単品(2個)",220,170,"any", 14, 14, 8, 0),

    # --- HottoMotto（追加+10） ---
    ("hottomotto","bento","玄米ごはん(小)",220,130,"any", 4, 1, 48, 2),
    ("hottomotto","bento","彩り野菜サラダ",120,180,"any", 4, 4, 14, 5),
    ("hottomotto","bento","ひじき煮",100,160,"any", 4, 3, 14, 5),
    ("hottomotto","bento","焼き魚単品(さば)",260,280,"dinner", 22, 18, 0, 0),
    ("hottomotto","bento","冷奴",80,120,"any", 7, 4, 3, 1),
    ("hottomotto","bento","具だくさん味噌汁",90,150,"any", 5, 3, 10, 2),
    ("hottomotto","bento","鶏むねグリル単品",210,320,"any", 32, 6, 2, 0),
    ("hottomotto","bento","もち麦ごはん(小)",240,150,"any", 5, 2, 50, 5),
    ("hottomotto","bento","ポテトサラダ(小)",160,140,"any", 3, 9, 16, 2),
    ("hottomotto","bento","きんぴらごぼう",110,150,"any", 2, 4, 16, 4),

    # --- HottoMotto（追加+20） ---
    ("hottomotto","bento","塩鮭単品",230,260,"any", 22, 14, 0, 0),
    ("hottomotto","bento","ミニ冷やしうどん",320,360,"lunch", 9, 4, 62, 3),
    ("hottomotto","bento","鶏そぼろ弁当(小)",520,520,"lunch", 24, 14, 62, 3),
    ("hottomotto","bento","照り焼きチキン弁当(小)",560,560,"dinner", 28, 16, 64, 3),
    ("hottomotto","bento","ブロッコリー(カップ)",60,130,"any", 4, 1, 6, 3),
    ("hottomotto","bento","サラダチキン(プレーン)",120,240,"any", 24, 1, 1, 0),
    ("hottomotto","bento","雑穀おにぎり",200,150,"any", 4, 2, 42, 4),
    ("hottomotto","bento","たまご焼き(2切)",110,140,"any", 7, 7, 5, 0),
    ("hottomotto","bento","野菜スープ",70,140,"any", 3, 2, 10, 2),
    ("hottomotto","bento","鶏むね唐揚げ(控えめ)",260,320,"any", 22, 12, 14, 1),
    ("hottomotto","bento","さば塩焼き単品",270,300,"any", 23, 18, 0, 0),
    ("hottomotto","bento","雑穀ロール(パン)",190,150,"breakfast", 6, 4, 34, 4),
    ("hottomotto","bento","たまごサンド(小)",280,300,"breakfast", 12, 14, 26, 2),
    ("hottomotto","bento","焼きおにぎり×2",300,180,"breakfast", 6, 2, 64, 2),
    ("hottomotto","bento","オートミール粥(小)",220,240,"breakfast", 8, 4, 36, 6),
    ("hottomotto","bento","鶏胸グリルと野菜",260,420,"dinner", 30, 8, 10, 5),
    ("hottomotto","bento","冷やし蕎麦(小)",350,410,"lunch", 13, 5, 60, 5),
    ("hottomotto","bento","豆腐ハンバーグ弁当(小)",540,560,"dinner", 24, 16, 68, 5),
    ("hottomotto","bento","たまご粥(小)",180,230,"breakfast", 8, 4, 30, 1),
    ("hottomotto","bento","ひよこ豆サラダ",230,360,"any", 10, 8, 28, 7),
]

@st.cache_data
def load_products_df():
    return pd.DataFrame(
        PRODUCTS,
        columns=[
            "store","category","name","kcal","price_jpy","meal_slot_hint",
            "protein_g","fat_g","carb_g","fiber_g"
        ]
    )

# -----------------------------
# TDEE（ハリス・ベネディクト改良版）＋活動係数5段階
# -----------------------------
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
    deficit_total = delta_w * 7700.0  # 体脂肪1kg ≈ 7700kcal
    deficit_per_day = deficit_total / days
    intake = max(1200, int(tdee - deficit_per_day))
    return intake, tdee, int(deficit_per_day), days

# -----------------------------
# 目安PFC（緩め）＋食物繊維下限
# -----------------------------
def target_pfc_grams(intake_kcal, weight_kg, p_per_kg=1.6, f_ratio=0.25):
    p_g = weight_kg * p_per_kg
    f_g = (intake_kcal * f_ratio) / 9.0
    c_kcal = intake_kcal - (p_g*4 + f_g*9)
    c_g = max(0, c_kcal / 4.0)
    return p_g, f_g, c_g

FIBER_MIN_G = 18

# -----------------------------
# コンボ生成（1〜3品）— PFC/Fiber 合算
# -----------------------------
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

def top_candidates_by_target(combos, target_kcal, keep_top=140):
    scored = [{"kcal":c["kcal"], "price":c["price"], "items":c["items"],
               "protein":c["protein"], "fat":c["fat"], "carb":c["carb"], "fiber":c["fiber"],
               "absdiff":abs(c["kcal"]-target_kcal)} for c in combos]
    scored.sort(key=lambda x: (x["absdiff"], x["price"]))
    return scored[:keep_top]

# -----------------------------
# スコア関数（カロリー主軸＋栄養はソフトに）
# -----------------------------
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

# 補助：コンボ中の「商品名」集合
def names_set(combo):
    return set(x["name"] for x in combo["items"])

# -----------------------------
# 3:4:3 固定（朝30/昼40/夜30）＋「商品名の重複禁止」でスコア最小
# -----------------------------
def optimize_day_fixed_score_no_overlap(combos_b, combos_l, combos_d, intake, budget, weight_kg):
    t_b = int(intake*0.30)
    t_l = int(intake*0.40)
    t_d = intake - t_b - t_l

    tg_p, tg_f, tg_c = target_pfc_grams(intake, weight_kg)

    cands_b = top_candidates_by_target(combos_b, t_b)
    cands_l = top_candidates_by_target(combos_l, t_l)
    cands_d = top_candidates_by_target(combos_d, t_d)

    best, best_score = None, float("inf")

    for cb in cands_b:
        names_b = names_set(cb)
        for cl in cands_l:
            # 朝と昼で同一商品名を使わない
            if names_b & names_set(cl):
                continue
            price_bl = cb["price"] + cl["price"]
            if price_bl > budget:
                continue

            kcal_bl = cb["kcal"] + cl["kcal"]
            p_bl = cb["protein"] + cl["protein"]
            f_bl = cb["fat"] + cl["fat"]
            c_bl = cb["carb"] + cl["carb"]
            fiber_bl = cb["fiber"] + cl["fiber"]
            names_bl = names_b | names_set(cl)

            remain = intake - kcal_bl
            # 残りに近い夕食を優先
            for cd in sorted(cands_d, key=lambda x:(abs(x["kcal"]-remain), x["price"]))[:200]:
                # 朝昼夜で同一商品名を使わない
                if names_bl & names_set(cd):
                    continue

                price_total = price_bl + cd["price"]
                if price_total > budget:
                    continue

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

# -----------------------------
# UI
# -----------------------------
st.title("食事改善アプリ（3:4:3固定・栄養考慮・重複禁止）")

with st.expander("条件入力", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        age = st.number_input("年齢", 18, 80, 33)
        sex = st.radio("性別", ["male","female"], horizontal=True)
        height = st.number_input("身長(cm)", 140, 210, 173)
        weight_now = st.number_input("現在体重(kg)", 35.0, 150.0, 70.0, step=0.1)
        weight_goal = st.number_input("目標体重(kg)", 35.0, 150.0, 65.0, step=0.1)
    with c2:
        deadline = st.date_input("期限日付", dt.date.today() + dt.timedelta(days=60))
        activity = st.selectbox(
            "活動量（TDEEの係数）",
            [
                "ほぼ運動しない(1.2)",
                "軽い運動(1.375)",
                "中程度の運動(1.55)",
                "激しい運動(1.725)",
                "非常に激しい(1.9)",
            ],
            index=1,
        )
        daily_budget = st.number_input("1日予算(円)", 300, 4000, 1200, step=10)
        store = st.selectbox("カテゴリ/店舗", ["seven","familymart","hottomotto"])
        st.caption("配分は固定：朝30% / 昼40% / 夜30%。同じ商品名の重複は許可しません。")

# 目標摂取カロリー
intake, tdee, deficit_day, days = calc_target_intake(
    age, sex, height, weight_now, weight_goal, deadline, activity
)
st.info(
    f"基礎TDEE: {tdee} kcal /日\n"
    f"必要赤字(目安): {deficit_day} kcal /日 × {days}日\n"
    f"目標摂取カロリー: **{intake} kcal /日**"
)

# プラン生成
if st.button("きょうの3食プランを作る", type="primary"):
    df = load_products_df()
    df = df[df["store"] == store].reset_index(drop=True)
    if df.empty:
        st.error("店舗データがありません。"); st.stop()

    # スロット分割
    df_b = df[df["meal_slot_hint"].isin(["breakfast","any"])]
    df_l = df[df["meal_slot_hint"].isin(["lunch","any"])]
    df_d = df[df["meal_slot_hint"].isin(["dinner","any"])]

    combos_b = generate_item_combos(df_b, budget=daily_budget)
    combos_l = generate_item_combos(df_l, budget=daily_budget)
    combos_d = generate_item_combos(df_d, budget=daily_budget)

    if not (combos_b and combos_l and combos_d):
        st.warning("候補が不足しています。商品を増やすか予算を調整してください。"); st.stop()

    best, score = optimize_day_fixed_score_no_overlap(
        combos_b, combos_l, combos_d, intake, daily_budget, weight_kg=weight_now
    )

    if best:
        # 表示テーブル
        def explode_slot(slot, jp):
            rows = []
            for it in best[slot]["items"]:
                rows.append([jp, it["name"], it["kcal"], it["protein_g"], it["fat_g"], it["carb_g"], it["fiber_g"], it["price_jpy"]])
            return rows

        rows = []
        rows += explode_slot("breakfast","朝")
        rows += explode_slot("lunch","昼")
        rows += explode_slot("dinner","夜")
        res = pd.DataFrame(rows, columns=["meal_slot","name","kcal","P(g)","F(g)","C(g)","Fiber(g)","price_jpy"])

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
