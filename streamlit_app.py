import os
import io
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Amazon 経費・伝票 自動化ツール",
    page_icon="📦",
    layout="wide",
)

st.title("📦 Amazon マーケットプレイス 伝票・経費 自動化")

# ── サイドバー: 設定 ────────────────────────────────────────────
with st.sidebar:
    st.header("設定")

    demo_mode = st.toggle("デモモード (API なしで動作確認)", value=True)

    if not demo_mode:
        st.subheader("SP-API 認証情報")
        client_id = st.text_input(
            "Client ID", value=os.getenv("SP_CLIENT_ID", ""), type="password"
        )
        client_secret = st.text_input(
            "Client Secret", value=os.getenv("SP_CLIENT_SECRET", ""), type="password"
        )
        refresh_token = st.text_input(
            "Refresh Token", value=os.getenv("SP_REFRESH_TOKEN", ""), type="password"
        )
        marketplace_id = st.text_input(
            "マーケットプレイスID",
            value=os.getenv("SP_MARKETPLACE_ID", "A1VC38T7YXB528"),
            help="Amazon.co.jp = A1VC38T7YXB528",
        )
    else:
        st.info("デモモード: サンプルデータを使用中\n\nSP-API 申請完了後にオフにしてください")

    st.divider()
    st.subheader("期間指定")
    col1, col2 = st.columns(2)
    with col1:
        date_from = st.date_input("開始日", value=datetime.now() - timedelta(days=30))
    with col2:
        date_to = st.date_input("終了日", value=datetime.now())

    fetch_btn = st.button("注文データを取得", type="primary", use_container_width=True)


# ── メイン: 注文一覧 ────────────────────────────────────────────
def load_orders(demo: bool, **kwargs) -> list[dict]:
    if demo:
        from amazon_sp_api.demo_data import get_demo_orders
        return get_demo_orders()

    from amazon_sp_api.auth import SPAPIAuth, SPAPICredentials
    from amazon_sp_api.orders import OrdersAPI

    creds = SPAPICredentials(
        client_id=kwargs["client_id"],
        client_secret=kwargs["client_secret"],
        refresh_token=kwargs["refresh_token"],
        marketplace_id=kwargs["marketplace_id"],
    )
    auth = SPAPIAuth(creds)
    api = OrdersAPI(auth)
    return api.get_orders(
        created_after=datetime.combine(kwargs["date_from"], datetime.min.time()),
        created_before=datetime.combine(kwargs["date_to"], datetime.max.time()),
    )


def load_items(order_id: str, demo: bool, auth=None) -> list[dict]:
    if demo:
        from amazon_sp_api.demo_data import get_demo_order_items
        return get_demo_order_items(order_id)

    from amazon_sp_api.orders import OrdersAPI
    return OrdersAPI(auth).get_order_items(order_id)


def orders_to_df(orders: list[dict]) -> pd.DataFrame:
    rows = []
    for o in orders:
        total = o.get("OrderTotal", {})
        addr = o.get("ShippingAddress", {})
        rows.append(
            {
                "注文ID": o.get("AmazonOrderId", ""),
                "注文日": o.get("PurchaseDate", "")[:10],
                "ステータス": o.get("OrderStatus", ""),
                "配送方式": o.get("FulfillmentChannel", ""),
                "合計金額 (円)": total.get("Amount", ""),
                "配送先 都道府県": addr.get("StateOrRegion", ""),
                "配送先 市区町村": addr.get("City", ""),
            }
        )
    return pd.DataFrame(rows)


if "orders" not in st.session_state:
    st.session_state.orders = []

if fetch_btn:
    with st.spinner("注文データを取得中..."):
        try:
            kwargs = {"date_from": date_from, "date_to": date_to}
            if not demo_mode:
                kwargs.update(
                    {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                        "marketplace_id": marketplace_id,
                    }
                )
            st.session_state.orders = load_orders(demo_mode, **kwargs)
            st.success(f"{len(st.session_state.orders)} 件の注文を取得しました")
        except Exception as e:
            st.error(f"取得エラー: {e}")

orders = st.session_state.orders

if orders:
    df = orders_to_df(orders)

    tab1, tab2, tab3 = st.tabs(["注文一覧", "伝票ダウンロード", "CSV エクスポート"])

    # ── タブ1: 注文一覧 ──
    with tab1:
        st.subheader(f"注文一覧 ({len(orders)} 件)")
        st.dataframe(df, use_container_width=True, hide_index=True)

        col_a, col_b, col_c = st.columns(3)
        total_sales = df["合計金額 (円)"].apply(pd.to_numeric, errors="coerce").sum()
        col_a.metric("合計売上", f"¥{total_sales:,.0f}")
        col_b.metric("注文件数", len(df))
        col_c.metric(
            "平均注文額",
            f"¥{(total_sales / len(df)):,.0f}" if len(df) > 0 else "¥0",
        )

    # ── タブ2: 伝票ダウンロード ──
    with tab2:
        st.subheader("配送伝票・請求書のダウンロード")

        if demo_mode:
            st.warning(
                "デモモード中は実際のPDFは取得できません。"
                "SP-API 申請完了後にデモモードをオフにしてください。"
            )

        order_id_input = st.selectbox(
            "注文IDを選択",
            options=df["注文ID"].tolist(),
        )

        col_d1, col_d2 = st.columns(2)

        with col_d1:
            st.markdown("**Easy Ship 配送伝票**")
            if st.button("配送伝票を取得", disabled=demo_mode):
                with st.spinner("取得中..."):
                    try:
                        from amazon_sp_api.auth import SPAPIAuth, SPAPICredentials
                        from amazon_sp_api.invoices import InvoicesAPI

                        creds = SPAPICredentials(
                            client_id=client_id,
                            client_secret=client_secret,
                            refresh_token=refresh_token,
                            marketplace_id=marketplace_id,
                        )
                        api = InvoicesAPI(SPAPIAuth(creds))
                        pdf = api.get_easy_ship_documents(order_id_input)
                        if pdf:
                            st.download_button(
                                "PDFをダウンロード",
                                data=pdf,
                                file_name=f"label_{order_id_input}.pdf",
                                mime="application/pdf",
                            )
                        else:
                            st.info("この注文の Easy Ship 伝票はありません")
                    except Exception as e:
                        st.error(str(e))

        with col_d2:
            st.markdown("**請求書 (インボイス)**")
            if st.button("請求書を取得", disabled=demo_mode):
                with st.spinner("取得中..."):
                    try:
                        from amazon_sp_api.auth import SPAPIAuth, SPAPICredentials
                        from amazon_sp_api.invoices import InvoicesAPI

                        creds = SPAPICredentials(
                            client_id=client_id,
                            client_secret=client_secret,
                            refresh_token=refresh_token,
                            marketplace_id=marketplace_id,
                        )
                        api = InvoicesAPI(SPAPIAuth(creds))
                        pdf = api.get_invoice_document(order_id_input)
                        if pdf:
                            st.download_button(
                                "請求書PDFをダウンロード",
                                data=pdf,
                                file_name=f"invoice_{order_id_input}.pdf",
                                mime="application/pdf",
                            )
                        else:
                            st.info("この注文の請求書はありません")
                    except Exception as e:
                        st.error(str(e))

    # ── タブ3: CSV エクスポート ──
    with tab3:
        st.subheader("経費データ CSV エクスポート")

        expense_df = df.copy()
        expense_df["税抜金額 (円)"] = (
            expense_df["合計金額 (円)"]
            .apply(pd.to_numeric, errors="coerce")
            .div(1.1)
            .round(0)
            .astype(int)
        )
        expense_df["消費税 (円)"] = (
            expense_df["合計金額 (円)"]
            .apply(pd.to_numeric, errors="coerce")
            .mul(0.1 / 1.1)
            .round(0)
            .astype(int)
        )
        expense_df["勘定科目"] = "売上高"
        expense_df["摘要"] = "Amazon マーケットプレイス売上"

        st.dataframe(expense_df, use_container_width=True, hide_index=True)

        csv_buf = io.StringIO()
        expense_df.to_csv(csv_buf, index=False, encoding="utf-8-sig")

        st.download_button(
            label="CSV をダウンロード (freee / 弥生 対応)",
            data=csv_buf.getvalue().encode("utf-8-sig"),
            file_name=f"amazon_expenses_{date_from}_{date_to}.csv",
            mime="text/csv",
        )

        excel_buf = io.BytesIO()
        with pd.ExcelWriter(excel_buf, engine="openpyxl") as writer:
            expense_df.to_excel(writer, index=False, sheet_name="Amazon経費")
        excel_buf.seek(0)

        st.download_button(
            label="Excel をダウンロード",
            data=excel_buf,
            file_name=f"amazon_expenses_{date_from}_{date_to}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

else:
    st.info("左側のサイドバーから「注文データを取得」ボタンを押してください。")

    with st.expander("使い方"):
        st.markdown(
            """
### 使い方

1. **デモモード** (デフォルト ON)
   - SP-API のキーなしでサンプルデータを確認できます
   - SP-API 申請中でもUIの動作確認が可能です

2. **本番モード** (デモモードをオフ)
   - Amazon Seller Central → アプリとサービス → アプリの開発 で取得した以下の情報を入力:
     - `Client ID`
     - `Client Secret`
     - `Refresh Token`
   - マーケットプレイスID は Amazon.co.jp = `A1VC38T7YXB528`

3. **伝票ダウンロード**
   - Easy Ship: Amazon の配送サービスで出荷した際の配送伝票 PDF
   - 請求書: 各注文の請求書 PDF

4. **CSVエクスポート**
   - freee / 弥生会計 に取り込める CSV 形式で出力
   - Excel形式でも出力可能
"""
        )
