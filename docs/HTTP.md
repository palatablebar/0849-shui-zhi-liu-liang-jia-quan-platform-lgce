# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿（同一来源幂等，返回同一 `run_id`）。
- `GET /api/runs`：最近批次。
- `GET /api/runs/{id}`：读取批次，含当前效力字段（`status`、`effective`、`valid`、`withdrawn`、`validity`）与 `withdrawal_reason`。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`；也接受 `decision=WITHDRAWN/REVOKED/INVALID` 撤回。
- `GET /api/runs/{id}/evidence`：下载已签发证据。批次撤回后返回 `409`，旧链接停止交付。
- `POST /api/samples/{sample_id}/invalidate`：抽检确认水样无效（body 可带 `reason`）。幂等，重试不新增记录；所有计算中用到该水样的批次立即失效，未用到的批次照常有效。
- `POST /api/withdrawals`：同上，body 传 `sample_id`、`reason`；也支持 `run_id` 直接撤回批次。
- `GET /api/withdrawals` / `GET /api/invalid-samples`：抽检无效与撤回记录。
- `POST /api/evidence/verify`、`POST /api/runs/{id}/verify`：复核查验原签发证据与 `artifact_sha256` 校验值；撤回后仍可验证原证据完整性，响应同时给出 `effective=false`。
- `GET /api/runs/{id}/evidence?verify=1`（或 `?checksum=`）：复核视图，返回原证据与校验结果。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。

## 效力与并发约定

- 批次效力与签发状态分离表达：撤回后 `status=WITHDRAWN`，同时 `effective=false`、`valid=false`、`withdrawn=true`，并给出各命名风格的别名字段。
- 撤回与签发在 SQLite `BEGIN IMMEDIATE` 事务内串行裁决：先发的签发会被随后的撤回标记失效；先发的撤回会让后到的签发被 `GATE_BLOCKED` 拒绝。无论提交顺序，含无效水样的批次最终都无效。
- `POST /api/runs` 按 `run_key` 幂等，重跑返回同一批次，撤回状态不会被清空；撤回后的批次再次签发一律拒绝。
