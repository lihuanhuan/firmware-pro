from typing import TYPE_CHECKING

import storage.recovery
from trezor import ui, utils, wire
from trezor.enums import ButtonRequestType
from trezor.lvglui.i18n import gettext as _, keys as i18n_keys
from trezor.lvglui.lv_colors import lv_colors
from trezor.ui.layouts import (
    backup_with_keytag,
    confirm_action,
    show_success,
    show_warning,
)
from trezor.ui.layouts.common import button_request
from trezor.ui.layouts.lvgl.lite import backup_with_lite
from trezor.ui.layouts.lvgl.recovery import (  # noqa: F401
    continue_recovery,
    request_word,
    request_word_count,
    show_group_share_success,
    show_remaining_shares,
)

from .. import backup_types
from . import word_validity
from .recover import RecoveryAborted

if TYPE_CHECKING:
    from typing import Callable
    from trezor.enums import BackupType


async def confirm_abort(ctx: wire.GenericContext, dry_run: bool = False) -> None:
    if dry_run:
        title = _(i18n_keys.TITLE__ABORT_CHECK)
        subtitle = _(i18n_keys.SUBTITLE__ABORT_PROCESSING)
        icon = "A:/res/warning.png"
        await confirm_action(
            ctx,
            "abort_recovery",
            title,
            description=subtitle,
            icon=icon,
            br_code=ButtonRequestType.ProtectCall,
            anim_dir=0,
            primary_color=lv_colors.ONEKEY_YELLOW,
        )
    else:
        icon = "A:/res/warning.png"

        await confirm_action(
            ctx,
            "abort_recovery",
            _(i18n_keys.TITLE__ABORT_IMPORT),
            description=_(i18n_keys.SUBTITLE__ABORT_PROCESSING),
            reverse=True,
            icon=icon,
            br_code=ButtonRequestType.ProtectCall,
            anim_dir=0,
            primary_color=lv_colors.ONEKEY_YELLOW,
        )


async def request_mnemonic(
    ctx: wire.GenericContext, word_count: int, backup_type: BackupType | None
) -> str | None:
    await button_request(ctx, "mnemonic", code=ButtonRequestType.MnemonicInput)

    words: list[str] = []
    while True:
        try:
            for i in range(len(words), word_count):
                word = await request_word(
                    ctx,
                    i,
                    word_count,
                    is_slip39=backup_types.is_slip39_word_count(word_count),
                )
                words.append(word)

                try:
                    word_validity.check(backup_type, words)
                except word_validity.AlreadyAdded:
                    await show_share_already_added(ctx)
                    words.clear()
                except word_validity.IdentifierMismatch:
                    await show_identifier_mismatch(ctx)
                    words.clear()
                except word_validity.ThresholdReached:
                    await show_group_threshold_reached(ctx)
                    words.clear()
        except wire.ActionCancelled as e:
            if len(words) == 0:
                raise e
            else:
                words.pop()
                continue
        else:
            if len(words) == 0:
                return None
            break

    return " ".join(words)


async def show_dry_run_result(
    ctx: wire.GenericContext, result: bool, is_slip39: bool, mnemonics: bytes
) -> None:
    if result:
        if is_slip39:
            text = "The entered recovery\nshares are valid and\nmatch what is currently\nin the device."
            raise
        else:
            text = _(i18n_keys.SUBTITLE__DEVICE_RECOVER_CHECK_CORRECT)
        await show_success(
            ctx,
            "success_dry_recovery",
            text,
            button=_(i18n_keys.BUTTON__CONTINUE),
            header=_(i18n_keys.TITLE__CORRECT),
        )
        # if not is_slip39 and not __debug__:
        if not is_slip39:
            if utils.get_current_backup_type() == utils.BACKUP_METHOD_LITE:
                await backup_with_lite(ctx, mnemonics, recovery_check=True)
            elif utils.get_current_backup_type() == utils.BACKUP_METHOD_KEYTAG:
                await backup_with_keytag(ctx, mnemonics, recovery_check=True)

    else:
        if is_slip39:
            raise
        else:
            text = _(i18n_keys.SUBTITLE__DEVICE_RECOVER_CHECK_NOT_MATCH)
        await show_warning(
            ctx,
            "warning_dry_recovery",
            text,
            button=_(i18n_keys.BUTTON__CONTINUE),
            icon="A:/res/danger.png",
            header=_(i18n_keys.TITLE__NOT_MATCH),
        )


async def show_dry_run_different_type(ctx: wire.GenericContext) -> None:
    await show_warning(
        ctx,
        "warning_dry_recovery",
        header="Dry run failure",
        content="Seed in the device was\ncreated using another\nbackup mechanism.",
        icon=ui.ICON_CANCEL,
        icon_color=ui.ORANGE_ICON,
        br_code=ButtonRequestType.ProtectCall,
    )


async def show_invalid_mnemonic(
    ctx: wire.GenericContext, mnemonics: list[str]
) -> None | int:
    if backup_types.is_slip39_word_count(len(mnemonics)):
        pass
    else:
        from trezor.lvglui.scrs.recovery_device import InvalidMnemonic

        screen = InvalidMnemonic(mnemonics)

        return await ctx.wait(screen.request())


async def show_share_already_added(ctx: wire.GenericContext) -> None:
    await show_warning(
        ctx,
        "warning_known_share",
        "Share already entered,\nplease enter\na different share.",
    )


async def show_identifier_mismatch(ctx: wire.GenericContext) -> None:
    await show_warning(
        ctx,
        "warning_mismatched_share",
        "You have entered\na share from another\nShamir Backup.",
    )


async def show_group_threshold_reached(ctx: wire.GenericContext) -> None:
    await show_warning(
        ctx,
        "warning_group_threshold",
        "Threshold of this\ngroup has been reached.\nInput share from\ndifferent group.",
    )


async def homescreen_dialog(
    ctx: wire.GenericContext,
    button_label: str,
    text: str,
    subtext: str | None = None,
    info_func: Callable | None = None,
) -> None:
    while True:
        if await continue_recovery(ctx, button_label, text, subtext, info_func):  # 继续恢复
            # go forward in the recovery process
            break
        # user has chosen to abort, confirm the choice
        dry_run = storage.recovery.is_dry_run()
        try:
            await confirm_abort(ctx, dry_run)
        except wire.ActionCancelled:
            pass
        else:
            raise RecoveryAborted
