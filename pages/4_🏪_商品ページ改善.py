import streamlit as st

st.set_page_config(page_title="商品ページ改善", page_icon="🏪", layout="wide")
st.title("🏪 商品ページ AI 改善提案")

demo_mode = st.session_state.get("demo_mode", True)

st.markdown("商品の現在の状態をAIが分析し、タイトル・箇条書き・説明文・画像の改善案を提案します。")

col1, col2 = st.columns([3, 1])
with col1:
    asin_input = st.text_input("ASIN を入力", value="B00DEMO001", placeholder="例: B07XXXXXYZ")
with col2:
    analyze_btn = st.button("🔍 AIに分析させる", type="primary")

if analyze_btn:
    with st.spinner("商品情報を取得・分析中..."):
        try:
            if demo_mode:
                from amazon_advertising.demo_data import get_demo_listing
                from amazon_advertising.ai_agent import get_demo_listing_proposals
                listing = get_demo_listing(asin_input)
                proposals = get_demo_listing_proposals()
            else:
                from amazon_sp_api.auth import SPAPIAuth, SPAPICredentials
                from amazon_sp_api.listings import ListingsAPI
                from amazon_advertising.ai_agent import analyze_listing

                creds = SPAPICredentials(
                    client_id=st.session_state.get("sp_client_id", ""),
                    client_secret=st.session_state.get("sp_client_secret", ""),
                    refresh_token=st.session_state.get("sp_refresh_token", ""),
                    marketplace_id="A1VC38T7YXB528",
                )
                auth = SPAPIAuth(creds)
                listing = ListingsAPI(auth).get_item(asin_input)
                proposals = analyze_listing(
                    listing=listing,
                    ai_provider=st.session_state.get("ai_provider", "openai"),
                    api_key=st.session_state.get("ai_api_key", ""),
                )

            st.session_state["current_listing"] = listing
            st.session_state["listing_proposals"] = proposals
            st.success(f"分析完了: {len(proposals)} 件の改善提案があります")
        except Exception as e:
            st.error(f"エラー: {e}")

listing = st.session_state.get("current_listing")
proposals = st.session_state.get("listing_proposals", [])

if listing:
    with st.expander("現在の商品情報", expanded=False):
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"**ASIN**: `{listing.get('asin', '')}`")
            st.markdown(f"**タイトル**: {listing.get('title', '')}")
            st.markdown(f"**価格**: ¥{listing.get('price', 0):,}")
            st.markdown(f"**評価**: ⭐ {listing.get('rating', '')} ({listing.get('reviewCount', 0)} レビュー)")
        with col_b:
            st.markdown("**箇条書き**:")
            for bp in listing.get("bulletPoints", []):
                st.markdown(f"- {bp}")
            st.markdown(f"**説明文**: {listing.get('description', '')[:200]}...")

if not proposals:
    if not listing:
        st.info("ASINを入力して「AIに分析させる」を押してください")
    st.stop()

st.divider()
st.subheader(f"改善提案 ({len(proposals)} 件)")

type_icons = {
    "タイトルを改善": "📝",
    "箇条書きを改善": "📋",
    "説明文を改善": "📄",
    "画像を追加・改善": "🖼️",
}

pending = [p for p in proposals if not p.approved and not p.rejected]
approved_list = [p for p in proposals if p.approved and not p.executed]
done = [p for p in proposals if p.executed]

if pending:
    st.markdown("### 📋 未承認の提案")
    for i, p in enumerate(pending):
        with st.container(border=True):
            icon = type_icons.get(p.proposal_type.value, "📝")
            st.markdown(f"**{icon} {p.proposal_type.value}**")
            st.markdown(f"**理由**: {p.reason}")

            col_curr, col_new = st.columns(2)
            with col_curr:
                st.markdown("**現在**")
                st.text_area("", value=str(p.current_value), height=120, disabled=True, key=f"curr_{i}")
            with col_new:
                st.markdown("**AI推奨**")
                st.text_area("", value=str(p.suggested_value), height=120, disabled=True, key=f"new_{i}")

            st.markdown(f"**期待効果**: {p.expected_effect}")

            col_ok, col_ng, _ = st.columns([1, 1, 5])
            with col_ok:
                if st.button("✅ 承認", key=f"listing_approve_{i}", type="primary"):
                    p.approved = True
                    st.rerun()
            with col_ng:
                if st.button("❌ 却下", key=f"listing_reject_{i}"):
                    p.rejected = True
                    st.rerun()

if approved_list:
    st.divider()
    st.markdown("### ✅ 承認済み（未実行）")
    for p in approved_list:
        st.markdown(f"- {p.proposal_type.value}: **{p.target_name}**")

    if st.button("🚀 承認済みをすべて実行", type="primary"):
        if demo_mode:
            for p in approved_list:
                p.executed = True
            st.success("✅ デモモード: すべての提案を実行しました（実際のAPI変更はなし）")
            st.rerun()
        else:
            from amazon_sp_api.auth import SPAPIAuth, SPAPICredentials
            from amazon_advertising.executor import execute_listing_proposal

            creds = SPAPICredentials(
                client_id=st.session_state.get("sp_client_id", ""),
                client_secret=st.session_state.get("sp_client_secret", ""),
                refresh_token=st.session_state.get("sp_refresh_token", ""),
                marketplace_id="A1VC38T7YXB528",
            )
            sp_auth = SPAPIAuth(creds)
            for p in approved_list:
                try:
                    msg = execute_listing_proposal(p, sp_auth)
                    p.executed = True
                    st.write(msg)
                except Exception as e:
                    st.error(f"❌ [{p.target_name}] エラー: {e}")
            st.rerun()

if done:
    st.divider()
    st.markdown("### ✔️ 実行済み")
    for p in done:
        st.markdown(f"- ~~{p.proposal_type.value}: **{p.target_name}**~~ ✔")
