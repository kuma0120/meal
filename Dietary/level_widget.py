# level_widget.py
import os
import datetime as dt
from typing import Optional, Tuple
import unicodedata
import streamlit as st

# -----------------------
# 進捗 → レベル 変換
# -----------------------
def compute_progress_percent(start_w: float, goal_w: float, current_w: float) -> float:
    """初期体重→目標体重に対する到達率 (0.0〜1.0)。減量/増量どちらでもOK。"""
    total = abs(goal_w - start_w)
    if total < 1e-6:
        return 1.0
    moved = abs(current_w - start_w)
    return max(0.0, min(1.0, moved / total))

def progress_to_level(p: float) -> int:
    """0,25,50,75,100% を閾値に 5段階 (0〜4)。"""
    if p >= 1.0: return 4
    if p >= 0.75: return 3
    if p >= 0.50: return 2
    if p >= 0.25: return 1
    return 0

# -----------------------
# Supabase からデータ取得
# -----------------------
@st.cache_data(show_spinner=False, ttl=60)
def fetch_profile_and_latest_weight(supabase, user_id: str) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[str]]:
    """
    想定テーブル:
      profiles(user_id, start_weight, goal_weight, ...)
      weight_logs(user_id, weight, logged_at, ...)
    戻り値: (start_weight, goal_weight, current_weight, latest_date_str) ※存在しなければ None
    """
    prof = supabase.table("profiles").select("start_weight, goal_weight").eq("user_id", user_id).single().execute()
    if getattr(prof, "error", None) or not getattr(prof, "data", None):
        return None, None, None, None
    start_w = prof.data.get("start_weight")
    goal_w = prof.data.get("goal_weight")

    logs = supabase.table("weight_logs").select("weight, logged_at").eq("user_id", user_id).order("logged_at", desc=True).limit(1).execute()
    if getattr(logs, "error", None) or not getattr(logs, "data", None):
        return start_w, goal_w, None, None
    current_w = logs.data[0].get("weight")
    latest_date = logs.data[0].get("logged_at")
    return start_w, goal_w, current_w, latest_date

# -----------------------
# 画像パスの解決
# （日本語ファイル名のNFC/NFD差異対策あり）
# -----------------------
def _norm_path(path: str) -> str:
    # macOS 由来の濁点分解(NFD)でのズレ対策
    return unicodedata.normalize("NFC", path)

# ★要件: カビゴン → プリン → コダック → ピカチュウ → カスミ
LEVEL_IMAGE_PATHS = [
    "Dietary/kabigon.png",   # Lv0
    "Dietary/purin.png",     # Lv1
    "Dietary/koduck.png",   # Lv2
    "Dietary/pikachu.png", # Lv3
    "Dietary/kasumi.png",     # Lv4
]

def get_level_image(level: int) -> Optional[str]:
    """ローカルに存在する最適な画像パスを返す"""
    idx = max(0, min(4, int(level)))
    candidates = [
        _norm_path(LEVEL_IMAGE_PATHS[idx]),
    ]
    for p in candidates:
        if p.startswith(("http://", "https://")):
            return p
        if os.path.exists(p):
            return p
    return None

# -----------------------
# 表示ウィジェット本体
# -----------------------
def render_level_widget(supabase, user_id: str, *, show_quick_log: bool = True):
    with st.container(border=True):
        st.subheader("レベルアップ")

        start_w, goal_w, current_w, latest_date = fetch_profile_and_latest_weight(supabase, user_id)

        # 未設定時のフォールバック入力（DB保存はしない）
        if start_w is None or goal_w is None:
            st.warning("初期体重/目標体重が未設定です。暫定値で表示します（DB保存なし）。")
            c1, c2 = st.columns(2)
            with c1:
                start_w = st.number_input("初期体重(kg)", 30.0, 200.0, 70.0, step=0.1, key="tmp_start")
            with c2:
                goal_w = st.number_input("目標体重(kg)", 30.0, 200.0, 65.0, step=0.1, key="tmp_goal")

        if current_w is None:
            st.info("最新の体重ログがありません。暫定値で表示します（DB保存なし）。")
            current_w = st.number_input("現在体重(kg)", 30.0, 200.0, start_w, step=0.1, key="tmp_now")

        p = compute_progress_percent(start_w, goal_w, current_w)
        level = progress_to_level(p)

        # レベルアップ演出
        prev_key = f"_prev_level_{user_id}"
        prev = st.session_state.get(prev_key)
        st.session_state[prev_key] = level
        if prev is not None and level > prev:
            st.balloons()

        c1, c2 = st.columns([1,2], vertical_alignment="center")
        with c1:
            st.metric("レベル", f"{level} / 4")
            st.progress(p, text=f"進捗 {int(p*100)}%")
            if latest_date:
                st.caption(f"最終ログ: {latest_date}")
            if show_quick_log:
                quick_log_weight(supabase, user_id)
        with c2:
            img = get_level_image(level)
            if img:
                st.image(img, use_column_width=True)
            else:
                st.info("レベル画像が見つかりません。assets/levels に画像を配置してください。")

        captions = [
            "スタート！少しずつ体を慣らそう。",
            "順調！食事リズムをキープ。",
            "折り返し。PFCと睡眠も最適化。",
            "ゴール目前！仕上げの一週間。",
            "達成！メンテナンス期へ移行。",
        ]
        st.caption(captions[level])
