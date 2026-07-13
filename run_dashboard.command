#!/bin/bash
cd "$(dirname "$0")"

echo "正在启动 XF 内部商业分析系统..."
echo "项目位置：$(pwd)"
echo ""

export HOME="$PWD"
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
export XF_STORAGE_MODE=persistent

if [ ! -f ".venv/bin/activate" ]; then
  echo "启动失败：找不到 Python 环境 .venv。"
  echo "请把这个窗口截图发给 Codex。"
  read -n 1 -s -r -p "按任意键关闭窗口..."
  exit 1
fi

source .venv/bin/activate

echo "如果启动成功，请打开："
echo "http://localhost:8501"
echo ""
echo "如果这个窗口显示报错，请截图发给 Codex。"
echo ""

python -m streamlit run app.py \
  --server.address 127.0.0.1 \
  --server.port 8501 \
  --server.headless true \
  --browser.gatherUsageStats false

echo ""
echo "Streamlit 已停止或启动失败。"
echo "请把上方错误信息截图发给 Codex。"
read -n 1 -s -r -p "按任意键关闭窗口..."
