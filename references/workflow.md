# 运行与数据契约

Python 3.10+，仅标准库。命令中的相对路径均相对调用目录；引号包住含空格路径。在 Windows PowerShell 不可用时可用 cmd.exe，不改变系统设置。

## 1. 扫描

创建独立工作目录下的 config.json：

```json
{
  "projects": [
    {"id":"app", "name":"我的项目", "path":"/absolute/project/path", "documents":["docs/requirements.md"], "scope_note":"本轮只评估此代码库"}
  ],
  "installed_skill_roots": ["/absolute/installed/skills"],
  "additional_installed_skills": []
}
```

documents、scope_note 和两个 installed 字段可省略。追加已知插件技能到 additional_installed_skills，字段 name/description/status。不填写假路径；仅需求阶段直接内联输出推荐，不调用依赖文件证据的 report 命令。

```text
python <skill>/scripts/radar.py scan --config config.json --out profile.json
```

扫描只读根目录白名单文档、浅层依赖文件与源码文件名；显式 documents 用于补充需求。每项目最多 2,500 个匹配文件、深度 4、12 份文档，每份最多 32 KB。源码文件名最多输出 500 个。截断必须人工识别，必要时缩小范围扫描。默认不读取源码正文。根 README 可能仍含业务信息；脱敏是尽力而为，profile.json 必须留在本地，不当作可公开材料。

已安装技能按三种项目目录及显式全局根盘点，不保证扫描到所有插件缓存；还要结合宿主会话技能清单。跳过符号链接/目录联接，并明确 linked-unread。扫描可能遗漏包管理器或嵌套 monorepo；不得推定缺失等于不存在。

## 2. 搜索与核验

优先复用连接器。它的账号 ID、认证信息属于当前会话，不写入可分发 Skill。GitHub 仓库搜索用于找集合，目录/树和代码搜索用于找集合内技能。可加入已知官方技能源并说明来源，不只依赖热度排序。

独立 CLI 备用方式：

```json
{"queries":["agent skills python testing stars:>100", "agent skills evaluation"]}
```

```text
python <skill>/scripts/radar.py search --plan search-plan.json --out discovery.json
python <skill>/scripts/radar.py inspect --repo owner/repo --path skills/example/SKILL.md --out candidate.json
```

只发送 search-plan 的通用 query；每次至多 8 条、每条前 10 个仓库。搜索未覆盖的部分要写在报告。脚本支持环境变量 GITHUB_TOKEN / GH_TOKEN，但不要求在聊天提供密钥；无令牌使用公开 API，限流则停止并记录，优先换用可用连接器。

inspect 先固定默认分支 commit，再读取文件。连接器路径等价：读取仓库元信息 → branches/{default_branch} 获取 commit → 固定 commit 读取树/具体文件。不要把 tree SHA 或 blob SHA 写成 commit。

candidates.json 是数组，每项包含：id（repo:path）、name、repo、path、commit、source_url（固定 commit GitHub blob 链接）、checked_at（ISO 时间）、repo_stars（可为空）、repo_pushed_at、repo_archived、license、sha256（正文 SHA256）、text、verified（正文是否核验）、verification_scope。无法固定版本时标未核验并降级，不捏造 commit。原始候选正文存工作目录，默认不捆绑到开源包。

## 3. 宿主 Agent 评估

assessment.json 格式：

```json
{
  "summary":"本轮主要发现",
  "coverage":"搜索方式、范围、时间、失败或未核实项",
  "decisions":[{
    "project_id":"app",
    "candidate_id":"owner/repo:skills/example/SKILL.md",
    "decision":"conditional",
    "compatibility":"unknown",
    "already_covered":false,
    "project_evidence":["README.md"],
    "skill_evidence":"从已取回正文复制的短句",
    "reason":"对应哪项需求，有何新增价值",
    "constraints":"运行依赖、适配范围和未验证项",
    "overlap":"与当前已装技能的比较",
    "scores":{"fit":4,"compatibility":3,"incremental":4,"maintenance":3,"popularity":2},
    "score_reasons":{"fit":"明确需求依据","compatibility":"具体待核实条件","incremental":"缺口说明","maintenance":"维护证据及限制","popularity":"实际仓库星数档位"}
  }]
}
```

示例只是字段说明，不得原样当作评估结果。所有项目至少有一个候选评估；没有适用候选时记录被排除候选与理由。脚本校验项目/候选 ID、证据路径、技能引用、逐项评分及推荐前置条件。

```text
python <skill>/scripts/radar.py report --profile profile.json --candidates candidates.json --assessment assessment.json --out report
```

输出 report.md、report.html、ranked.json。HTML 为本地静态报告，无外部脚本或请求，不需要起服务。HTML 给简版，Markdown 保留逐项评分。中文标题可由报告上下文传入；v0.1 不自动安装、不写持久化偏好、不定时运行。

## 验证

```text
python -m unittest discover -s <skill>/tests -v
```

测试使用临时文件和模拟 GitHub 响应，覆盖数据边界、固定版本、失败报告、错误推荐拦截和 HTML 转义。真实 GitHub 核验另记录，不把模拟网络通过声称为公网可用。
