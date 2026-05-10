import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="設定", page_icon="⚙️", layout="wide")
st.title("⚙️ 設定")

st.subheader("モード選択")
demo_mode = st.toggle("デモモード (APIキーなしで動作確認)", value=st.session_state.get("demo_mode", True))
st.session_state["demo_mode"] = demo_mode

if demo_mode:
    st.success("デモモード ON: サンプルデータで全機能を試せます")
else:
    st.warning("本番モード: 実際のAmazon APIを使用します")

st.divider()

col1, col2 = st.columns(2)

with col1:
    st.subheader("🤖 AI API設定")
    ai_provider = st.selectbox(
        "AIプロバイダー",
        ["openai", "gemini"],
        format_func=lambda x: "OpenAI GPT-4o" if x == "openai" else "Google Gemini 1.5 Flash",
        index=0 if st.session_state.get("ai_provider", "openai") == "openai" else 1,
    )
    st.session_state["ai_provider"] = ai_provider

    if ai_provider == "openai":
        openai_key = st.text_input(
            "OpenAI APIキー",
            value=st.session_state.get("openai_key", os.getenv("OPENAI_API_KEY", "")),
            type="password",
            placeholder="sk-...",
        )
        st.session_state["openai_key"] = openai_key
        st.session_state["ai_api_key"] = openai_key
    else:
        gemini_key = st.text_input(
            "Google Gemini APIキー",
            value=st.session_state.get("gemini_key", os.getenv("GEMINI_API_KEY", "")),
            type="password",
            placeholder="AIzaSy...",
        )
        st.session_state["gemini_key"] = gemini_key
        st.session_state["ai_api_key"] = gemini_key

    st.caption(
        "OpenAI: platform.openai.com → API Keys\n\n"
        "Gemini: aistudio.google.com → Get API key"
    )

with col2:
    st.subheader("📦 Amazon SP-API")
    sp_client_id = st.text_input(
        "Client ID",
        value=st.session_state.get("sp_client_id", os.getenv("SP_CLIENT_ID", "")),
        type="password",
    )
    sp_client_secret = st.text_input(
        "Client Secret",
        value=st.session_state.get("sp_client_secret", os.getenv("SP_CLIENT_SECRET", "")),
        type="password",
    )
    sp_refresh_token = st.text_input(
        "Refresh Token",
        value=st.session_state.get("sp_refresh_token", os.getenv("SP_REFRESH_TOKEN", "")),
        type="password",
    )
    st.session_state.update({
        "sp_client_id": sp_client_id,
        "sp_client_secret": sp_client_secret,
        "sp_refresh_token": sp_refresh_token,
        "sp_marketplace_id": "A1VC38T7YXB528",
    })

st.divider()

st.subheader("📣 Amazon Advertising API")
col3, col4 = st.columns(2)
with col3:
    adv_client_id = st.text_input(
        "Advertising Client ID",
        value=st.session_state.get("adv_client_id", os.getenv("ADV_CLIENT_ID", "")),
        type="password",
        help="SP-APIと同じClient IDを使う場合はそのまま入力",
    )
    adv_client_secret = st.text_input(
        "Advertising Client Secret",
        value=st.session_state.get("adv_client_secret", os.getenv("ADV_CLIENT_SECRET", "")),
        type="password",
    )
with col4:
    adv_refresh_token = st.text_input(
        "Advertising Refresh Token",
        value=st.session_state.get("adv_refresh_token", os.getenv("ADV_REFRESH_TOKEN", "")),
        type="password",
    )
    adv_profile_id = st.text_input(
        "Profile ID",
        value=st.session_state.get("adv_profile_id", os.getenv("ADV_PROFILE_ID", "")),
        help="Advertising APIのプロファイルID (接続テストで自動取得も可能)",
    )

st.session_state.update({
    "adv_client_id": adv_client_id,
    "adv_client_secret": adv_client_secret,
    "adv_refresh_token": adv_refresh_token,
    "adv_profile_id": adv_profile_id,
})

st.divider()

if st.button("💾 設定を保存 & 接続テスト", type="primary"):
    if demo_mode:
        st.success("デモモードで保存しました。すべてのページが使用可能です。")
    else:
        errors = []
        if not st.session_state.get("ai_api_key"):
            errors.append("AIのAPIキーが未入力です")
        if not sp_client_id or not sp_client_secret or not sp_refresh_token:
            errors.append("SP-APIの認証情報が不足しています")

        if errors:
            for e in errors:
                st.error(e)
        else:
            with st.spinner("接続確認中..."):
                try:
                    from amazon_advertising.auth import AdvAuth, AdvCredentials
                    creds = AdvCredentials(
                        client_id=adv_client_id or sp_client_id,
                        client_secret=adv_client_secret or sp_client_secret,
                        refresh_token=adv_refresh_token or sp_refresh_token,
                    )
                    auth = AdvAuth(creds)
                    profiles = auth.get_profiles()
                    st.success(f"✅ Advertising API 接続成功！プロファイル {len(profiles)} 件取得")
                    if profiles and not adv_profile_id:
                        st.session_state["adv_profile_id"] = str(profiles[0]["profileId"])
                        st.info(f"Profile ID を自動設定しました: {profiles[0]['profileId']}")
                except Exception as e:
                    st.error(f"接続エラー: {e}")

st.divider()
with st.expander("APIキーの取得方法"):
    st.markdown("""
**Amazon SP-API (Seller Central)**
1. Seller Central にログイン
2. アプリとサービス → アプリの開発 → 新しいアプリを作成
3. Client ID / Client Secret / Refresh Token を取得

**Amazon Advertising API**
1. advertising.amazon.co.jp → API アクセスをリクエスト
2. Client ID は SP-API と共用可能な場合あり
3. Refresh Token は Advertising 用に別途取得が必要

**OpenAI API**
- platform.openai.com → API Keys

**Google Gemini API**
- aistudio.google.com → Get API key (無料枠あり)
""")
