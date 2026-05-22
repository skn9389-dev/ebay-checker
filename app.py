"""
eBay 偽造品ポリシー違反リスク検出ツール — Streamlit ウェブアプリ版
"""

import csv
import io
import re
from dataclasses import dataclass, field

import streamlit as st
import pandas as pd

# ─────────────────────────────────────────────
# リスクワード辞書
# ─────────────────────────────────────────────

RISK_WORDS: list[tuple[str, int, str]] = [
    (r"\bfake\b",                90, "偽物であることを明示"),
    (r"\breplica\b",             85, "レプリカ（偽造品の婉曲表現）"),
    (r"\bcounterfeit\b",         95, "偽造品であることを明示"),
    (r"\bknockoff\b",            90, "ノックオフ（模造品）"),
    (r"\bimitation\b",           70, "イミテーション"),
    (r"\bbootleg\b",             85, "ブートレグ（非正規品）"),
    (r"\bpirated?\b",            90, "海賊版"),
    (r"\bfaux\b",                50, "フォー（偽物の意）"),
    (r"\bnot\s+authentic\b",     80, "本物ではないと明記"),
    (r"\bnot\s+genuine\b",       80, "本物ではないと明記"),
    (r"\bnot\s+original\b",      70, "オリジナルではないと明記"),
    (r"\binspired\s+by\b",       60, "ブランドに触発されたと示唆"),
    (r"\bstyle\s+of\b",          50, "ブランドのスタイルと示唆"),
    (r"\blook[\s-]alike\b",      65, "見た目が似ているを示唆"),
    (r"\bsame\s+as\b",           55, "同じと主張（ブランド模倣の疑い）"),
    (r"\bidentical\s+to\b",      60, "同一と主張"),
    (r"\bhigh[- ]?quality\s+copy\b", 80, "高品質コピーを示唆"),
    (r"\bsuper\s+copy\b",        85, "スーパーコピー"),
    (r"\baaa[\+\s]*grade\b",     80, "AAAグレード（偽造品の隠語）"),
    (r"\b1:1\b",                 75, "1:1（完全コピーの隠語）"),
    (r"\bunauthorized\b",        70, "非公認"),
    (r"\bunofficial\b",          50, "非公式"),
    (r"\boff[\s-]?market\b",     65, "市場外品"),
    (r"\bgrey\s+market\b",       55, "グレーマーケット"),
    (r"\bno\s+box\b",            20, "箱なし"),
    (r"\bno\s+tags?\b",          20, "タグなし"),
]

BRAND_NAMES: list[str] = [
    "louis vuitton", "lv", "gucci", "chanel", "prada", "hermes", "hermès",
    "rolex", "omega", "patek philippe", "cartier", "breitling",
    "nike", "adidas", "supreme", "off-white", "balenciaga", "yeezy",
    "apple", "airpods", "iphone", "samsung", "sony",
    "coach", "michael kors", "kate spade", "burberry",
]

# ─────────────────────────────────────────────
# 検出ロジック
# ─────────────────────────────────────────────

@dataclass
class CheckResult:
    row_index: int
    original_title: str
    original_desc: str
    risk_score: int
    risk_level: str
    detected_words: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    safe_title: str = ""


def _score_text(text: str) -> tuple[int, list[str], list[str]]:
    lower = text.lower()
    max_score = 0
    detected, reasons = [], []
    for pattern, score, reason in RISK_WORDS:
        if re.search(pattern, lower, re.IGNORECASE):
            m = re.search(pattern, lower, re.IGNORECASE)
            detected.append(m.group(0) if m else pattern)
            reasons.append(reason)
            max_score = max(max_score, score)
    for brand in BRAND_NAMES:
        if brand in lower and brand not in detected:
            detected.append(brand)
            reasons.append(f"有名ブランド名「{brand}」を含む（真正品の証明が必要）")
            max_score = max(max_score, max_score + 10 if max_score > 0 else 15)
    return min(max_score, 100), detected, reasons


def _risk_level(score: int) -> str:
    if score >= 80: return "HIGH"
    if score >= 50: return "MEDIUM"
    if score >= 15: return "LOW"
    return "SAFE"


def _safe_title(title: str, detected: list[str]) -> str:
    result = title
    for word in detected:
        result = re.sub(re.escape(word), "[REMOVED]", result, flags=re.IGNORECASE)
    return result.strip()


def check_row(index: int, title: str, desc: str) -> CheckResult:
    score, detected, reasons = _score_text(f"{title} {desc}")
    return CheckResult(
        row_index=index,
        original_title=title,
        original_desc=desc,
        risk_score=score,
        risk_level=_risk_level(score),
        detected_words=detected,
        reasons=reasons,
        safe_title=_safe_title(title, detected),
    )


# ─────────────────────────────────────────────
# Streamlit UI
# ─────────────────────────────────────────────

st.set_page_config(page_title="eBay リスクチェッカー", page_icon="🛡️", layout="wide")

st.title("🛡️ eBay 偽造品ポリシー リスクチェッカー")
st.caption("CSVをアップロードするだけで、Title・Descriptionの危険ワードを自動検出します。")

uploaded = st.file_uploader("CSVファイルをアップロード", type=["csv"])

if uploaded:
    df = pd.read_csv(uploaded, encoding="utf-8-sig")
    col_map = {c.lower(): c for c in df.columns}
    title_col = col_map.get("title", "")
    desc_col  = col_map.get("description", "")

    if not title_col:
        st.error("「Title」列が見つかりません。CSVの列名を確認してください。")
        st.stop()

    results: list[CheckResult] = []
    for i, row in df.iterrows():
        title = str(row.get(title_col, ""))
        desc  = str(row.get(desc_col, "")) if desc_col else ""
        results.append(check_row(int(i) + 2, title, desc))

    # ── サマリー ──────────────────────────────
    counts = {lvl: sum(1 for r in results if r.risk_level == lvl)
              for lvl in ("HIGH", "MEDIUM", "LOW", "SAFE")}

    st.markdown("---")
    st.subheader("📊 集計結果")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🚨 HIGH",   counts["HIGH"],   help="即修正が必要")
    c2.metric("⚠️ MEDIUM", counts["MEDIUM"], help="要確認")
    c3.metric("💡 LOW",    counts["LOW"],    help="軽微な注意")
    c4.metric("✅ SAFE",   counts["SAFE"],   help="問題なし")

    # ── 詳細テーブル ──────────────────────────
    st.markdown("---")
    st.subheader("📋 商品別レポート")

    LEVEL_EMOJI = {"HIGH": "🚨", "MEDIUM": "⚠️", "LOW": "💡", "SAFE": "✅"}
    LEVEL_COLOR = {"HIGH": "#ff4b4b", "MEDIUM": "#ffa500", "LOW": "#00bcd4", "SAFE": "#21c354"}

    for r in results:
        emoji = LEVEL_EMOJI[r.risk_level]
        color = LEVEL_COLOR[r.risk_level]
        label = f"{emoji} 行{r.row_index}  |  スコア {r.risk_score}/100  |  {r.original_title}"

        with st.expander(label):
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**危険度レベル**")
                st.markdown(f"<span style='color:{color};font-size:1.4em;font-weight:bold'>{r.risk_level}</span>",
                            unsafe_allow_html=True)
                st.metric("スコア", f"{r.risk_score} / 100")
            with col_b:
                if r.detected_words:
                    st.markdown("**検出されたワード**")
                    for w, reason in zip(r.detected_words, r.reasons):
                        st.markdown(f"- `{w}` — {reason}")
                else:
                    st.success("危険なワードは検出されませんでした")

            if r.risk_level != "SAFE":
                st.markdown("**安全なタイトル候補**")
                st.info(r.safe_title)

    # ── 修正済みCSVダウンロード ───────────────
    st.markdown("---")
    st.subheader("⬇️ 修正済みCSVをダウンロード")

    out_df = df.copy()
    out_df["risk_score"]     = [r.risk_score for r in results]
    out_df["risk_level"]     = [r.risk_level for r in results]
    out_df["detected_words"] = ["; ".join(r.detected_words) for r in results]
    out_df["reasons"]        = ["; ".join(r.reasons) for r in results]
    out_df["safe_title"]     = [r.safe_title for r in results]

    csv_bytes = out_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="📥 修正済みCSVをダウンロード",
        data=csv_bytes,
        file_name="listings_checked.csv",
        mime="text/csv",
    )

else:
    st.info("👆 上のボタンからCSVファイルをアップロードしてください")
    st.markdown("""
    ### CSVに必要な列
    | 列名 | 必須 |
    |------|------|
    | Title | ✅ |
    | Description | 任意 |
    """)
