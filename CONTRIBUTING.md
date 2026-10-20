# Contributing

欢迎修复问题、改进文档和补充数据库兼容性。请先在 Issue 中说明较大的设计变化。

## 本地检查

使用 Windows 与 Python 3.14，安装 `requirements-lock.txt`，退出 Pure Writer 后运行：

```powershell
$env:PW_RUN_PROTOCOL_TEST='1'
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

测试使用临时目录中的合成数据库，不依赖开发者的小说。修改写入逻辑时，应覆盖冲突失败不落库、备份、历史、重试和回退行为。

## 提交范围

- 保留默认预览、版本校验和应用状态检查。不要引入自动强关客户端或无条件覆盖。
- 适配新的原生 schema 时说明版本和验证方式；不要迁移用户的原生表结构。
- 不提交真实稿件、数据库、备份、客户端配置、访问令牌或个人机器上的日志。
- 用合成示例复现问题。截图和错误输出发布前先检查是否包含稿件、书名或本地个人路径。
- PR 说明具体触发条件、行为变化和已执行的测试；区分数据库验证与实际客户端/云同步验证。

贡献代码和文档以本项目 MIT 许可证发布。
