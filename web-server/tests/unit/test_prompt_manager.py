import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


class TestPromptManager:
    def test_override_single_section_from_file(self):
        from src.services.prompt_manager import PromptManager

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("## identity\nCustom agent identity.")
            f.flush()
            pm = PromptManager(prompt_path=Path(f.name))
            prompt = pm.build_system_prompt()
            assert "Custom agent identity." in prompt
            assert "AI 运维 Agent" not in prompt

    def test_override_partial_sections(self):
        from src.services.prompt_manager import PromptManager

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("## identity\nCustom identity.\n\n## rules\nCustom rules.")
            f.flush()
            pm = PromptManager(prompt_path=Path(f.name))
            prompt = pm.build_system_prompt()
            assert "Custom identity." in prompt
            assert "Custom rules." in prompt
            assert "工具调用规范" in prompt

    def test_environment_section_is_dynamic(self):
        from src.services.prompt_manager import PromptManager

        pm = PromptManager()
        pm.set_environment({"target_host": "host-a"})
        prompt1 = pm.build_system_prompt()
        assert "host-a" in prompt1

        pm.set_environment({"target_host": "host-b"})
        prompt2 = pm.build_system_prompt()
        assert "host-b" in prompt2
        assert "host-a" not in prompt2

    def test_memory_section_is_dynamic(self):
        from src.services.prompt_manager import PromptManager

        pm = PromptManager()
        pm.set_memory("记住：上次重启了 nginx")
        prompt = pm.build_system_prompt()
        assert "记住：上次重启了 nginx" in prompt
