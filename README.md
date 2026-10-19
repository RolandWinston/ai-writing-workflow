# AI Writing Workflow

从实际长篇修订中整理的三角色协作工作流：主笔负责改稿，文笔编辑负责表达，设定审校负责事实、人物、动作和时序。总控协调版本与交付。

我将作者反馈转成可追踪的问题，把原稿、作者手改、候选稿与审核报告按版本绑定，并制作校验工具检查报告过期、漏审、重复派工和阅读记录复用。AI 负责辅助产出与审阅，最终取舍仍由作者决定。

## 开始

Python 3.10+，只依赖标准库。在仓库根目录运行：

```shell
python -m unittest -v test_reading_reuse
python workflow_guard.py --help
python 核验三代理流程.py --help
```

- [WORKFLOW.md](WORKFLOW.md)：实际执行顺序、返修上限与交付条件。
- [AGENTS.md](AGENTS.md)：接入代理时的边界。
- [example-mapping.json](example-mapping.json)：专为公开说明编写的演示文本，不是历史稿件或审核证据。
- `workflow_guard.py`：逐段映射、阅读记录、历史删改审计及发布检查。
- `核验三代理流程.py`：核对三角色登记、候选 SHA-256、独立报告、范围和问题状态。

`workflow_guard.py` 的历史发布清单与三代理 3.0 记录使用不同结构，不能把任一通过结果当作另一种记录的通过。主流程以三代理 3.0 为准。

## 阅读记录示例

```shell
python workflow_guard.py read example-mapping.json P01 P03 --receipt read-01.json --reading-index readings.sqlite --reader prose-editor --source-key demo
python workflow_guard.py read-note read-01.json "已连读三段；人物动作与对白归属一致，待主笔提交候选。"
python workflow_guard.py read example-mapping.json P01 P03 --receipt read-02.json --reading-index readings.sqlite --reader prose-editor --source-key demo
```

第三条命令复用同岗位、同文字的既有记录。内容变化后使用 `--changed-only`，读取改动段及相邻上下文；另一个审阅岗位仍须独立读取。输出文本不自动等于读懂或通过。

本库不自动调用模型，不包含 API 密钥。结构校验不能证明真实派工、阅读质量或文笔水平；真实运行须保留实际工具记录。文风案例见 [AI Prose Review Library](https://github.com/RolandWinston/ai-prose-review-library)。[个人作品集](https://rolandwinston.github.io/)
