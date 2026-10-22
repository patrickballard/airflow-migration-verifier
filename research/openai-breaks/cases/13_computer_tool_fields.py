from openai.types.responses.computer_tool import ComputerTool
t = ComputerTool.model_validate({"type": "computer_use_preview", "display_width": 1024, "display_height": 768, "environment": "browser"})
print("display_width:", t.display_width)
