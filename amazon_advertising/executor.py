"""承認済み提案をAmazon APIへ反映するエグゼキューター"""
from __future__ import annotations

from .ai_agent import Proposal, ProposalType
from .campaigns import CampaignsAPI


def execute_proposal(proposal: Proposal, campaigns_api: CampaignsAPI) -> str:
    """提案を実行し結果メッセージを返す"""
    pt = proposal.proposal_type

    if pt in (ProposalType.LOWER_BID, ProposalType.RAISE_BID):
        campaigns_api.update_keyword_bid(proposal.target_id, float(proposal.suggested_value))
        return f"✅ [{proposal.target_name}] 入札額を ¥{proposal.current_value} → ¥{proposal.suggested_value} に変更しました"

    elif pt == ProposalType.PAUSE_KEYWORD:
        campaigns_api.pause_keyword(proposal.target_id)
        return f"⏸️ [{proposal.target_name}] キーワードを停止しました"

    elif pt == ProposalType.ADJUST_BUDGET:
        campaigns_api.update_campaign_budget(proposal.target_id, float(proposal.suggested_value))
        return f"✅ [{proposal.target_name}] 予算を ¥{proposal.current_value} → ¥{proposal.suggested_value} に変更しました"

    else:
        return f"ℹ️ [{proposal.target_name}] リスティング変更は手動で行ってください（提案内容をコピーして使用してください）"


def execute_listing_proposal(proposal: Proposal, sp_auth) -> str:
    """リスティング改善提案を実行する（SP-API Listings API）"""
    from amazon_sp_api.listings import ListingsAPI

    api = ListingsAPI(sp_auth)
    pt = proposal.proposal_type

    if pt == ProposalType.LISTING_TITLE:
        api.update_title(proposal.target_id, str(proposal.suggested_value))
        return f"✅ [{proposal.target_name}] タイトルを更新しました"

    elif pt == ProposalType.LISTING_BULLETS:
        bullets = [b.strip() for b in str(proposal.suggested_value).split("/") if b.strip()]
        api.update_bullet_points(proposal.target_id, bullets)
        return f"✅ [{proposal.target_name}] 箇条書きを更新しました"

    elif pt == ProposalType.LISTING_DESCRIPTION:
        api.update_description(proposal.target_id, str(proposal.suggested_value))
        return f"✅ [{proposal.target_name}] 説明文を更新しました"

    else:
        return f"ℹ️ [{proposal.target_name}] 画像変更はSellerCentralから手動で追加してください"
