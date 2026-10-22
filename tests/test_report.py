from api_change_agent.report import Row, render_table


def test_table_links_and_escaping():
    rows = [
        Row("app/x.py", 3, "client.beta.chat.completions.parse", "client.chat.completions.parse",
            "beta-chat-completions-to-ga", "https://example.com/cl"),
        Row("app/y.py", 9, "a | b <c>", "line1\nline2", "functions-to-tools", "https://example.com/t"),
    ]
    table = render_table(rows, blob_base="https://github.com/acme/app/blob/api-change-agent/openai")
    lines = table.splitlines()
    assert lines[0] == "| Location | Before → After | Rule | Changelog |"
    assert lines[2] == (
        "| [app/x.py:3](https://github.com/acme/app/blob/api-change-agent/openai/app/x.py#L3) "
        "| <code>client.beta.chat.completions.parse</code> → <code>client.chat.completions.parse</code> "
        "| `beta-chat-completions-to-ga` | [changelog](https://example.com/cl) |"
    )
    assert "<code>a &#124; b &lt;c&gt;</code>" in lines[3]
    assert "<code>line1<br>line2</code>" in lines[3]


def test_table_without_links():
    rows = [Row("a.py", 1, "x", "y", "r", "https://e.com")]
    assert "| a.py:1 |" in render_table(rows, blob_base=None)


def test_long_cells_truncated():
    rows = [Row("a.py", 1, "\n".join(f"l{i}" for i in range(20)), "y", "r", "https://e.com")]
    assert "…" in render_table(rows, blob_base=None)
