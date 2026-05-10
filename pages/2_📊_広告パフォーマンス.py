import pandas as pd
import streamlit as st

st.set_page_config(page_title="広告パフォーマンス", page_icon="📊", layout="wide")
st.title("📊 広告パフォーマンス")

demo_mode = st.session_state.get("demo_mode", True)

if st.button("🔄 最新データを取得", type="primary"):
    with st.spinner("広告データを取得中..."):
        try:
            if demo_mode:
                from amazon_advertising.demo_data import get_demo_keyword_report, get_demo_campaigns
                rows = get_demo_keyword_report()
                campaigns = get_demo_campaigns()
            else:
                from amazon_advertising.auth import AdvAuth, AdvCredentials
                from amazon_advertising.campaigns import CampaignsAPI
                creds = AdvCredentials(
                    client_id=st.session_state.get("adv_client_id", ""),
                    client_secret=st.session_state.get("adv_client_secret", ""),
                    refresh_token=st.session_state.get("adv_refresh_token", ""),
                    profile_id=st.session_state.get("adv_profile_id", ""),
                )
                api = CampaignsAPI(AdvAuth(creds))
                rows = api.get_yesterday_report()
                campaigns = api.list_campaigns()

            st.session_state["kw_report"] = rows
            st.session_state["campaigns"] = campaigns
            st.success(f"取得完了: {len(rows)} キーワード")
        except Exception as e:
            st.error(f"取得エラー: {e}")

rows = st.session_state.get("kw_report")

if not rows:
    st.info("「最新データを取得」ボタンを押してください")
    st.stop()

df = pd.DataFrame(rows)

# サマリー指標
total_cost = df["cost"].sum()
total_sales = df["attributedSales14d"].sum()
total_clicks = df["clicks"].sum()
total_impressions = df["impressions"].sum()
overall_acos = round(total_cost / total_sales * 100, 1) if total_sales > 0 else None

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("広告費合計", f"¥{total_cost:,.0f}")
col2.metric("広告経由売上", f"¥{total_sales:,.0f}")
col3.metric("全体ACOS", f"{overall_acos}%" if overall_acos else "---")
col4.metric("クリック数", f"{total_clicks:,}")
col5.metric("表示回数", f"{total_impressions:,}")

st.divider()
st.subheader("キーワード別パフォーマンス")

target_acos = st.slider("目標ACOS (%)", min_value=5, max_value=80, value=25, step=5)
st.session_state["target_acos"] = target_acos

def acos_color(val):
    if val is None:
        return "background-color: #f0f0f0"
    if val > target_acos * 1.5:
        return "background-color: #ffcccc"
    if val < target_acos * 0.6:
        return "background-color: #ccffcc"
    return ""

display_cols = ["keywordText", "campaignName", "impressions", "clicks", "ctr", "cpc", "cost", "attributedSales14d", "acos"]
display_labels = {
    "keywordText": "キーワード",
    "campaignName": "キャンペーン",
    "impressions": "表示回数",
    "clicks": "クリック",
    "ctr": "CTR(%)",
    "cpc": "CPC(¥)",
    "cost": "広告費(¥)",
    "attributedSales14d": "売上(¥)",
    "acos": "ACOS(%)",
}

display_df = df[display_cols].rename(columns=display_labels)

styled = display_df.style.applymap(
    lambda v: acos_color(v) if isinstance(v, (int, float)) or v is None else "",
    subset=["ACOS(%)"],
).format({
    "広告費(¥)": "{:,.0f}",
    "売上(¥)": "{:,.0f}",
    "CTR(%)": "{:.2f}",
    "CPC(¥)": "{:.0f}",
    "ACOS(%)": lambda v: f"{v:.1f}" if v is not None else "---",
})

st.dataframe(styled, use_container_width=True, hide_index=True)

st.caption(f"🟥 ACOS > {target_acos * 1.5:.0f}% (目標の1.5倍超) | 🟩 ACOS < {target_acos * 0.6:.0f}% (目標の60%未満、拡大余地あり)")
