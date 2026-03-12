"""自定义 memory middleware：每轮调用前都重新加载 memory 文件。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from deepagents.middleware.memory import MemoryMiddleware, MemoryState, MemoryStateUpdate

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from langgraph.runtime import Runtime


class ReloadingMemoryMiddleware(MemoryMiddleware):
    """覆盖 deepagents 默认行为，确保每轮都重新加载 memory 文件。"""

    def before_agent(  # type: ignore[override]
        self,
        state: MemoryState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> MemoryStateUpdate:
        backend = self._get_backend(state, runtime, config)
        contents: dict[str, str] = {}

        results = backend.download_files(list(self.sources))
        for path, response in zip(self.sources, results, strict=True):
            if response.error is not None:
                if response.error == "file_not_found":
                    continue
                msg = f"Failed to download {path}: {response.error}"
                raise ValueError(msg)
            if response.content is not None:
                contents[path] = response.content.decode("utf-8")

        return MemoryStateUpdate(memory_contents=contents)

    async def abefore_agent(  # type: ignore[override]
        self,
        state: MemoryState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> MemoryStateUpdate:
        backend = self._get_backend(state, runtime, config)
        contents: dict[str, str] = {}

        results = await backend.adownload_files(list(self.sources))
        for path, response in zip(self.sources, results, strict=True):
            if response.error is not None:
                if response.error == "file_not_found":
                    continue
                msg = f"Failed to download {path}: {response.error}"
                raise ValueError(msg)
            if response.content is not None:
                contents[path] = response.content.decode("utf-8")

        return MemoryStateUpdate(memory_contents=contents)
