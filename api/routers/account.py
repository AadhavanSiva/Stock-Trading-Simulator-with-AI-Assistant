"""The account itself, and the leaderboard it may choose to appear on."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response

from api import schemas
from api.security import CurrentUser, current_user
from portfolio_tracker import operations
from portfolio_tracker.models import users

router = APIRouter(prefix="/api", tags=["account"])


@router.get("/leaderboard", response_model=schemas.LeaderboardOut)
def get_leaderboard(user: CurrentUser = Depends(current_user)):
    """Ranked by account value, among those who asked to be listed.

    The reader's own standing is included whether or not they opted in:
    "where would I come" is the question that makes joining an informed
    choice.
    """
    return schemas.LeaderboardOut(
        rows=[schemas.LeaderboardRow(**row._asdict())
              for row in operations.leaderboard_standings(user_id=user.id)],
        standing=schemas.standing_out(operations.my_standing(user.id)),
        starting_cash=users.starting_cash(),
    )


@router.get("/account", response_model=schemas.AccountOut)
def get_account(user: CurrentUser = Depends(current_user)):
    opted_in, leaderboard_name = users.get_leaderboard_settings(user.id)
    return schemas.AccountOut(
        email=user.email,
        display_name=user.display_name or user.email,
        cash=user.cash,
        starting_cash=users.starting_cash(),
        trade_count=operations.trade_history(user.id, page=1, page_size=1).total,
        delete_confirmation=operations.DELETE_CONFIRMATION,
        leaderboard_opt_in=opted_in,
        leaderboard_name=leaderboard_name,
        leaderboard_name_max=operations.LEADERBOARD_NAME_MAX,
        standing=schemas.standing_out(operations.my_standing(user.id)),
    )


@router.post("/account/leaderboard", response_model=schemas.LeaderboardSettingOut)
def set_leaderboard(body: schemas.LeaderboardSetting,
                    user: CurrentUser = Depends(current_user)):
    """Join or leave the leaderboard."""
    operations.set_leaderboard_participation(user.id, body.opt_in, name=body.name)
    opted_in, name = users.get_leaderboard_settings(user.id)
    return schemas.LeaderboardSettingOut(
        opt_in=opted_in, name=name,
        standing=schemas.standing_out(operations.my_standing(user.id)),
        message=("You are on the leaderboard now." if opted_in
                 else "You have been taken off the leaderboard."),
    )


@router.get("/account/export")
def export_account(user: CurrentUser = Depends(current_user)):
    """The account's own data as a JSON file to keep.

    Served as a download, and already no-store like every API response:
    it is someone's holdings and trades.
    """
    payload = operations.export_account(user.id)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        json.dumps(payload, indent=2) + "\n",
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="portfolio-tracker-export-{stamp}.json"'},
    )


@router.post("/account/delete", response_model=schemas.AccountDeleted)
def delete_account(body: schemas.DeleteAccount, user: CurrentUser = Depends(current_user)):
    """Erase the account, once the confirmation has been typed out.

    operations.delete_account re-checks the typed word, so the guard does
    not depend on the UI. Every token for the account stops working at
    once, because the account they name is gone.
    """
    return schemas.AccountDeleted(email=operations.delete_account(user.id, body.confirmation))
