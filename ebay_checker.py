"""
eBay 偽造品ポリシー違反リスク検出ツール
"""

import csv
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field

# ─────────────────────────────────────────────
# リスクワード辞書
# ─────────────────────────────────────────────

# (単語パターン, スコア, 理由)
RISK_WORDS: list[tuple[str, int, str]] = [
    # 明示的な偽造示唆 → 高リスク
    (r"\bfake\b",           90, "偽物であることを明示"),
    (r"\breplica\b",        85, "レプリカ（偽造品の婉曲表現）"),
    (r"\bcounterfeit\b",    95, "偽造品であることを明示"),
    (r"\bknockoff\b",       90, "ノックオフ（模造品）"),
    (r"\bimitation\b",      70, "イミテーション（偽物の示唆）"),
    (r"\bbootleg\b",        85, "ブートレグ（非正規品）"),
    (r"\bpirated?\b",       90, "海賊版"),
    (r"\bfaux\b",           50, "フォー（フランス語で偽物）"),
    (r"\bnot\s+authentic\b",80, "本物ではないと明記"),
    (r"\bnot\s+genuine\b",  80, "本物ではないと明記"),
    (r"\bnot\s+original\b", 70, "オリジナルではないと明記"),
    # 価格・品質を装う曖昧表現 → 中リスク
    (r"\binspired\s+by\b",  60, "ブランドに触発されたと示唆"),
    (r"\bstyle\s+of\b",     50, "ブランドのスタイルと示唆"),
    (r"\blook[\s-]alike\b", 65, "見た目が似ているを示唆"),
    (r"\bsame\s+as\b",      55, "同じと主張（ブランド模倣の疑い）"),
    (r"\bidentical\s+to\b", 60, "同一と主張"),
    (r"\bhigh[- ]?quality\s+copy\b", 80, "高品質コピーを示唆"),
    (r"\bsuper\s+copy\b",   85, "スーパーコピー"),
    (r"\baaa[\+\s]*grade\b",80, "AAAグレード（偽造品の隠語）"),
    (r"\b1:1\b",            75, "1:1（完全コピーの隠語）"),
    # 正規流通外を示す表現 → 中リスク
    (r"\bunauthorized\b",   70, "非公認"),
    (r"\bunofficial\b",     50, "非公式（状況により問題）"),
    (r"\boff[\s-]?market\b",65, "市場外品"),
    (r"\bgrey\s+market\b",  55, "グレーマーケット"),
    (r"\bno\s+box\b",       20, "箱なし（低リスクだが注意）"),
    (r"\bno\s+tags?\b",     20, "タグなし（低リスクだが注意）"),
    # 有名ブランド名＋疑惑語の組み合わせチェックは後述の BRAND_RISK で実施
]

# ブランド名は「注意メモ」として表示するだけ → スコアに加算しない
# 真正品を出品している場合はブランド名を書いてOKなので削除しない
BRAND_NAMES: list[str] = [
    "louis vuitton", "gucci", "chanel", "prada", "hermes", "hermès",
    "rolex", "omega", "patek philippe", "cartier", "breitling",
    "nike", "adidas", "supreme", "off-white", "balenciaga", "yeezy",
    "apple", "airpods", "iphone", "samsung", "sony",
    "coach", "michael kors", "kate spade", "burberry",
]

# ブランド名と一緒に使うと危険な組み合わせワード
BRAND_DANGER_COMBOS: list[str] = [
    "replica", "fake", "copy", "knockoff", "imitation",
    "inspired", "style", "aaa", "1:1", "super copy",
]

# ─────────────────────────────────────────────
# データ構造
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
    modified_desc: str = ""


# ─────────────────────────────────────────────
# コア検出ロジック
# ─────────────────────────────────────────────

def _score_text(text: str) -> tuple[int, list[str], list[str]]:
    """テキストをスキャンしてスコア・検出語・理由を返す。"""
    lower = text.lower()
    max_score = 0
    detected: list[str] = []
    reasons: list[str] = []
    brand_notes: list[str] = []  # ブランド名は別管理（スコアに加算しない）

    # ① 危険ワードをチェック（スコアに加算）
    for pattern, score, reason in RISK_WORDS:
        if re.search(pattern, lower, re.IGNORECASE):
            match = re.search(pattern, lower, re.IGNORECASE)
            word = match.group(0) if match else pattern
            detected.append(word)
            reasons.append(reason)
            max_score = max(max_score, score)

    # ② ブランド名チェック（スコアには加算しない・注意メモのみ）
    found_brands = []
    for brand in BRAND_NAMES:
        if brand in lower:
            found_brands.append(brand)

    if found_brands:
        # ブランド名＋危険ワードの組み合わせがある場合だけスコアに加算
        for combo in BRAND_DANGER_COMBOS:
            if combo in lower:
                max_score = max(max_score, 85)
                break
        # 注意メモとして追加（SAFEでも表示）
        brand_notes.append(f"ブランド名を含む: {', '.join(found_brands)} ※真正品なら問題なし")

    return min(max_score, 100), detected, reasons, brand_notes


def _risk_level(score: int) -> str:
    if score >= 80:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    if score >= 15:
        return "LOW"
    return "SAFE"


def _safe_title(title: str, detected: list[str]) -> str:
    """検出語を [REMOVED] に置換した安全タイトル候補を返す。"""
    result = title
    for word in detected:
        result = re.sub(re.escape(word), "[REMOVED]", result, flags=re.IGNORECASE)
    return result.strip()


def check_row(index: int, title: str, desc: str) -> CheckResult:
    combined = f"{title} {desc}"
    score, detected, reasons, brand_notes = _score_text(combined)
    level = _risk_level(score)
    safe = _safe_title(title, detected)

    # ブランド注意メモをreasonsに追加（スコアには影響しない）
    all_reasons = reasons + brand_notes

    # description の危険語を [FLAGGED: 理由] に置換
    modified_desc = desc
    for word, _, reason in RISK_WORDS:
        modified_desc = re.sub(
            word,
            lambda m, r=reason: f"[FLAGGED:{r}]",
            modified_desc,
            flags=re.IGNORECASE,
        )

    return CheckResult(
        row_index=index,
        original_title=title,
        original_desc=desc,
        risk_score=score,
        risk_level=level,
        detected_words=detected,
        reasons=all_reasons,
        safe_title=safe,
        modified_desc=modified_desc,
    )


# ─────────────────────────────────────────────
# CSV 入出力
# ─────────────────────────────────────────────

def read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    return rows, fieldnames


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    extra = ["risk_score", "risk_level", "detected_words", "reasons", "safe_title"]
    all_fields = list(fieldnames) + [f for f in extra if f not in fieldnames]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields)
        writer.writeheader()
        writer.writerows(rows)


# ─────────────────────────────────────────────
# レポート表示
# ─────────────────────────────────────────────

LEVEL_COLORS = {
    "HIGH":   "\033[91m",  # 赤
    "MEDIUM": "\033[93m",  # 黄
    "LOW":    "\033[96m",  # シアン
    "SAFE":   "\033[92m",  # 緑
}
RESET = "\033[0m"


def print_report(results: list[CheckResult]) -> None:
    print("\n" + "=" * 60)
    print("  eBay 偽造品ポリシー リスクレポート")
    print("=" * 60)

    for r in results:
        color = LEVEL_COLORS.get(r.risk_level, "")
        print(f"\n行 {r.row_index:>3} │ {color}{r.risk_level:6}{RESET} │ スコア: {r.risk_score:>3}/100")
        print(f"  元タイトル  : {r.original_title}")
        if r.detected_words:
            print(f"  検出ワード  : {', '.join(r.detected_words)}")
            print(f"  修正理由    : {' / '.join(r.reasons)}")
        if r.risk_level != "SAFE":
            print(f"  安全タイトル: {r.safe_title}")

    print("\n" + "-" * 60)
    counts = {lvl: sum(1 for r in results if r.risk_level == lvl)
              for lvl in ("HIGH", "MEDIUM", "LOW", "SAFE")}
    for lvl, cnt in counts.items():
        color = LEVEL_COLORS.get(lvl, "")
        print(f"  {color}{lvl:6}{RESET}: {cnt} 件")
    print("=" * 60 + "\n")


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────

def main(input_path: str) -> None:
    src = Path(input_path)
    if not src.exists():
        print(f"[ERROR] ファイルが見つかりません: {src}")
        sys.exit(1)

    print(f"読み込み中: {src}")
    rows, fieldnames = read_csv(src)

    # Title / Description 列の自動検出（大文字小文字を無視）
    col_map = {c.lower(): c for c in fieldnames}
    title_col = col_map.get("title", "")
    desc_col  = col_map.get("description", "")

    if not title_col:
        print("[ERROR] 'Title' 列が見つかりません。列名を確認してください。")
        print(f"  検出された列: {fieldnames}")
        sys.exit(1)

    results: list[CheckResult] = []
    for i, row in enumerate(rows, start=2):  # 行番号はヘッダ=1 として 2 から
        title = row.get(title_col, "")
        desc  = row.get(desc_col, "") if desc_col else ""
        result = check_row(i, title, desc)
        results.append(result)

        # CSVに解析列を追記
        row["risk_score"]     = result.risk_score
        row["risk_level"]     = result.risk_level
        row["detected_words"] = "; ".join(result.detected_words)
        row["reasons"]        = "; ".join(result.reasons)
        row["safe_title"]     = result.safe_title
        if desc_col:
            row[desc_col] = result.modified_desc

    print_report(results)

    out = src.parent / f"{src.stem}_checked{src.suffix}"
    write_csv(out, rows, fieldnames)
    print(f"修正済み CSV を保存しました: {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方: python ebay_checker.py <input.csv>")
        print("例:     python ebay_checker.py listings.csv")
        sys.exit(0)
    main(sys.argv[1])
