# Summary Service

异步文档摘要微服务。API 接收不超过 256 KiB 的 UTF-8 文本并立即返回任务 token；单 Pod 内 5 个 worker 使用 Pydantic AI 调用阿里云百炼 `qwen3.7-plus`，结果最长 400 个 Unicode 字符。

## API

提交任务：

```bash
curl -X POST https://summary.services.whale-smart.com/v1/summaries \
  -H "X-API-Key: $SUMMARY_SERVICE_API_KEY" \
  -H "Idempotency-Key: request-001" \
  -H "Content-Type: application/json" \
  -d '{"text":"需要摘要的正文"}'
```

返回 HTTP 202：

```json
{"token":"...","status":"queued","submitted_at":"2026-07-15T09:00:00Z"}
```

查询任务：

```bash
curl https://summary.services.whale-smart.com/v1/summaries/$TOKEN \
  -H "X-API-Key: $SUMMARY_SERVICE_API_KEY"
```

状态为 `queued`、`running`、`succeeded` 或 `failed`；清理后的 tombstone 返回 410。未知 token 和其他调用方的 token 返回 404。队列 100 个活跃任务时返回 429；文本超限返回 413。

## 配置

所有环境变量使用 `SUMMARY_` 前缀：

- `SUMMARY_DASHSCOPE_API_KEY`：阿里云百炼密钥。
- `SUMMARY_API_KEYS`：逗号分隔的 `client_id:sha256(api-key)`，只允许 hash。
- `SUMMARY_IDEMPOTENCY_SECRET`：至少 32 字符，用于派生幂等任务 token。
- `SUMMARY_DATABASE_PATH`：默认 `/data/summary.db`。
- `SUMMARY_SYSTEM_PROMPT`、`SUMMARY_TASK_PROMPT`：提示词；任务模板必须且只能包含一个 `{{ text }}`。
- `SUMMARY_MODEL_NAME`：默认 `qwen3.7-plus`。
- `SUMMARY_LLM_BASE_URL`：默认阿里云百炼 OpenAI 兼容地址。

API key 轮换时把新旧两项同时放入 `SUMMARY_API_KEYS`，调用方完成切换后删除旧 hash。不得在 Git、镜像或日志中保存明文密钥。

## 开发与测试

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
bash tests/test_container.sh
```

本地启动前先设置三个必需的 `SUMMARY_*` 密钥变量，然后执行：

```bash
summary-service migrate
summary-service api
summary-service worker
```

## 运维边界

- 单个 Pod 的 API、worker 两个容器共享 SQLite PVC；SQLite WAL、短事务和租约恢复保证单 Pod 稳定运行。
- 成功任务保留 2 小时，失败任务保留 24 小时，expired tombstone 再保留 24 小时。
- 当前部署不可水平扩容。需要多副本时迁移到外部队列与服务端数据库，再接入 Keycloak Client Credentials。
- 备份必须通过 SQLite online backup 生成一致性快照，不能直接复制活动的 WAL 文件。
