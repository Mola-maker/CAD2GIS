# 设计与组件来源

## 实际使用
- [Pydantic AI](https://github.com/pydantic/pydantic-ai)：MIT；使用 pydantic-ai-slim 2.42.0 的 Agent、OpenAI 兼容适配和工具循环。
- [Framework7](https://github.com/framework7io/framework7)：MIT；使用 9.1.3 的本地 CSS / JavaScript，实现 iOS 风格分页和弹窗。许可证随软件保留。
- [Figma 设计文件](https://www.figma.com/design/gjcYZ4DDk11piCtxr3XE3y)：molamaker · CAD2GIS — Blue Atelier；设计复用 Figma Simple Design System 的组件实例。
- 图标根据用户提供的 molamaker 蓝色角色图生成，背景为同色系原创生成图；不包含 Apple 标志或系统字体文件。

## 参考与取舍
- [Apple 动效指南](https://developer.apple.com/design/human-interface-guidelines/motion)：用于持续时间、过渡和减少动态效果的设计参考。
- [Apple macOS 26 UI Kit](https://www.figma.com/community/file/1543337041090580818/macos-26)：仅作布局与视觉参考，不随包分发受限素材。
- [Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness)：调研其完整工作流思路；当前实际集成为轻量核心 Agent 加现有项目 MCP，并非安装该独立 Harness 包。

调研使用 GitHub、Exa、Tavily 与 Figma。选择 Python 智能体核心是为了复用现有转换工具和打包运行时，避免增加第二个 Node 服务进程。界面中文字体优先 Noto Sans SC，Windows 使用可用的系统字体回退。
