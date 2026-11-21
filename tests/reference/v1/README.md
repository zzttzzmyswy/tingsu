# v1 回归参考

这里保存迁移前的 Python 实现及其单元测试，供生成历史测试数据和对照两版行为使用。v2 桌面入口位于 [cmd/tingsu](../../../cmd/tingsu/)，发行包只构建 Go 后端。

历史单元测试可在这个目录中使用独立 Python 环境运行。

```sh
python3 -m pip install -r requirements.txt
PYTHONPATH=. python3 -m unittest discover -s tests
```
