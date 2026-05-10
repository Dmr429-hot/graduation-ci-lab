# graduation-ci-lab

本项目用于毕业设计中的开源软件包自动构建测试、失败日志提取与失败原因分类。

系统可以根据输入的软件包名称和上游仓库地址，自动完成源码克隆、构建方式识别、构建测试执行、失败日志提取和失败原因分类，并最终生成三个结果表：成功表、失败表和暂不构建表。

---

## 1. 项目功能

本项目主要实现以下功能：

1. 批量读取待测试软件包列表；
2. 自动克隆上游开源仓库；
3. 自动识别软件包构建方式；
4. 根据构建方式执行自动构建和测试；
5. 构建或测试失败时，自动提取关键失败日志片段；
6. 根据规则对失败原因进行分类；
7. 输出最终结果到 `results/` 目录下的 CSV 文件。

---

## 2. 目录结构

```text
graduation-ci-lab
├── auto_one.sh              # 单个软件包测试入口
├── batch_run.py             # 批量测试入口
├── install_base_deps.sh     # 安装基础构建工具
├── data/
│   └── packages.csv         # 待测试软件包列表
├── sources/
│   └── 软件包源码目录        # 自动克隆的上游源码，不建议上传 Git
├── results/
│   ├── suc.csv              # 构建测试成功的软件包
│   ├── fail.csv             # 构建测试失败的软件包
│   └── other.csv            # 暂不构建的软件包
└── tools/
    ├── detect_build_system.py    # 构建方式识别
    ├── build_driver.py           # 自动构建与测试执行
    ├── analyze_ci_log.py         # 失败日志提取与原因分类
    └── result_writer.py          # 结果写入 suc/fail/other 三个表
