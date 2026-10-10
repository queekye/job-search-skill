# 公共公司资源分发

公司资源属于公开方法，通过静态目录分发。个人 WebDAV 同步全部账号的数据库业务数据和原始附件，不同步公司包、待审包、接受收据或版本锁。换到新电脑后，安装内置资源、从公共目录下载，或离线导入所需版本。完整离线换机备份的范围见 [migration.md](migration.md)。

## 可托管文件

发布构建生成 `dist/catalog/` 和 `company-catalog-<版本>.zip`。目录内只有公开 HTML/CSS/JS、`index.json`、许可证、`.nojekyll` 和 `packages/<id>-<version>.zip`，不读取个人数据目录。可整体托管到 GitHub Pages、Cloudflare Pages 或其他 HTTPS 静态站点，无需数据库、服务器 API、登录或访问 Token。

生产目录为 [job-search-company-resources.pages.dev](https://job-search-company-resources.pages.dev/)，源码和版本由 [queekye/job-search-skill](https://github.com/queekye/job-search-skill) 管理。

在网页“设置与跟进 → 资源与共建入口”填写 `https://job-search-company-resources.pages.dev/index.json`。进入“公司资源”，点击“刷新公共目录”，再选择“下载并审阅”。下载仅保存待审 ZIP；阅读显示的完整文本和脚本后，点击“确认已审阅并启用”。安装不会执行脚本，网站操作仍遵循各包的能力范围。

目录地址未配置或用户没有点击刷新时，不自动访问远端。目录连接失败不影响内置资源、已接受资源或离线导入。网页与本地服务仍在本机运行；公共站点只负责下载公司方法。

## GitHub 版本与 Cloudflare 自动部署

在 GitHub 管理公开公司包的 `manifest.json`、方法和脚本；内容修改时增加包版本，通过 Git 提交和标签保留历史。Cloudflare Pages 选择原生 Git 集成并连接该仓库，开启生产分支自动部署。推送更新后由 Pages 构建并发布静态目录；其他分支可生成预览部署。[Cloudflare Git 集成](https://developers.cloudflare.com/pages/configuration/git-integration/)

构建配置：

| 项目 | 值 |
|---|---|
| Framework preset | None |
| Root directory | 仓库根目录，留空 |
| Build command | `python scripts/check_source.py && python scripts/build_catalog.py --output dist/catalog` |
| Build output directory | `dist/catalog` |
| Production branch | `main` |

构建仅使用 Python 标准库，读取公开公司资源、目录页面、样式和许可证；不实例化个人数据服务，不读取 `JOB_SEARCH_DATA_DIR`，无需上传个人档案或设置 WebDAV/OTP 凭据。输出目录是部署资产的唯一入口。[Cloudflare 构建配置](https://developers.cloudflare.com/pages/configuration/build-configuration/)

本地也可运行同一条命令。`--output` 要求新目录或空目录，避免把额外文件带进部署；重复本地构建时使用另一空目录。当前 15 个公司包生成 21 个公开文件，包含 15 个版本 ZIP。无参数运行 `python scripts/build_catalog.py` 仍只更新本地 `web/catalog.js`，保持既有发布构建兼容。

首次发布后，另一次 `main` 推送用于核验原生自动构建和部署。线上索引和选定 ZIP 核对 HTTP 200、字节数与 SHA-256；本机使用合成档案验收“刷新公共目录 → 下载并审阅 → 确认已审阅并启用”，不读取或发布真实求职资料。启用状态留在本机。

## 免费静态托管选择

2026-10-10 核对的官方规则：

| 平台 | 与本项目相关的免费额度 | 使用方式 |
|---|---|---|
| Cloudflare Pages | 纯静态请求免费且不限请求数；免费计划每月 500 次构建、20,000 文件；单文件最多 25 MiB | 整体部署 `dist/catalog/`，不添加 Functions |
| GitHub Pages | GitHub Free 可从公开仓库提供 Pages；网站最多 1 GB；每月 100 GB 为软带宽限制 | 提供同一个静态目录，保留 `.nojekyll` |

额度来源：[Cloudflare 静态请求计费](https://developers.cloudflare.com/pages/functions/pricing/)、[Cloudflare Pages 限额](https://developers.cloudflare.com/pages/platform/limits/)、[GitHub Pages 限额](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)。平台额度可能调整，发布前重新核对。

当前公司包的产品限额是每个 ZIP 2 MiB，适合上述静态托管。本项目采用 GitHub 管理源码和资源版本、Cloudflare Pages 托管静态目录；无需额外引入对象存储、GitHub API、Workers 或付费服务。

本地下载器要求索引和 ZIP 直接返回文件，不跟随重定向。GitHub Releases 的跳转下载地址不作为本版自动下载入口；仍可自行下载 ZIP 后离线导入。将同一套版本 ZIP 和索引放在同源静态目录，可以避免下载跳转和凭据处理。

## 索引契约 1

`index.json`：

```json
{
  "schema_version": 1,
  "packages": [
    {
      "id": "synthetic-company",
      "name": "示例公司",
      "version": "1.2.3",
      "description": "公开招聘方法",
      "origin": "https://company.example.com",
      "capabilities": {},
      "verification": {},
      "download": {
        "path": "packages/synthetic-company-1.2.3.zip",
        "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        "bytes": 1024
      }
    }
  ]
}
```

元数据沿用公司包契约，`download.path` 固定为 `packages/<id>-<version>.zip`，相对于索引所在目录。索引最多 1 MiB、256 个版本；同一 ID/版本不可重复。ZIP 继续遵守 [company-pack.md](company-pack.md) 的大小、成员路径、编码和资源哈希校验。修改内容时增加版本并重建索引；目录内 ZIP 的 SHA-256 和字节数必须与索引一致。

URL 只支持不带账号、查询或片段的公网 HTTPS；不使用本机代理、浏览器 Cookie 或 WebDAV 密码。HTTP `127.0.0.1` 仅供合成测试。连接固定已检查的公网 IP，TLS 校验原域名；不允许索引或 ZIP 跳转到另一地址。下载使用公开 GET，不上传个人档案、申请记录或附件。

## 本地 API

- `resource.refresh {url?}`：使用指定地址或当前账号的 `settings.registry_url`，返回 `{configured,url,items}`。未配置时返回 `configured:false` 和空列表；读取目录不修改数据库或启用资源。Codex 可读取此元数据。
- `resource.download {id,version,sha256,url?}`：仅本人操作，重新读取索引并匹配选定版本和 ZIP 哈希，再下载并校验完整包。目录改变时停止，不静默换成其他版本。返回既有完整审阅响应、`candidate_id`、`registry_url` 和 `download`；MCP 不新增下载写入工具。
- `resource.approve {candidate_id}`：继续使用既有本人审阅入口。接受类型仍为 `community_import/user_reviewed`，不会把公共目录的验证声明变成官方现场核验。

SHA-256 证明下载内容与所选目录一致；它不能替代来源判断、代码审阅或招聘网站现场核验。待审包和接受状态保存在各自电脑，不通过个人 WebDAV 自动传播。
