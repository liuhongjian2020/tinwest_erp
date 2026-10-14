\# PROJECT\_CONTEXT.md



> 最后更新：2026-10-14

> 当前阶段：核心业务闭环已跑通，正在优化和扩展

> 新对话使用方式：先把本文件完整发给 AI，再说明本次要做的任务。



\## 1. 项目概览

\- 项目名称：Tinwest ERP

\- 项目目标：外贸订单管理、采购、库存、出运及现金流预测的轻量级 ERP。

\- 代码仓库：本地项目文件夹 `tinwest\_erp`



\## 2. 技术栈

\- 后端：Python 3.10+ / FastAPI / SQLite3

\- 前端：Vue 3 (CDN 引入) + 原生 HTML/CSS (内嵌在 main.py 的 HTMLResponse 中)

\- 数据处理：Pandas（用于导入 Excel 数据）

\- 运行方式：

```bash

\# 安装依赖

pip install fastapi uvicorn pandas openpyxl

\# 启动服务

python main.py

\# 或 uvicorn main:app --reload

