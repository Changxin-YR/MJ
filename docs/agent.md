# Director Agent

LangGraph 主图包含身份验证、项目 Scope、意图理解、结构化上下文、语义检索、上下文包、计划、审批、工具执行、任务等待、检查、诊断、人工审核、正式结果、记忆更新和审计节点。Redis Stack 保存 checkpoint，序列化禁用 pickle 回退与动态模块白名单。

Tool Gateway 只登记 `load_project_context`、`retrieve_semantic_context`、`load_generation_job`、`generate_shot`。每个工具有输入契约、权限、风险级别、幂等与确认标记。调用参数不得指定 `project_id`、`workspace_id`、`user_id`、`role` 来覆盖服务端 Scope。未获批准不能执行生成，撤销会话后不能 resume。

计划节点根据 API 的显式 `intent` 构造确定性动作计划。`ANALYZE` 在 `DIRECTOR_LLM_MODE=dashscope` 时调用 Qwen Plus，对结构化信息和标记为不可信的检索文本做只读摘要；结构化上下文包含角色正式版本的背景与 DNA，RAG 对中文连续对白额外保留邻接叙事和发言轮次。分析提示要求只能依据相邻上下文解析中文省略主语，来源不足时必须明确说明无法判断。真实运行证据在 `evidence/final-demo/director-llm.json`，三道事实问答的评估见 `evidence/agent-eval/open-quality.json`。生成动作仍需人工批准。Worker 完成媒体后运行结构化 Inspector：真实图像使用当前图像，真实视频抽取开头、约 1/3、2/3 和末尾四个时间点交 Qwen VL 联合检查；声音与 FakeProvider 输出明确标记技术检查范围。对白音频内容本身以及跨镜头连续性仍需人工完整复核。

`app/mcp/server.py` 提供 stdio MCP 适配器，仅导出只读工具。进程访问令牌必须与 Director Run 的用户和服务端 Session 一致，随后仍调用 Tool Gateway。长期记忆更新仍待正式审批闭环完善。
