import streamlit as st

st.set_page_config(page_title="AI広告提案", page_icon="🤖", layout="wide")
st.title("🤖 AI 広告改善提案")

demo_mode = st.session_state.get("demo_mode", True)
target_acos = st.session_state.get("target_acos", 25)

st.markdown(f"**目標ACOS: {target_acos}%** (変更は「広告パフォーマンス」ページのスライダーから)")

col_btn1, col_btn2 = st.columns([2, 6])
with col_btn1:
    analyze_btn = st.button("🔍 AIに分析させる", type="primary")

if analyze_btn:
    kw_report = st.session_state.get("kw_report")
    if not kw_report and not demo_mode:
        st.warning("先に「広告パフォーマンス」ページでデータを取得してください")
        st.stop()

    with st.spinner("AIが広告データを分析中..."):
        try:
            if demo_mode:
                from amazon_advertising.ai_agent import get_demo_ad_proposals
                proposals = get_demo_ad_proposals()
            else:
                from amazon_advertising.ai_agent import analyze_ads
                proposals = analyze_ads(
                    keyword_rows=kw_report,
                    target_acos=target_acos,
                    ai_provider=st.session_state.get("ai_provider", "openai"),
                    api_key=st.session_state.get("ai_api_key", ""),
                )
            st.session_state["ad_proposals"] = proposals
            st.success(f"分析完了: {len(proposals)} 件の改善提案があります")
        except Exception as e:
            st.error(f"AI分析エラー: {e}")

proposals = st.session_state.get("ad_proposals", [])

if not proposals:
    st.info("「AIに分析させる」ボタンを押してください")
    st.stop()

st.divider()
st.subheader(f"改善提案一覧 ({len(proposals)} 件)")

pending = [p for p in proposals if not p.approved and not p.rejected]
approved_list = [p for p in proposals if p.approved and not p.executed]
done = [p for p in proposals if p.executed]

if pending:
    st.markdown("### 📋 未承認の提案")
    for i, p in enumerate(pending):
        with st.container(border=True):
            col1, col2 = st.columns([5, 1])
            with col1:
                badge = {"入札額を下げる": "🔻", "入札額を上げる": "🔺", "キーワードを停止": "⏸️", "予算を調整": "💰"}.get(p.proposal_type.value, "📝")
                st.markdown(f"**{badge} {p.proposal_type.value}**: `{p.target_name}`")
                st.markdown(f"**理由**: {p.reason}")
                st.markdown(f"**変更**: `{p.current_value}` → **`{p.suggested_value}`**")
                st.markdown(f"**期待効果**: {p.expected_effect}")
            with col2:
                if st.button("✅ 承認", key=f"approve_{i}", type="primary"):
                    p.approved = True
                    st.rerun()
                if st.button("❌ 却下", key=f"reject_{i}"):
                    p.rejected = True
                    st.rerun()

if approved_list:
    st.divider()
    st.markdown("### ✅ 承認済み（未実行）")
    for p in approved_list:
        st.markdown(f"- {p.proposal_type.value}: **{p.target_name}** `{p.current_value}` → `{p.suggested_value}`")

    if st.button("🚀 承認済みをすべて実行", type="primary"):
        if demo_mode:
            for p in approved_list:
                p.executed = True
            st.success("✅ デモモード: すべての提案を実行しました（実際のAPI変更はなし）")
            st.rerun()
        else:
            from amazon_advertising.auth import AdvAuth, AdvCredentials
            from amazon_advertising.campaigns import CampaignsAPI
            from amazon_advertising.executor import execute_proposal

            creds = AdvCredentials(
                client_id=st.session_state.get("adv_client_id", ""),
                client_secret=st.session_state.get("adv_client_secret", ""),
                refresh_token=st.session_state.get("adv_refresh_token", ""),
                profile_id=st.session_state.get("adv_profile_id", ""),
            )
            api = CampaignsAPI(AdvAuth(creds))
            results = []
            for p in approved_list:
                try:
                    msg = execute_proposal(p, api)
                    p.executed = True
                    results.append(msg)
                except Exception as e:
                    results.append(f"❌ [{p.target_name}] エラー: {e}")
            for r in results:
                st.write(r)
            st.rerun()

if done:
    st.divider()
    st.markdown("### ✔️ 実行済み")
    for p in done:
        st.markdown(f"- ~~{p.proposal_type.value}: **{p.target_name}**~~ ✔")
