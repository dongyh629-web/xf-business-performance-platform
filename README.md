# Internal Business Dashboard

这是一个用于读取 Unleashed Excel 导出文件、清洗销售明细并生成内部商业分析 Dashboard 的 Streamlit 应用。

## 当前版本范围

- 读取 Excel 文件
- 自动识别 Unleashed 原始销售明细表
- 按已确认口径清洗数据
- 首页 Dashboard
- 客户分析
- 产品分析
- 数据质量中心

## 已确认业务口径

- 销售日期使用 `Required Date`
- 只统计 `Status = Completed`
- `Sub Total = 0` 的行视为赠品/样品，计入销量，不计入销售额
- 客户类型使用 Unleashed 的 `Customer Type`
- 产品组使用 Unleashed 的 `Product Group`
- 后续会补充 `Customer Code` 和 `Area`
- 第一版不做复杂权限

## 运行方式

```bash
source .venv/bin/activate
streamlit run app.py
```

然后在浏览器打开 Streamlit 显示的本地地址。
