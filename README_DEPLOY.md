# XF 内部商业分析系统：部署说明

本文档用于把当前 Streamlit 商业分析系统从本地运行，准备迁移到线上固定网址访问。

当前阶段只建议部署为“演示版”。不要把真实 Excel、CSV、Parquet、日志、密码、API Key 或账号信息提交到 GitHub。

## 1. 当前项目的部署模式

当前项目支持两种运行模式：

- 本地持久模式：适合你自己在电脑上使用。上传 Excel 后可以保存处理后的本地 Parquet，方便下次打开继续查看。
- 云端演示模式：适合 Streamlit Community Cloud。上传 Excel 后，数据只保存在当前浏览器会话中，不写入共享数据文件。

云端演示模式是当前默认模式。本地启动脚本会自动设置为本地持久模式。

## 2. 演示版与内部正式版的区别

演示版：

- 用于验证线上页面能否打开。
- 可以测试 Excel 上传和页面交互。
- 不建议长期保存真实公司数据。
- 不适合多人正式同时使用。

内部正式版：

- 使用真实销售数据。
- 需要登录和访问控制。
- 需要数据库或受保护存储。
- 需要不同用户或不同数据集之间隔离。
- 需要备份、权限和数据保留策略。

## 3. 为什么不能提交真实数据

当前数据可能包含客户名称、Customer Code、Product Code、订单金额和经营情况。这些都属于公司商业数据。

如果把真实数据提交到 GitHub，即使仓库是私有仓库，也会增加泄露风险。若未来误设为公开仓库，数据会直接暴露。

如果敏感文件已经进入 Git 历史，仅加入 `.gitignore` 不够，需要清理 Git 历史后再推送。

## 4. `.gitignore` 保护了哪些文件

当前 `.gitignore` 会排除：

- `.env` 和 `.env.*`
- `.streamlit/secrets.toml`
- `.streamlit/credentials.toml`
- `data/` 下的真实数据
- `uploads/` 下的上传文件
- `outputs/` 下的导出文件
- Excel、CSV、Parquet、ZIP、日志文件
- Python 缓存和本地虚拟环境

允许保留的只有空目录占位文件：

- `data/.gitkeep`
- `uploads/.gitkeep`

## 5. 如何本地启动

本地使用时，双击：

`启动仪表盘.command`

或：

`run_dashboard.command`

启动后打开：

`http://localhost:8501`

本地脚本会自动启用本地持久模式。

## 6. 如何云端部署

暂时不要直接部署。准备部署时建议按以下顺序：

1. 确认 `.gitignore` 已经排除真实数据。
2. 确认 GitHub 仓库是 Private。
3. 确认没有 Excel、CSV、Parquet、日志或 secrets 文件被 Git 跟踪。
4. 把代码推送到 GitHub 私有仓库。
5. 登录 Streamlit Community Cloud。
6. 授权 Streamlit 读取指定 GitHub 仓库。
7. 创建 App，选择仓库、分支和入口文件 `app.py`。
8. 部署后打开生成的网址。
9. 上传测试 Excel，确认页面正常。

## 7. Streamlit Cloud 需要填写哪些信息

通常需要：

- Repository：你的 GitHub 私有仓库
- Branch：主分支，例如 `main`
- Main file path：`app.py`
- Python version：由 `runtime.txt` 指定为 Python 3.12

当前演示版不需要填写数据库密码或 API Key。

## 8. 如何测试 Excel 上传

部署后打开网页：

1. 在首页左侧上传 Unleashed 导出的 Excel。
2. 等待系统读取和清洗。
3. 检查首页、客户分析、产品分析和数据质量中心。
4. 切换 Date Basis，确认页面同步变化。
5. 不要把测试 Excel 提交到 GitHub。

## 9. 会话数据何时会丢失

云端演示模式下，上传数据只保存在当前浏览器会话中。

以下情况可能导致数据丢失：

- 浏览器会话结束。
- 应用重启。
- Streamlit Cloud 休眠后重新唤醒。
- 长时间不活动。

这是当前演示版的预期行为。

## 10. 为什么当前不适合多人正式使用

当前版本没有登录系统，也没有数据库。

虽然云端演示模式避免了把上传数据写入共享 Parquet，但它仍然不是完整的企业级权限系统。任何拿到访问链接的人都可能打开页面并上传自己的文件。

正式多人使用前，应增加：

- 登录保护
- 用户权限
- 数据集隔离
- 持久化存储
- 审计日志
- 数据备份

## 11. 后续正式版需要增加什么

建议正式版增加：

- 登录和权限控制
- 受保护数据库，例如 Postgres 或 Supabase
- 文件存储，例如 S3、SharePoint 或 Google Drive
- 每次导入的文件哈希和导入记录
- 数据版本管理
- 管理员数据清理功能
- 更完整的异常日志

## 12. 如何回退到本地版本

线上部署失败不会影响本地版本。

你可以继续双击：

`启动仪表盘.command`

然后打开：

`http://localhost:8501`

本地已有的 `data/processed/latest_sales.parquet` 不会因为部署准备而被删除。

## 部署前检查清单

- [ ] GitHub 仓库是 Private
- [ ] 没有 Excel/CSV/Parquet 被跟踪
- [ ] 没有 secrets 文件被跟踪
- [ ] 应用无数据时可以启动
- [ ] 上传 Excel 后页面正常
- [ ] 不同会话的数据不会覆盖
- [ ] Streamlit 应用访问权限已确认
