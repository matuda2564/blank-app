import streamlit as st

st.set_page_config(
    page_title="Amazon AI エージェント",
    page_icon="🤖",
    layout="wide",
)

st.title("🤖 Amazon AIエージェント ダッシュボード")
st.markdown(
    """
このツールはAmazonの広告・商品ページをAIが分析し、改善提案を行います。
提案に承認するとAmazon APIを通じて自動で反映されます。

---

### 📂 メニュー（左のサイドバーから選択）

| ページ | 内容 |
|--------|------|
| **⚙️ 設定** | APIキーの入力・接続確認 |
| **📊 広告パフォーマンス** | 昨日の広告データを確認 |
| **🤖 AI 広告提案** | AIが入札・予算の改善を提案 → 承認で自動反映 |
| **🏪 商品ページ改善** | AIが商品ページの改善を提案 → 承認で自動反映 |

---

> **デモモードで今すぐ試せます。** APIキーは「設定」ページで後から入力できます。
"""
)

col1, col2, col3 = st.columns(3)
col1.metric("今日の広告費", "¥--", help="設定ページでAPIを接続すると表示されます")
col2.metric("今日の売上 (広告経由)", "¥--")
col3.metric("全体ACOS", "--%")

st.info("👈 左のサイドバーからページを選択してください")
