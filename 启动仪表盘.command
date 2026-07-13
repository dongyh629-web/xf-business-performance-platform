#!/bin/bash
cd "$(dirname "$0")"

LOG_FILE="streamlit_startup.log"
URL="http://localhost:8501"

echo "正在启动 XF 内部商业分析系统..."
echo "项目位置：$(pwd)"
echo "启动日志：$(pwd)/$LOG_FILE"
echo ""

export HOME="$PWD"
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
export XF_STORAGE_MODE=persistent

if [ ! -x ".venv/bin/python" ]; then
  echo "启动失败：找不到项目自带的 Python 环境。" | tee "$LOG_FILE"
  echo "请把这个窗口截图发给 Codex。"
  read -n 1 -s -r -p "按任意键关闭窗口..."
  exit 1
fi

if [ ! -f "app.py" ]; then
  echo "启动失败：找不到 app.py。" | tee "$LOG_FILE"
  echo "请把这个窗口截图发给 Codex。"
  read -n 1 -s -r -p "按任意键关闭窗口..."
  exit 1
fi

echo "启动中，请稍等..."
echo "如果成功，浏览器会自动打开：$URL"
echo ""

(
  sleep 4
  open "$URL"
) &

".venv/bin/python" -m streamlit run app.py \
  --server.address 127.0.0.1 \
  --server.port 8501 \
  --server.headless true \
  --browser.gatherUsageStats false \
  2>&1 | tee "$LOG_FILE"

echo ""
echo "Streamlit 已停止或启动失败。"
echo "请把上方错误信息，或 streamlit_startup.log 文件内容发给 Codex。"
read -n 1 -s -r -p "按任意键关闭窗口..."
