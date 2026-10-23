# 形态决策台

这是“形态决策台”的独立部署包，包含可浏览的静态页面、历史形态数据、只读实时刷新程序、测试与云服务器部署配置。它用于观察 Polymarket 电竞市场的分钟级价格路径，并对照历史相似形态；**不包含钱包私钥，也不会下单、撤单或自动交易**。

## 当前内容

- 形态决策台：当前盘面、历史回放、未来赛程与候选形态证据图。
- 形态总库、形态速查、新版形态审计、形态分类、频率排行和粒度对照。
- 英雄联盟与 CS2 的逐局、整局 1 分钟 / 5 分钟 / 10 分钟价格路径。
- 预计算训练库：12,852 条训练记录、297 场去重比赛、318 个比赛入口；数据生成时间见页面顶部的数据体检。
- Polymarket 公共 Gamma / CLOB 行情的只读刷新程序。
- 可选的 Stake 赛前赛程与十进制赔率参考；十进制赔率不会和 Polymarket 0–1 价格混画。

## 页面入口

本地直接打开 `site/index.html`，或启动静态服务器：

```bash
python3 -m http.server 8765 --directory site
```

然后访问 <http://127.0.0.1:8765/>。主要页面：

- `site/index.html`：形态决策台。
- `site/pattern_structure_audit_2026-10-22.html`：新版形态审计。
- `site/pattern_library.html`：形态总库与分类图表。
- `site/pattern_playbook.html`：形态速查。
- `site/pattern_taxonomy.html`：形态分类。
- `site/pattern_frequency_index.html`：形态频率排行。
- `site/pattern_shapes_granularity.html`：1 分钟 / 10 分钟粒度对照。

## 工作方式

```text
Polymarket 公共行情
        ↓
update_pattern_decision_live.py
        ↓
历史训练库 + 当前比赛路径 + 候选形态
        ↓
自包含 site/index.html
```

`data/pattern_decision_data.json` 是随仓库发布的预计算历史库。云端定时任务读取它，补充当前比赛和未来赛程后重绘 `site/index.html`，因此不需要重新下载全部历史快照即可运行。

手动刷新：

```bash
python3 tools/update_pattern_decision_live.py \
  --base-data data/pattern_decision_data.json \
  --template tools/pattern_decision_workspace.template.html \
  --watchlist config/market_watchlist.json \
  --out site/index.html \
  --json-out state/latest.json
```

刷新程序只调用公开接口。API 为空、网络失败或页面构建失败时，程序会在覆盖文件前停止，已有页面继续保留。

## 云服务器部署

推荐 Ubuntu 22.04 / 24.04，Python 3.10+，Nginx。以下路径与仓库内的示例配置一致：

- 程序：`/opt/pattern-decision`
- 网站：`/var/www/pattern-decision`
- 刷新状态：`/var/lib/pattern-decision`

先让服务器具备访问这个私有仓库的权限（SSH deploy key 或 GitHub 已授权账号），再执行：

```bash
sudo apt-get update
sudo apt-get install -y python3 nginx rsync
sudo git clone YOUR_GITHUB_PRIVATE_REPO_URL /opt/pattern-decision
cd /opt/pattern-decision
sudo bash scripts/install.sh
```

安装脚本会完成以下动作：

1. 将 `site/` 下静态页面部署到 `/var/www/pattern-decision`。
2. 安装 Nginx 站点配置，并保留可恢复的原 `default` 配置。
3. 安装每 5 分钟运行一次的 `pattern-decision-live.service` 与 `.timer`。
4. 首次刷新成功后将新页面写入网站目录；失败不会覆盖已部署页面。

检查服务：

```bash
systemctl status pattern-decision-live.timer
systemctl list-timers pattern-decision-live.timer
journalctl -u pattern-decision-live.service -n 100 --no-pager
nginx -t
```

部署完成后访问 `http://YOUR_SERVER_IP/` 或 `http://YOUR_SERVER_IP/intel/`。如需 HTTPS，请在 Nginx 上配置自己的域名和证书；仓库不包含任何真实服务器地址或证书。

## 测试

运行依赖很少：

```bash
python3 -m pip install -r requirements-dev.txt
make test
```

也可直接执行：

```bash
PYTHONPATH=tools pytest -q tests
```

当前测试覆盖决策台页面契约、实时刷新解析逻辑、形态审计双线图与数据隔离。缺少原始快照时，两个“从原始 JSONL 重建训练库”的测试会标记为跳过；预计算训练库的完整性另有测试覆盖。

## 历史原始快照

完整原始快照约为 634 MB，未放入本仓库。它只影响从 `docs/data/snapshots/` 重新计算全部历史训练样本，不影响：

- 直接浏览当前静态页面；
- 使用预计算 `data/pattern_decision_data.json`；
- 定时刷新 Polymarket 当前比赛与未来赛程；
- 运行决策台、总库、速查、审计等页面。

如确需重建历史训练库，应把原始快照按原目录结构放到 `docs/data/snapshots/`，再运行 `tools/build_pattern_decision_site.py` 与相关生成器。原始快照属于可选、私密的大数据资产，不应和网页代码一起公开分享。

## 数据边界

- 当前版本是 P1 观察版：相似度不是校准后的胜率，形态候选不构成收益承诺或交易建议。
- 候选路径会随着新的分钟价格到达而变化；赛前样本不足时会明确显示数据不足。
- 英雄联盟与 CS2、逐局与整局、1 分钟 / 5 分钟 / 10 分钟样本严格分层，不互相混算。
- 项目不包含钱包私钥、助记词、API 密钥或真实订单执行能力。
- 实时数据来自公开接口；接口暂停、限流或数据缺口会在诊断 JSON 和页面状态中体现。

## 目录

```text
data/                     预计算训练库
docs/                     方法、结构审计与部署相关说明
reports/                  历史形态摘要源文件
site/                     可直接部署的静态页面
tools/                    页面生成与只读实时刷新程序
tests/                    回归测试
config/                   市场扫描白名单与赛事分级
deploy/                   Nginx 与 systemd 示例配置
scripts/install.sh        云服务器安装脚本
```

## 使用边界

本仓库按私有研究项目交付。未经项目所有者许可，不将页面、数据或分析内容公开转载、再分发或用于对外商业服务。
