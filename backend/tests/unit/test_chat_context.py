from datetime import timedelta

from ai_platform.services.chat_context import Context, cost, message


def test_clip_complete_turns_to_both_caps_and_preserve_fixed_input():
    history = [
        {"role": role, "text": text, "sequence": i}
        for i, (role, text) in enumerate(
            [
                ("user", "old"),
                ("assistant", "answer"),
                ("user", "recent"),
                ("assistant", "fact"),
                ("user", "latest"),
                ("assistant", "ok"),
            ],
            1,
        )
    ]
    ctx = Context(
        message("system", "policy"),
        message("user", "current"),
        history,
        {"id": "summary", "summary_text": "x" * 70},
        budget=60,
        window=220,
        output=40,
        count=2,
        threshold=80,
    )
    assert ctx.summary_request(timedelta(seconds=119)) is None
    messages, mode, plan = ctx.reply()
    assert messages == [ctx.system, message("user", "latest"), message("assistant", "ok"), ctx.current]
    assert sum(map(cost, messages[1:-1])) <= ctx.budget and len(messages[1:-1]) <= ctx.count
    assert mode == "truncated" and plan["summary_id"] is None and plan["first_sequence"] == 5
    ctx.budget = 10
    assert ctx.reply()[0] == [ctx.system, ctx.current]


def test_token_threshold_uses_history_budget_at_equality():
    history = [
        {"sequence": 1, "role": "user", "text": "x" * 48},
        {"sequence": 2, "role": "assistant", "text": "y" * 16},
    ]
    ctx = Context(
        message("system", "fixed"),
        message("user", "now"),
        history,
        None,
        budget=101,
        window=512,
        output=32,
        count=20,
        threshold=80,
    )
    assert ctx.summary_request(timedelta(seconds=130)) is None
    ctx.budget = 100
    prompt, output, watermark = ctx.summary_request(timedelta(seconds=120))
    assert prompt[1:] == ctx.historical() and watermark == 2 and output <= 512
    assert sum(map(cost, prompt)) + output + 16 <= ctx.window
