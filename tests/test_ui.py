from bambu_spaghetti_watch.ui import ICONS, icon_for


def test_icon_reflects_verdict():
    assert icon_for("watching", "safe") == ICONS["watching"]
    assert icon_for("idle", "safe") == ICONS["idle"]
    assert icon_for("watching", "warning") == ICONS["warning"]
    assert icon_for("watching", "failure") == ICONS["warning"]
    assert icon_for("error", "safe") == ICONS["error"]


def test_icons_are_distinct_glyphs():
    assert len(set(ICONS.values())) == len(ICONS)


def test_loop_runner_reuses_one_event_loop():
    import asyncio

    from bambu_spaghetti_watch.ui import LoopRunner

    async def loop_id():
        return id(asyncio.get_running_loop())

    runner = LoopRunner()
    try:
        ids = {runner.run(loop_id()) for _ in range(3)}
    finally:
        runner.stop()
    assert len(ids) == 1


def test_symbol_for_follows_the_same_rules_as_the_glyph():
    from bambu_spaghetti_watch.ui import SYMBOLS, symbol_for

    assert symbol_for("idle", "safe") == SYMBOLS["idle"]
    assert symbol_for("watching", "failure") == SYMBOLS["warning"]
    assert symbol_for("error", "safe") == SYMBOLS["error"]
