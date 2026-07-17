# Agent Notes

涉及麒麟远程服务器上的用户测试或浏览器调试时，先阅读并按以下 runbook 执行：

- [远程调试启动流程](docs/远程调试启动流程.md)

不要使用 `docker compose`，不要在远程服务器执行 Bun/npm 前端构建。SSH 密码、GitHub PAT、LLM API Key 等凭据只从当前用户会话或安全凭据源获取，不写入仓库文件、命令历史或日志。
